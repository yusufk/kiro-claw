FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl python3 python3-pip python3-venv ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*

# Install kiro-cli (Linux, stable channel) then move to shared PATH.
# The install script resolves the latest stable release; it has no exact-version
# pin, so we bust the layer cache with KIRO_CLI_CACHEBUST to force a fresh pull on
# deliberate rebuilds. Bump the value (e.g. a date) to re-fetch current stable.
# Validated against kiro-cli 2.34.x (V3) on 2026-10-25 for the FRIDAY V3 upgrade.
ARG KIRO_CLI_CACHEBUST=2026-10-25
RUN curl -fsSL https://cli.kiro.dev/install | bash \
    && mv /root/.local/bin/kiro-cli* /usr/local/bin/ \
    && kiro-cli --version

# Install uv (for uvx/mcp-proxy) globally
ENV UV_INSTALL_DIR=/usr/local/bin
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Pre-install MCP tools so uvx doesn't download at runtime
ENV UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin
RUN uv tool install mcp-proxy \
    && uv tool install mcp-server-fetch

# Pre-cache playwright MCP for npx
RUN npx -y @playwright/mcp@latest --help 2>/dev/null || true

# Memory provider: "memory" (JSON graph, works everywhere) or "mempalace" (semantic search, needs AVX CPU)
ARG MEMORY_PROVIDER=memory
RUN if [ "$MEMORY_PROVIDER" = "mempalace" ]; then \
      pip install --break-system-packages mempalace; \
    else \
      npx -y @modelcontextprotocol/server-memory --help 2>/dev/null || true; \
    fi

# Create workspace dirs
RUN mkdir -p /workspace/brain /workspace/scratch /workspace/ipc /home/node/.kiro \
    && chown -R node:node /workspace /home/node /opt/uv-tools

# Install IPC tools (proactive messaging + task scheduling)
COPY --chown=node:node tools/ /usr/local/bin/

USER node
WORKDIR /workspace

COPY --chown=node:node entrypoint.py /entrypoint.py

ENTRYPOINT ["python3", "/entrypoint.py"]
