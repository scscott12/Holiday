#!/usr/bin/env bash
set -euo pipefail
DEVICE="${1:-skeleton}"
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
types=(button number switch sensor binary_sensor)
keys=(say blink flicker restart motion_trigger eyes_dim eyes_full volume motion_enabled night_mode status reply_time transcript motion speaking ready)
for typ in "${types[@]}"; do
  for key in "${keys[@]}"; do
    pub "homeassistant/${typ}/${DEVICE}/${key}/config"
  done
done
echo "Done. Restart your service to republish clean discovery."
