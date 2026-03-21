#!/usr/bin/env python3
"""Container entrypoint — long-running loop, reads JSON lines from stdin."""

import json
import os
import re
import subprocess
import sys

OUTPUT_START = "---KIROCLAW_OUTPUT_START---"
OUTPUT_END = "---KIROCLAW_OUTPUT_END---"

_ENV_PLACEHOLDER = re.compile(r"__ENV:(\w+)__")


def _patch_agent_configs():
    """Resolve __ENV:VAR__ placeholders in agent configs from container env vars."""
    agents_dir = os.path.expanduser("~/.kiro/agents")
    if not os.path.isdir(agents_dir):
        return
    for fname in os.listdir(agents_dir):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(agents_dir, fname)
        try:
            raw = open(fpath).read()
            patched = _ENV_PLACEHOLDER.sub(lambda m: os.environ.get(m.group(1), ""), raw)
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
    chat_id = data.get("chat_id", "")

    # Expose chat_id so IPC tools can reference it
    if chat_id:
        os.environ["JARVIS_CHAT_ID"] = str(chat_id)

    cmd = ["kiro-cli", "chat", "--agent", agent, "--no-interactive", "--trust-all-tools"]
    if resume:
        cmd.append("--resume")
    cmd.append(prompt)

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print(OUTPUT_START, flush=True)
        for line in proc.stdout:
            print(f"STREAM:{line.rstrip()}", flush=True)
        proc.wait(timeout=280)
        stderr = proc.stderr.read().strip()
        if proc.returncode != 0 and stderr:
            print(f"STREAM:{stderr}", flush=True)
        print(OUTPUT_END, flush=True)
    except subprocess.TimeoutExpired:
        proc.kill()
        print(OUTPUT_START, flush=True)
        print("STREAM:kiro-cli timed out", flush=True)
        print(OUTPUT_END, flush=True)
    except Exception as e:
        print(OUTPUT_START, flush=True)
        print(f"STREAM:{e}", flush=True)
        print(OUTPUT_END, flush=True)


def main():
    _patch_agent_configs()
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
