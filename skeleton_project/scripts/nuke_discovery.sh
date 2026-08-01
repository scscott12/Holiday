#!/usr/bin/env bash
set -euo pipefail
DEVICE="${1:-skeleton}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
: "${MQTT_HOST:=127.0.0.1}"
: "${MQTT_PORT:=1883}"
: "${MQTT_USER:=}"
: "${MQTT_PASS:=}"
pub() {
  local topic="$1"
  if [[ -n "$MQTT_USER" ]]; then
    mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" -u "$MQTT_USER" -P "$MQTT_PASS" -t "$topic" -n -r || true
  else
    mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" -t "$topic" -n -r || true
  fi
}
while IFS= read -r topic; do
  pub "$topic"
done < <(
  PYTHONPATH="${SCRIPT_DIR}/.." python3 - "$DEVICE" <<'PY'
import sys
from holiday_skeleton.discovery import discovery_messages

for topic, _payload in discovery_messages(sys.argv[1]):
    print(topic)
PY
)
echo "Done. Restart your service to republish clean discovery."
