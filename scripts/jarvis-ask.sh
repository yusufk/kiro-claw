#!/bin/bash
# Ask cappucino JARVIS (SSH - synchronous response)
ssh cappucino "echo '$*' | /home/yusuf/.local/bin/kiro-cli chat --agent jarvis --non-interactive 2>&1" | tail -20
