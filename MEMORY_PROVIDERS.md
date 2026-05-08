# Memory Provider Configuration

Kiro-Claw supports pluggable memory systems. Set `MEMORY_PROVIDER` in `.env` and configure the matching MCP server in `data/agent.json`.

## Option A: `memory` (default)

JSON knowledge graph via `@modelcontextprotocol/server-memory`. Works on any CPU, no native dependencies.

```json
"memory": {
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-memory"],
  "env": {"MEMORY_FILE_PATH": "/workspace/brain/memory.json"}
}
```

Tools: `create_entities`, `add_observations`, `search_nodes`, `read_graph`, `open_nodes`, `delete_entities`

## Option B: `mempalace`

Semantic search via ChromaDB embeddings. Requires AVX-capable CPU (most modern x86 — NOT AMD Turion II or similar pre-2011 CPUs).

```json
"mempalace": {
  "command": "python3",
  "args": ["-m", "mempalace.mcp_server"]
}
```

Tools: `mempalace_search`, `mempalace_add_drawer`, `mempalace_kg_add`, `mempalace_kg_query`, etc.

**Build**: Set `MEMORY_PROVIDER=mempalace` in `.env` before running `deploy-cappucino.sh`.

## Switching

1. Update `MEMORY_PROVIDER` in `.env`
2. Update `mcpServers` in `data/agent.json` (swap the memory block)
3. Update `tools` and `allowedTools` arrays to reference the correct `@memory` or `@mempalace`
4. Rebuild container: `docker build --build-arg MEMORY_PROVIDER=mempalace -t kiro-claw-agent:latest container/`
5. Restart: `./kiro-claw.sh restart`
