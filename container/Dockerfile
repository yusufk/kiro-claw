FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl python3 python3-pip python3-venv ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*

# Install kiro-cli (Linux) then move to shared PATH
RUN curl -fsSL https://cli.kiro.dev/install | bash \
    && mv /root/.local/bin/kiro-cli* /usr/local/bin/

# Install uv (for uvx/mcp-proxy) globally
ENV UV_INSTALL_DIR=/usr/local/bin
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Pre-install MCP tools so uvx doesn't download at runtime
ENV UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin
RUN uv tool install mcp-proxy \
    && uv tool install mcp-server-fetch

# Pre-cache playwright MCP for npx
RUN npx -y @playwright/mcp@latest --help 2>/dev/null || true

# Create workspace dirs
RUN mkdir -p /workspace/brain /workspace/scratch /workspace/ipc /home/node/.kiro \
    && chown -R node:node /workspace /home/node /opt/uv-tools

# Install IPC tools (proactive messaging + task scheduling)
COPY --chown=node:node tools/ /usr/local/bin/

USER node
WORKDIR /workspace

COPY --chown=node:node entrypoint.py /entrypoint.py

ENTRYPOINT ["python3", "/entrypoint.py"]
