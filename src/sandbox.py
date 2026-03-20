import io
import tarfile

import docker

from .config import settings


class SandboxManager:
    def __init__(self):
        self.client = docker.from_env()

    def create(self) -> str:
        container = self.client.containers.run(
            image=settings.sandbox_image,
            command="tail -f /dev/null",
            network_mode="none",
            mem_limit=settings.sandbox_memory,
            nano_cpus=int(settings.sandbox_cpus * 1e9),
            detach=True,
        )
        return container.id  # type: ignore[union-attr]

    def exec(self, container_id: str, command: str, timeout: int = 30) -> dict:
        container = self.client.containers.get(container_id)
        wrapped_command = f"timeout {timeout} {command}"
        exit_code, output = container.exec_run(wrapped_command, demux=True)
        stdout_bytes, stderr_bytes = output if output else (None, None)
        return {
            "stdout": stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else "",
            "stderr": stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else "",
            "return_code": exit_code,
        }

    def copy_to(self, container_id: str, local_path: str, container_path: str):
        container = self.client.containers.get(container_id)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            tar.add(local_path, arcname=container_path.lstrip("/"))
        buf.seek(0)
        container.put_archive("/", buf)

    def copy_from(self, container_id: str, container_path: str) -> bytes:
        container = self.client.containers.get(container_id)
        stream, _ = container.get_archive(container_path)
        buf = io.BytesIO()
        for chunk in stream:
            buf.write(chunk)
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r") as tar:
            member = tar.getmembers()[0]
            f = tar.extractfile(member)
            if f is None:
                return b""
            return f.read()

    def list_files(self, container_id: str, path: str = "/workspace") -> list[str]:
        result = self.exec(container_id, f"ls -la {path}")
        return result["stdout"].splitlines()

    def destroy(self, container_id: str):
        container = self.client.containers.get(container_id)
        container.remove(force=True)
