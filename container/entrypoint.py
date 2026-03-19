#!/usr/bin/env python3
"""Container entrypoint — long-running loop, reads JSON lines from stdin."""

import json
import os
import subprocess
import sys

OUTPUT_START = "---KIROCLAW_OUTPUT_START---"
OUTPUT_END = "---KIROCLAW_OUTPUT_END---"

# Map host paths to container paths in agent configs
PATH_REWRITES = {
    "/Users/": "/workspace/brain/",  # catch-all for Mac user paths pointing to brain content
}


def _patch_agent_paths():
    """Rewrite host-specific paths in agent configs to container paths."""
    agents_dir = os.path.expanduser("~/.kiro/agents")
    if not os.path.isdir(agents_dir):
        return
    for fname in os.listdir(agents_dir):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(agents_dir, fname)
        try:
            raw = open(fpath).read()
            patched = raw
            for server in json.loads(raw).get("mcpServers", {}).values():
                for key, val in server.get("env", {}).items():
                    if isinstance(val, str) and val.startswith("/Users/"):
                        # Rewrite to brain mount if the file exists there
                        basename = os.path.basename(val)
                        container_path = f"/workspace/brain/{basename}"
                        patched = patched.replace(val, container_path)
            if patched != raw:
                open(fpath, "w").write(patched)
        except Exception:
            pass


def write_output(status, result=None, error=None):
    msg = json.dumps({"status": status, "result": result, "error": error})
    print(f"{OUTPUT_START}\n{msg}\n{OUTPUT_END}", flush=True)


def handle(data):
    prompt = data.get("prompt", "")
    agent = data.get("agent", "JARVIS")
    resume = data.get("resume", False)

    cmd = ["kiro-cli", "chat", "--agent", agent, "--no-interactive", "--trust-all-tools"]
    if resume:
        cmd.append("--resume")
    cmd.append(prompt)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=280)
        response = proc.stdout.strip()
        if proc.returncode != 0 and not response:
            response = proc.stderr.strip() or f"kiro-cli exited {proc.returncode}"
        write_output("success", result=response)
    except subprocess.TimeoutExpired:
        write_output("error", error="kiro-cli timed out")
    except Exception as e:
        write_output("error", error=str(e))


def main():
    _patch_agent_paths()
    print("KIROCLAW_READY", flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            handle(data)
        except json.JSONDecodeError as e:
            write_output("error", error=f"Invalid JSON: {e}")


if __name__ == "__main__":
    main()
