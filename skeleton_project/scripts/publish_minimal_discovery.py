#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

from paho.mqtt import client as mqtt

# Allow this script to run directly from skeleton_project/scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from holiday_skeleton.discovery import discovery_messages
from holiday_skeleton.personality import PersonalityConfigError, PersonalityLibrary


HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
PORT = int(os.environ.get("MQTT_PORT", "1883"))
USER = os.environ.get("MQTT_USER", "")
PASSWORD = os.environ.get("MQTT_PASS", "")
DEVICE = os.environ.get("DEVICE_NAME", "skeleton")
PERSONALITIES_PATH = os.environ.get(
    "PERSONALITIES_PATH",
    str(Path(__file__).resolve().parents[1] / "personalities.json"),
)


def personality_names():
    try:
        return PersonalityLibrary.load(PERSONALITIES_PATH).names
    except PersonalityConfigError as error:
        print(f"Personality discovery fallback: {error}", file=sys.stderr)
        return ("legacy",)


def main():
    client = mqtt.Client()
    if USER:
        client.username_pw_set(USER, PASSWORD)
    client.connect(HOST, PORT, 60)
    client.loop_start()

    for topic, payload in discovery_messages(DEVICE, personality_names()):
        body = "" if payload is None else json.dumps(payload)
        client.publish(topic, body, retain=True)

    client.loop_stop()
    client.disconnect()
    print("Published minimal discovery set.")


if __name__ == "__main__":
    main()
