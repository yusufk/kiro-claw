FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl python3 ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*

# Install kiro-cli (Linux) then move to shared PATH
RUN curl -fsSL https://cli.kiro.dev/install | bash \
    && mv /root/.local/bin/kiro-cli* /usr/local/bin/

# Create workspace dirs
RUN mkdir -p /workspace/brain /workspace/scratch /home/node/.kiro \
    && chown -R node:node /workspace /home/node

USER node
WORKDIR /workspace

COPY --chown=node:node entrypoint.py /entrypoint.py

ENTRYPOINT ["python3", "/entrypoint.py"]
