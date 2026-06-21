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
_AUTH_URL_RE = re.compile(r"(https?://\S*(device|authorize|verify|sso|oidc)\S*)", re.IGNORECASE)


def _check_auth_url(line: str):
    """If line contains an auth/device-flow URL, send it to Telegram via IPC."""
    match = _AUTH_URL_RE.search(line)
    if not match:
        return
    url = match.group(1)
    _send_auth_ipc(f"🔑 Auth required — open this link:\n\n{url}")


def _send_auth_ipc(text: str):
    """Write an IPC message to Telegram."""
    chat_id = os.environ.get("JARVIS_CHAT_ID", "")
    if not chat_id:
        return
    ipc_dir = "/workspace/ipc"
    if not os.path.isdir(ipc_dir):
        return
    import time
    msg = json.dumps({"type": "message", "chat_id": int(chat_id), "text": text})
    fpath = os.path.join(ipc_dir, f"auth-{int(time.time() * 1000)}.json")
    with open(fpath, "w") as f:
        f.write(msg)


def _do_device_flow_login():
    """Run kiro-cli login --use-device-flow and send the URL to Telegram."""
    import time
    print("STREAM:Auth expired — starting device flow login...", flush=True)
    proc = subprocess.Popen(
        ["kiro-cli", "login", "--use-device-flow"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    # Read output looking for the URL (arrives in first few seconds)
    deadline = time.time() + 15
    for line in proc.stdout:
        stripped = line.strip()
        if "awsapps.com" in stripped or "user_code" in stripped:
            _send_auth_ipc(f"🔑 Auth required:\n\n{stripped}")
        match = _AUTH_URL_RE.search(stripped)
        if match:
            _send_auth_ipc(f"🔑 Open this link to authenticate FRIDAY:\n\n{match.group(1)}")
        if time.time() > deadline:
            break
    # Let login continue in background (user will auth on phone)
    # Don't wait forever — it'll complete when user approves
    try:
        proc.wait(timeout=300)
        if proc.returncode == 0:
            _send_auth_ipc("✅ Auth successful — FRIDAY back online.")
    except subprocess.TimeoutExpired:
        proc.kill()
        _send_auth_ipc("⚠️ Auth timed out — try sending a message again.")


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
            # Rewrite Mac paths to container paths
            patched = raw.replace("/Users/yusuf/Documents/Obsidian/Yusufs Vault/AI brain", "/workspace/brain")
            patched = _ENV_PLACEHOLDER.sub(lambda m: os.environ.get(m.group(1), ""), patched)
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

    cmd = ["kiro-cli", "chat", "--agent", agent, "--no-interactive", "--trust-all-tools", "--require-mcp-startup"]
    if resume:
        cmd.append("--resume")
    cmd.append(prompt)

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print(OUTPUT_START, flush=True)
        lines_out = []
        for line in proc.stdout:
            stripped = line.rstrip()
            print(f"STREAM:{stripped}", flush=True)
            lines_out.append(stripped)
            _check_auth_url(stripped)
        proc.wait(timeout=280)
        stderr = proc.stderr.read().strip()
        if proc.returncode != 0 and stderr:
            print(f"STREAM:{stderr}", flush=True)
            for errline in stderr.split("\n"):
                _check_auth_url(errline)

        # Detect auth failure and auto-trigger device flow login
        all_output = "\n".join(lines_out) + "\n" + (stderr or "")
        if "Failed to open browser" in all_output or "use-device-flow" in all_output:
            _do_device_flow_login()

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
