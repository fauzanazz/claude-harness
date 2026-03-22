use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use serde::{Deserialize, Serialize};
use std::path::Path;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::process::Command;
#[cfg(target_os = "linux")]
use tokio_vsock::VsockListener;

const VSOCK_PORT: u32 = 5000;
const TCP_FALLBACK_PORT: u16 = 5000;
const MAX_MESSAGE_SIZE: u32 = 64 * 1024 * 1024; // 64MB

// --- Protocol types ---

#[derive(Debug, Deserialize)]
struct Request {
    id: u64,
    method: String,
    #[serde(default)]
    params: serde_json::Value,
}

#[derive(Debug, Serialize)]
struct Response {
    id: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

impl Response {
    fn ok(id: u64, result: serde_json::Value) -> Self {
        Self {
            id,
            result: Some(result),
            error: None,
        }
    }

    fn err(id: u64, msg: impl Into<String>) -> Self {
        Self {
            id,
            result: None,
            error: Some(msg.into()),
        }
    }
}

// --- Protocol framing: 4-byte big-endian length + JSON ---

async fn read_message<R: AsyncReadExt + Unpin>(reader: &mut R) -> std::io::Result<Option<Request>> {
    let len = match reader.read_u32().await {
        Ok(n) => n,
        Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => return Ok(None),
        Err(e) => return Err(e),
    };

    if len > MAX_MESSAGE_SIZE {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("message too large: {len} bytes"),
        ));
    }

    let mut buf = vec![0u8; len as usize];
    reader.read_exact(&mut buf).await?;

    serde_json::from_slice(&buf)
        .map(Some)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))
}

async fn write_message<W: AsyncWriteExt + Unpin>(
    writer: &mut W,
    response: &Response,
) -> std::io::Result<()> {
    let payload = serde_json::to_vec(response)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    writer.write_u32(payload.len() as u32).await?;
    writer.write_all(&payload).await?;
    writer.flush().await
}

// --- Handlers ---

async fn handle_ping(id: u64) -> Response {
    Response::ok(id, serde_json::json!({ "status": "ok" }))
}

async fn handle_exec(id: u64, params: &serde_json::Value) -> Response {
    let command = match params.get("command").and_then(|v| v.as_str()) {
        Some(c) => c,
        None => return Response::err(id, "missing 'command' parameter"),
    };

    let timeout_secs = params
        .get("timeout")
        .and_then(|v| v.as_u64())
        .unwrap_or(30);

    let wrapped = if cfg!(target_os = "linux") {
        format!("timeout {timeout_secs} sh -c {}", shell_escape(command))
    } else {
        command.to_string()
    };

    let output = Command::new("sh")
        .arg("-c")
        .arg(&wrapped)
        .output()
        .await;

    match output {
        Ok(out) => Response::ok(
            id,
            serde_json::json!({
                "stdout": String::from_utf8_lossy(&out.stdout),
                "stderr": String::from_utf8_lossy(&out.stderr),
                "return_code": out.status.code().unwrap_or(-1),
            }),
        ),
        Err(e) => Response::err(id, format!("exec failed: {e}")),
    }
}

async fn handle_read_file(id: u64, params: &serde_json::Value) -> Response {
    let path = match params.get("path").and_then(|v| v.as_str()) {
        Some(p) => p,
        None => return Response::err(id, "missing 'path' parameter"),
    };

    match tokio::fs::read(path).await {
        Ok(data) => Response::ok(
            id,
            serde_json::json!({
                "content": BASE64.encode(&data),
                "size": data.len(),
            }),
        ),
        Err(e) => Response::err(id, format!("read_file failed: {e}")),
    }
}

async fn handle_write_file(id: u64, params: &serde_json::Value) -> Response {
    let path = match params.get("path").and_then(|v| v.as_str()) {
        Some(p) => p,
        None => return Response::err(id, "missing 'path' parameter"),
    };
    let content_b64 = match params.get("content").and_then(|v| v.as_str()) {
        Some(c) => c,
        None => return Response::err(id, "missing 'content' parameter"),
    };

    let data = match BASE64.decode(content_b64) {
        Ok(d) => d,
        Err(e) => return Response::err(id, format!("invalid base64: {e}")),
    };

    // Ensure parent directory exists
    if let Some(parent) = Path::new(path).parent() {
        if let Err(e) = tokio::fs::create_dir_all(parent).await {
            return Response::err(id, format!("mkdir failed: {e}"));
        }
    }

    match tokio::fs::write(path, &data).await {
        Ok(()) => Response::ok(
            id,
            serde_json::json!({
                "bytes_written": data.len(),
            }),
        ),
        Err(e) => Response::err(id, format!("write_file failed: {e}")),
    }
}

async fn handle_list_files(id: u64, params: &serde_json::Value) -> Response {
    let path = params
        .get("path")
        .and_then(|v| v.as_str())
        .unwrap_or("/workspace");

    let mut entries = Vec::new();
    let mut dir = match tokio::fs::read_dir(path).await {
        Ok(d) => d,
        Err(e) => return Response::err(id, format!("list_files failed: {e}")),
    };

    while let Ok(Some(entry)) = dir.next_entry().await {
        let name = entry.file_name().to_string_lossy().into_owned();
        let is_dir = entry
            .file_type()
            .await
            .map(|ft| ft.is_dir())
            .unwrap_or(false);
        entries.push(serde_json::json!({
            "name": name,
            "is_dir": is_dir,
        }));
    }

    Response::ok(id, serde_json::json!({ "entries": entries }))
}

async fn dispatch(req: Request) -> Response {
    match req.method.as_str() {
        "ping" => handle_ping(req.id).await,
        "exec" => handle_exec(req.id, &req.params).await,
        "read_file" => handle_read_file(req.id, &req.params).await,
        "write_file" => handle_write_file(req.id, &req.params).await,
        "list_files" => handle_list_files(req.id, &req.params).await,
        _ => Response::err(req.id, format!("unknown method: {}", req.method)),
    }
}

// --- Helpers ---

fn shell_escape(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

// --- Connection handler ---

async fn handle_connection<S: AsyncReadExt + AsyncWriteExt + Unpin>(stream: S) {
    let (mut reader, mut writer) = tokio::io::split(stream);

    loop {
        match read_message(&mut reader).await {
            Ok(Some(req)) => {
                let resp = dispatch(req).await;
                if let Err(e) = write_message(&mut writer, &resp).await {
                    eprintln!("guest-agent: write error: {e}");
                    break;
                }
            }
            Ok(None) => break,
            Err(e) => {
                eprintln!("guest-agent: read error: {e}");
                break;
            }
        }
    }

    eprintln!("guest-agent: connection closed");
}

// --- Main ---

#[cfg(target_os = "linux")]
#[tokio::main(flavor = "current_thread")]
async fn main() -> std::io::Result<()> {
    eprintln!("guest-agent: listening on vsock port {VSOCK_PORT}");

    let listener = VsockListener::bind(libc::VMADDR_CID_ANY, VSOCK_PORT)?;

    loop {
        let (stream, addr) = listener.accept().await?;
        eprintln!("guest-agent: connection from cid={}", addr.cid());
        tokio::spawn(handle_connection(stream));
    }
}

#[cfg(not(target_os = "linux"))]
#[tokio::main(flavor = "current_thread")]
async fn main() -> std::io::Result<()> {
    use tokio::net::TcpListener;

    eprintln!(
        "guest-agent: TCP fallback mode (non-Linux), listening on 0.0.0.0:{TCP_FALLBACK_PORT}"
    );

    let listener = TcpListener::bind(("0.0.0.0", TCP_FALLBACK_PORT)).await?;

    loop {
        let (stream, addr) = listener.accept().await?;
        eprintln!("guest-agent: connection from {addr}");
        tokio::spawn(handle_connection(stream));
    }
}

// --- Tests ---

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_shell_escape_simple() {
        assert_eq!(shell_escape("echo hello"), "'echo hello'");
    }

    #[test]
    fn test_shell_escape_with_quotes() {
        assert_eq!(shell_escape("echo 'hi'"), "'echo '\\''hi'\\'''");
    }

    #[test]
    fn test_response_ok_serialization() {
        let resp = Response::ok(1, serde_json::json!({"status": "ok"}));
        let json = serde_json::to_value(&resp).unwrap();
        assert_eq!(json["id"], 1);
        assert_eq!(json["result"]["status"], "ok");
        assert!(json.get("error").is_none());
    }

    #[test]
    fn test_response_err_serialization() {
        let resp = Response::err(2, "something broke");
        let json = serde_json::to_value(&resp).unwrap();
        assert_eq!(json["id"], 2);
        assert_eq!(json["error"], "something broke");
        assert!(json.get("result").is_none());
    }

    #[test]
    fn test_request_deserialization() {
        let json = r#"{"id": 1, "method": "ping"}"#;
        let req: Request = serde_json::from_str(json).unwrap();
        assert_eq!(req.id, 1);
        assert_eq!(req.method, "ping");
        assert!(req.params.is_null());
    }

    #[test]
    fn test_request_with_params() {
        let json = r#"{"id": 5, "method": "exec", "params": {"command": "ls"}}"#;
        let req: Request = serde_json::from_str(json).unwrap();
        assert_eq!(req.id, 5);
        assert_eq!(req.method, "exec");
        assert_eq!(req.params["command"], "ls");
    }

    #[tokio::test]
    async fn test_handle_ping() {
        let resp = handle_ping(42).await;
        assert!(resp.error.is_none());
        assert_eq!(resp.result.unwrap()["status"], "ok");
    }

    #[tokio::test]
    async fn test_handle_exec_missing_command() {
        let resp = handle_exec(1, &serde_json::json!({})).await;
        assert!(resp.error.is_some());
        assert!(resp.error.unwrap().contains("missing"));
    }

    #[tokio::test]
    async fn test_handle_exec_echo() {
        let resp = handle_exec(1, &serde_json::json!({"command": "echo hello"})).await;
        let result = resp.result.unwrap();
        assert_eq!(result["stdout"].as_str().unwrap().trim(), "hello");
        assert_eq!(result["return_code"], 0);
    }

    #[tokio::test]
    async fn test_handle_read_file_missing_path() {
        let resp = handle_read_file(1, &serde_json::json!({})).await;
        assert!(resp.error.is_some());
    }

    #[tokio::test]
    async fn test_handle_read_file_nonexistent() {
        let resp =
            handle_read_file(1, &serde_json::json!({"path": "/tmp/nonexistent_guest_agent_test"}))
                .await;
        assert!(resp.error.is_some());
    }

    #[tokio::test]
    async fn test_handle_write_and_read_file() {
        let test_path = "/tmp/guest_agent_test_write_read.txt";
        let content = BASE64.encode(b"hello from test");

        let write_resp =
            handle_write_file(1, &serde_json::json!({"path": test_path, "content": content}))
                .await;
        assert!(write_resp.error.is_none());
        assert_eq!(write_resp.result.unwrap()["bytes_written"], 15);

        let read_resp = handle_read_file(2, &serde_json::json!({"path": test_path})).await;
        assert!(read_resp.error.is_none());
        let result = read_resp.result.unwrap();
        let decoded = BASE64.decode(result["content"].as_str().unwrap()).unwrap();
        assert_eq!(decoded, b"hello from test");

        // Cleanup
        let _ = tokio::fs::remove_file(test_path).await;
    }

    #[tokio::test]
    async fn test_handle_write_file_invalid_base64() {
        let resp = handle_write_file(
            1,
            &serde_json::json!({"path": "/tmp/test", "content": "not!valid!base64!!!"}),
        )
        .await;
        assert!(resp.error.is_some());
        assert!(resp.error.unwrap().contains("base64"));
    }

    #[tokio::test]
    async fn test_handle_list_files() {
        let resp = handle_list_files(1, &serde_json::json!({"path": "/tmp"})).await;
        assert!(resp.error.is_none());
        let entries = resp.result.unwrap()["entries"].as_array().unwrap().clone();
        assert!(!entries.is_empty());
    }

    #[tokio::test]
    async fn test_handle_list_files_default_path() {
        // /workspace may not exist on host, but test the error path
        let resp = handle_list_files(1, &serde_json::json!({})).await;
        // Either succeeds or gives a proper error — no panic
        assert!(resp.error.is_some() || resp.result.is_some());
    }

    #[tokio::test]
    async fn test_dispatch_unknown_method() {
        let req = Request {
            id: 99,
            method: "unknown_method".to_string(),
            params: serde_json::Value::Null,
        };
        let resp = dispatch(req).await;
        assert!(resp.error.is_some());
        assert!(resp.error.unwrap().contains("unknown method"));
    }

    #[tokio::test]
    async fn test_protocol_roundtrip() {
        // Write a length-prefixed request
        let req_json = serde_json::json!({"id": 7, "method": "ping"});
        let payload = serde_json::to_vec(&req_json).unwrap();
        let mut buf = Vec::new();
        buf.extend_from_slice(&(payload.len() as u32).to_be_bytes());
        buf.extend_from_slice(&payload);

        // Read it back using our framing
        let mut reader = &buf[..];
        let parsed = read_message(&mut reader).await.unwrap().unwrap();
        assert_eq!(parsed.id, 7);
        assert_eq!(parsed.method, "ping");
    }
}
