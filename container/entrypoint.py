#!/usr/bin/env python3
"""Container entrypoint — long-running loop, reads JSON lines from stdin."""

import json
import subprocess
import sys

OUTPUT_START = "---KIROCLAW_OUTPUT_START---"
OUTPUT_END = "---KIROCLAW_OUTPUT_END---"


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
