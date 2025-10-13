#!/usr/bin/env bash
set -euo pipefail
: "${MQTT_HOST:=127.0.0.1}"
: "${MQTT_PORT:=1883}"
: "${MQTT_USER:=}"
: "${MQTT_PASS:=}"
if [[ -n "$MQTT_USER" ]]; then
  mosquitto_sub -h "$MQTT_HOST" -p "$MQTT_PORT" -u "$MQTT_USER" -P "$MQTT_PASS" -t 'homeassistant/#' -v
else
  mosquitto_sub -h "$MQTT_HOST" -p "$MQTT_PORT" -t 'homeassistant/#' -v
fi
