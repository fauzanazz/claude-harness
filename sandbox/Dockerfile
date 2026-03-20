FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ripgrep \
    jq \
    curl \
    git \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

RUN mkdir -p /workspace/uploads
