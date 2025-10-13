#!/usr/bin/env python3
import os, json
from paho.mqtt import client as mqtt

HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
PORT = int(os.environ.get("MQTT_PORT", "1883"))
USER = os.environ.get("MQTT_USER", "")
PASS = os.environ.get("MQTT_PASS", "")
DEVICE = os.environ.get("DEVICE_NAME", "skeleton")
BASE = f"holiday/{DEVICE}"
DISC = "homeassistant"

def pub(c, topic, payload, retain=True):
    c.publish(topic, payload, retain=retain)

def disc(comp, key):
    return f"{DISC}/{comp}/{DEVICE}/{key}/config"

def devobj():
    return {"identifiers":[f"holiday_{DEVICE}"],"name":DEVICE.capitalize(),"manufacturer":"SkeletonWorks","model":"Animatronic v1"}

def main():
    c = mqtt.Client()
    if USER:
        c.username_pw_set(USER, PASS)
    c.connect(HOST, PORT, 60)
    c.loop_start()
    dev = devobj()

    # Binary sensors
    pub(c, disc("binary_sensor","motion"), json.dumps({"name":f"{DEVICE.capitalize()} Motion","uniq_id":f"holiday_{DEVICE}_motion","stat_t":f"{BASE}/motion","pl_on":"ON","pl_off":"OFF","avty_t":f"{BASE}/availability","dev":dev}))
    pub(c, disc("binary_sensor","speaking"), json.dumps({"name":f"{DEVICE.capitalize()} Speaking","uniq_id":f"holiday_{DEVICE}_speaking","stat_t":f"{BASE}/speaking","pl_on":"ON","pl_off":"OFF","avty_t":f"{BASE}/availability","dev":dev}))
    pub(c, disc("binary_sensor","ready"), json.dumps({"name":f"{DEVICE.capitalize()} Ready","uniq_id":f"holiday_{DEVICE}_ready","stat_t":f"{BASE}/ready","pl_on":"ON","pl_off":"OFF","avty_t":f"{BASE}/availability","dev":dev}))

    # Sensors
    pub(c, disc("sensor","status"), json.dumps({"name":f"{DEVICE.capitalize()} Status","uniq_id":f"holiday_{DEVICE}_status","stat_t":f"{BASE}/status","avty_t":f"{BASE}/availability","dev":dev}))
    pub(c, disc("sensor","reply_time"), json.dumps({"name":f"{DEVICE.capitalize()} Reply Time","uniq_id":f"holiday_{DEVICE}_reply_time","stat_t":f"{BASE}/llm/reply_time","unit_of_measurement":"s","avty_t":f"{BASE}/availability","dev":dev}))
    pub(c, disc("sensor","transcript"), json.dumps({"name":f"{DEVICE.capitalize()} Transcript","uniq_id":f"holiday_{DEVICE}_transcript","stat_t":f"{BASE}/transcript","avty_t":f"{BASE}/availability","dev":dev}))

    # Numbers
    pub(c, disc("number","eyes_dim"), json.dumps({"name":f"{DEVICE.capitalize()} Eyes Dim %","uniq_id":f"holiday_{DEVICE}_eyes_dim","cmd_t":f"{BASE}/eyes/dim/set","stat_t":f"{BASE}/eyes/dim","min":0,"max":100,"step":1,"mode":"box","unit_of_measurement":"%","avty_t":f"{BASE}/availability","dev":dev}))
    pub(c, disc("number","eyes_full"), json.dumps({"name":f"{DEVICE.capitalize()} Eyes Full %","uniq_id":f"holiday_{DEVICE}_eyes_full","cmd_t":f"{BASE}/eyes/full/set","stat_t":f"{BASE}/eyes/full","min":0,"max":100,"step":1,"mode":"box","unit_of_measurement":"%","avty_t":f"{BASE}/availability","dev":dev}))
    pub(c, disc("number","volume"), json.dumps({"name":f"{DEVICE.capitalize()} Volume %","uniq_id":f"holiday_{DEVICE}_volume","cmd_t":f"{BASE}/volume/set","stat_t":f"{BASE}/volume","min":0,"max":200,"step":5,"mode":"box","unit_of_measurement":"%","avty_t":f"{BASE}/availability","dev":dev}))

    # Switches
    pub(c, disc("switch","motion_enabled"), json.dumps({"name":f"{DEVICE.capitalize()} Motion Enabled","uniq_id":f"holiday_{DEVICE}_motion_enabled","cmd_t":f"{BASE}/motion/enabled/set","stat_t":f"{BASE}/motion/enabled","pl_on":"ON","pl_off":"OFF","avty_t":f"{BASE}/availability","dev":dev}))
    pub(c, disc("switch","night_mode"), json.dumps({"name":f"{DEVICE.capitalize()} Night Mode","uniq_id":f"holiday_{DEVICE}_night_mode","cmd_t":f"{BASE}/night_mode/set","stat_t":f"{BASE}/night_mode","pl_on":"ON","pl_off":"OFF","avty_t":f"{BASE}/availability","dev":dev}))

    # Buttons
    pub(c, disc("button","say"), json.dumps({"name":f"{DEVICE.capitalize()} Say","uniq_id":f"holiday_{DEVICE}_say_btn","cmd_t":f"{BASE}/say/set","avty_t":f"{BASE}/availability","dev":dev}))
    pub(c, disc("button","blink"), json.dumps({"name":f"{DEVICE.capitalize()} Blink","uniq_id":f"holiday_{DEVICE}_blink_btn","cmd_t":f"{BASE}/blink/set","avty_t":f"{BASE}/availability","dev":dev}))
    pub(c, disc("button","flicker"), json.dumps({"name":f"{DEVICE.capitalize()} Flicker","uniq_id":f"holiday_{DEVICE}_flicker_btn","cmd_t":f"{BASE}/flicker/set","avty_t":f"{BASE}/availability","dev":dev}))
    pub(c, disc("button","restart"), json.dumps({"name":f"{DEVICE.capitalize()} Restart Service","uniq_id":f"holiday_{DEVICE}_restart_btn","cmd_t":f"{BASE}/restart/set","avty_t":f"{BASE}/availability","dev":dev}))
    pub(c, disc("button","motion_trigger"), json.dumps({"name":f"{DEVICE.capitalize()} Trigger Motion","uniq_id":f"holiday_{DEVICE}_motion_trigger_btn","cmd_t":f"{BASE}/motion/trigger/set","avty_t":f"{BASE}/availability","dev":dev}))

    c.loop_stop(); c.disconnect()
    print("Published minimal discovery set.")
if __name__ == "__main__":
    main()
