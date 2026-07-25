#!/bin/bash
# Query remote JARVIS - MQTT primary, SSH fallback

MQTT_HOST="cappucino"
MQTT_USER="jarvis"
MQTT_PASS="TLzfqGsGVw5rL5j"

MODE="${JARVIS_MODE:-mqtt}"  # Default to MQTT, set JARVIS_MODE=ssh for SSH

if [ "$MODE" = "ssh" ]; then
    # SSH mode - synchronous
    ssh cappucino "echo '$*' | /home/yusuf/.local/bin/kiro-cli chat --agent jarvis --non-interactive 2>&1" | sed -n '/^>/,/Credits:/p'
else
    # MQTT mode - asynchronous
    mosquitto_pub -h "$MQTT_HOST" -u "$MQTT_USER" -P "$MQTT_PASS" \
      -t "jarvis/cappucino/query" -m "$*" -q 1
    
    echo "✅ Query sent to cappucino JARVIS via MQTT"
    echo "💡 Use JARVIS_MODE=ssh for synchronous response"
fi
