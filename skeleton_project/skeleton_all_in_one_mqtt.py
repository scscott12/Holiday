#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Holiday Skeleton single-process runtime (Home Assistant ready)."""
import os, time, json, queue, subprocess, random, threading, re, wave, signal, math
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit
import numpy as np

from holiday_skeleton.audio import SpeechGate, resample_linear_int16
from holiday_skeleton.barge_in import (
    AnyStopEvent,
    BargeInAction,
    BargeInMatcher,
    BargeInMonitor,
)
from holiday_skeleton.brain import (
    ConversationMemory,
    OllamaStreamingClient,
    normalize_ollama_chat_url,
)
from holiday_skeleton.content import (
    ContentReloadError,
    ContentReloadInterrupted,
    prepare_content,
)
from holiday_skeleton.controller import EventKind, RuntimeState, SkeletonController
from holiday_skeleton.discovery import discovery_messages
from holiday_skeleton.idle_life import IdleAction, IdleDecision, IdleLifeScheduler
from holiday_skeleton.personality import (
    can_switch_personality,
    PersonalityConfigError,
    PersonalityLibrary,
)
from holiday_skeleton.health import (
    ComponentState,
    HealthSnapshot,
    RuntimeHealthMonitor,
)
from holiday_skeleton.scene import (
    SceneAction,
    SceneConfigError,
    SceneLibrary,
    SceneRunner,
    load_wav_pcm16,
    resolve_sound_path,
)
from holiday_skeleton.settings import (
    DayProfile,
    OperatorSettings,
    OperatorSettingsStore,
    SettingsConfigError,
)
from holiday_skeleton.self_test import (
    SelfTestInterrupted,
    SelfTestRunner,
    SelfTestStep,
    SelfTestStepSkipped,
)
from holiday_skeleton.speech import PiperSpeechEngine, SpeechEngineError
from holiday_skeleton.watchdog import (
    ControllerWatchdog,
    WatchdogSnapshot,
    WatchdogState,
)

def _safe_im(name, sub=None):
    try:
        return __import__(name) if sub is None else __import__(name, fromlist=[sub])
    except Exception as e:
        print(f"[import] {name} failed: {e}")
        return None

sd          = _safe_im("sounddevice")
requests    = _safe_im("requests")
vosk        = _safe_im("vosk")
gpiozero    = _safe_im("gpiozero")
board       = _safe_im("board")
busio       = _safe_im("busio")
pca9685_mod = _safe_im("adafruit_pca9685")
try:
    import adafruit_motor.servo as ada_servo_mod
except Exception as e:
    print("[import] adafruit_motor.servo failed:", e)
    ada_servo_mod = None
paho = _safe_im("paho") or _safe_im("paho.mqtt.client")

def clamp(x, lo, hi):
    try: 
        return lo if x < lo else hi if x > hi else x
    except: 
        return lo

def envs(k, d): v=os.getenv(k); return v if v is not None else d

def env_words(k, default):
    return tuple(
        word.strip()
        for word in envs(k, default).split(",")
        if word.strip()
    )

USER_HOME    = envs("HOME", "/home/pi")
DEVICE_NAME  = envs("DEVICE_NAME", "skeleton")
MQTT_HOST    = envs("MQTT_HOST", "<ipAddress>")
MQTT_PORT    = int(envs("MQTT_PORT", "1883"))
MQTT_USER    = envs("MQTT_USER", "<Username>")
MQTT_PASS    = envs("MQTT_PASS", "")
MQTT_BASE    = f"holiday/{DEVICE_NAME}"

MODEL_PATH   = envs("MODEL_PATH", f"{USER_HOME}/models/vosk-en")
PIPER_BIN    = envs("PIPER_BIN",  f"{USER_HOME}/bin/piper/piper")
PIPER_MODEL  = envs("PIPER_MODEL",f"{USER_HOME}/piper/en-gb-alan-low.onnx")
PIPER_CONFIG = envs("PIPER_CONFIG","").strip() or None
TTS_WAV      = envs("TTS_WAV", "/tmp/tts.wav")
TTS_FRAME_MS = float(envs("TTS_FRAME_MS","20"))
AUDIO_OUTPUT_DEVICE = envs("AUDIO_OUTPUT_DEVICE","").strip() or None
TTS_CANNED_CACHE = envs("TTS_CANNED_CACHE","1").strip().lower() in ("1","true","yes","on")

PCA_FREQ     = int(envs("PCA_FREQ","50"))
JAW_CH       = int(envs("JAW_CH","0"))
EYES_CH      = int(envs("EYES_CH","4"))
JAW_MIN_US   = int(envs("JAW_MIN_US","512"))
JAW_MAX_US   = int(envs("JAW_MAX_US","1000"))
JAW_REST_FRAC= float(envs("JAW_REST_FRAC","0.25"))
JAW_MAX_FRAC = float(envs("JAW_MAX_FRAC","1.0"))

EYES_IDLE_FRAC   = float(envs("EYES_IDLE_FRAC","0.0"))
EYES_LISTEN_FRAC = float(envs("EYES_LISTEN_FRAC","0.18"))
EYES_SPEAK_FRAC  = float(envs("EYES_SPEAK_FRAC","1.0"))
EYES_INVERT      = int(envs("EYES_INVERT","0"))

PIR_PIN          = int(envs("PIR_PIN","17"))
MOTION_HOLD_SEC  = float(envs("MOTION_HOLD_SEC","0.8"))
MOTION_COOLDOWN_SEC = float(envs("MOTION_COOLDOWN_SEC","8.0"))

IDLE_LIFE_ENABLED = envs("IDLE_LIFE_ENABLED","1").strip().lower() in ("1","true","yes","on")
IDLE_LIFE_MIN_SEC = float(envs("IDLE_LIFE_MIN_SEC","18.0"))
IDLE_LIFE_MAX_SEC = float(envs("IDLE_LIFE_MAX_SEC","45.0"))
IDLE_MUTTER_CHANCE = float(envs("IDLE_MUTTER_CHANCE","0.12"))
IDLE_EYE_PULSE_FRAC = float(envs("IDLE_EYE_PULSE_FRAC","0.10"))
IDLE_EYE_PULSE_MS = float(envs("IDLE_EYE_PULSE_MS","180"))
IDLE_JAW_TWITCH_FRAC = float(envs("IDLE_JAW_TWITCH_FRAC","0.14"))
IDLE_JAW_TWITCH_MS = float(envs("IDLE_JAW_TWITCH_MS","160"))

SCENES_ENABLED = envs("SCENES_ENABLED","1").strip().lower() in ("1","true","yes","on")
SCENES_PATH = envs("SCENES_PATH",os.path.join(os.path.dirname(__file__),"scenes.json"))
SCENE_SOUND_DIR = envs("SCENE_SOUND_DIR",os.path.join(os.path.dirname(__file__),"sounds"))
SCENE_MAX_SECONDS = float(envs("SCENE_MAX_SECONDS","30"))

PERSONALITIES_ENABLED = envs("PERSONALITIES_ENABLED","1").strip().lower() in ("1","true","yes","on")
PERSONALITIES_PATH = envs("PERSONALITIES_PATH",os.path.join(os.path.dirname(__file__),"personalities.json"))
PERSONALITY_REQUESTED = envs("PERSONALITY","").strip().lower()
CONTENT_RELOAD_ENABLED = envs("CONTENT_RELOAD_ENABLED","1").strip().lower() in ("1","true","yes","on")

PERSIST_SETTINGS_ENABLED = envs("PERSIST_SETTINGS_ENABLED","1").strip().lower() in ("1","true","yes","on")
PERSIST_SETTINGS_PATH = envs(
    "PERSIST_SETTINGS_PATH",
    "/var/lib/holiday-skeleton/operator-settings.json",
)
MAINTENANCE_MODE_DEFAULT = envs("MAINTENANCE_MODE","0").strip().lower() in (
    "1","true","yes","on"
)

HEALTH_INTERVAL_SEC = float(envs("HEALTH_INTERVAL_SEC","30"))
HEALTH_LATENCY_WINDOW = int(envs("HEALTH_LATENCY_WINDOW","20"))
HEALTH_TEMP_WARN_C = float(envs("HEALTH_TEMP_WARN_C","75"))
HEALTH_TEMP_CRITICAL_C = float(envs("HEALTH_TEMP_CRITICAL_C","82"))
HEALTH_DISK_WARN_PERCENT = float(envs("HEALTH_DISK_WARN_PERCENT","90"))
HEALTH_DISK_CRITICAL_PERCENT = float(envs("HEALTH_DISK_CRITICAL_PERCENT","97"))
OLLAMA_HEALTHCHECK_ENABLED = envs("OLLAMA_HEALTHCHECK_ENABLED","1").strip().lower() in ("1","true","yes","on")
WATCHDOG_CONTROLLER_STALE_SEC = float(envs("WATCHDOG_CONTROLLER_STALE_SEC","45"))

SELF_TEST_ENABLED = envs("SELF_TEST_ENABLED","1").strip().lower() in ("1","true","yes","on")
SELF_TEST_MAX_SECONDS = float(envs("SELF_TEST_MAX_SECONDS","12"))
SELF_TEST_EYES_FRAC = float(envs("SELF_TEST_EYES_FRAC","0.25"))
SELF_TEST_JAW_FRAC = float(envs("SELF_TEST_JAW_FRAC","0.20"))
SELF_TEST_STEP_SEC = float(envs("SELF_TEST_STEP_SEC","0.35"))
SELF_TEST_LINE = envs("SELF_TEST_LINE","Systems awake and ready.").strip()[:160]

VOSK_RATE        = int(envs("VOSK_RATE","16000"))
SD_BLOCKSIZE     = int(envs("SD_BLOCKSIZE","1024"))
ENERGY_GATE      = float(envs("ENERGY_GATE","180"))
MIN_TEXT_LEN     = int(envs("MIN_TEXT_LEN","2"))
PREROLL_SEC      = float(envs("PREROLL_SEC","0.5"))
NO_SPEECH_TIMEOUT= float(envs("NO_SPEECH_TIMEOUT","10.0"))
SPEECH_START_TIMEOUT = float(envs("SPEECH_START_TIMEOUT",str(NO_SPEECH_TIMEOUT)))
MIN_VOICED_SEC   = float(envs("MIN_VOICED_SEC","0.16"))
END_SILENCE_SEC  = float(envs("END_SILENCE_SEC","0.75"))
MAX_UTTERANCE_SEC= float(envs("MAX_UTTERANCE_SEC","12.0"))

BARGE_IN_ENABLED = envs("BARGE_IN_ENABLED","1").strip().lower() in ("1","true","yes","on")
BARGE_IN_STOP_COMMANDS = env_words("BARGE_IN_STOP_COMMANDS","stop,quiet")
BARGE_IN_LISTEN_COMMANDS = env_words("BARGE_IN_LISTEN_COMMANDS","wait")
BARGE_IN_WAKE_WORDS = env_words("BARGE_IN_WAKE_WORDS",DEVICE_NAME.replace("_"," "))
BARGE_IN_ENERGY_GATE = float(envs("BARGE_IN_ENERGY_GATE","320"))
BARGE_IN_MIN_VOICED_SEC = float(envs("BARGE_IN_MIN_VOICED_SEC","0.10"))
BARGE_IN_PARTIAL_CONFIRMATIONS = int(envs("BARGE_IN_PARTIAL_CONFIRMATIONS","2"))
BARGE_IN_CAPTURE_RATE = int(envs("BARGE_IN_CAPTURE_RATE","44100"))
BARGE_IN_REQUIRE_WAKE_WORD = envs("BARGE_IN_REQUIRE_WAKE_WORD","0").strip().lower() in ("1","true","yes","on")

OLLAMA_URL   = normalize_ollama_chat_url(envs("OLLAMA_CHAT_URL",envs("OLLAMA_URL","http://127.0.0.1:11434/api/chat")))
OLLAMA_MODEL = envs("OLLAMA_MODEL","qwen2.5:0.5b")
KEEP_ALIVE   = envs("KEEP_ALIVE","24h")
OLLAMA_TIMEOUT = (3, 30)
LLM_MEMORY_TURNS = max(0,int(envs("LLM_MEMORY_TURNS","3")))
LLM_CONTEXT_TOKENS = max(128,int(envs("LLM_CONTEXT_TOKENS","512")))
LLM_MAXIMUM_TOKENS = int(envs("LLM_MAXIMUM_TOKENS","50"))
LLM_TEMPERATURE = float(envs("LLM_TEMPERATURE","0.6"))
LLM_REPEAT_PENALTY = float(envs("LLM_REPEAT_PENALTY","1.05"))
OLLAMA_OPTS  = {"num_predict": LLM_MAXIMUM_TOKENS, "num_thread": 4, "temperature": LLM_TEMPERATURE, "repeat_penalty": LLM_REPEAT_PENALTY, "num_ctx": LLM_CONTEXT_TOKENS}
LLM_PHRASE_MIN_CHARS = int(envs("LLM_PHRASE_MIN_CHARS","12"))
LLM_PHRASE_SOFT_CHARS = int(envs("LLM_PHRASE_SOFT_CHARS","36"))
LLM_PHRASE_MAX_CHARS = int(envs("LLM_PHRASE_MAX_CHARS","72"))
VOLUME       = float(envs("VOLUME","1.0"))
PERSONALITY_VOLUME_MULTIPLIER = 1.0

SYSTEM_PROMPT = (
    "You are a semi-retired pirate trying to act normal in modern times. "
    "Be witty, chaotic, and playfully dramatic — a mix of trauma dumping and humor. "
    "Use Gen Z slang naturally. One short sentence. Never curse."
)
LLM_FALLBACK_LINE = "Arrr, I be old and forgetful — say it again, matey!"
PROMPTS_PATH = envs("PROMPTS_PATH", os.path.join(os.path.dirname(__file__), "prompts.json"))

MORNING_LINES=[
"Ayo, morning hit harder than a cannonball — you sleep or just vibe through it?",
"Lowkey forgot the sun still does this rising thing — you a morning person or nah?",
"Bruh, I’m not even caffeinated and the sky’s already loud. You good?"
]
AFTERNOON_LINES=[
"Midday vibes hittin’ weird — feels like the ocean’s plotting something again.",
"Lowkey feel like I should be plundering something right now. You busy?",
"Afternoons feel like filler episodes — you got main quest energy left?"
]
EVENING_LINES=[
"Yo, sun’s clocking out — wish I could too, fr.",
"Twilight’s giving ‘emotional damage but pretty’ — you good?",
"The light hits different — makes me wanna overshare then disappear."
]
NIGHT_LINES=[
"Moon’s out and I’m spiraling — you up?",
"Quiet seas are sus — chaos is comforting, no cap.",
"Night’s got that ‘haunted by choices but vibin’ anyway’ energy."
]
GOODBYE_LINES=[
"Aight, gotta bounce — my emotional support parrot’s acting toxic again.",
"Fr tho, storms don’t wait for sad boys. Catch you on calmer seas.",
"Peace out, sailor — my attention span just walked the plank."
]
IDLE_LINES=[
"Did that shadow just move, or am I losing me marbles again?",
"Still here. Still dead. Weirdly productive.",
"The silence be loud tonight.",
"I swear that parrot still owes me money.",
"Just resting me bones. All of them."
]

_scene_library=None
_scene_runner=None
_scene_sound_cache={}
_scene_sound_errors={}
_scene_load_error=""
_scene_count=0
_scene_interrupted=0
_scene_active=False
_scene_current="none"
_scene_step="none"

_personality_library=None
_personality_active=None
_personality_load_error=""
_personality_switch_count=0
_personality_last_result="starting"
_personality_last_error="none"

_content_reload_active=False
_content_reload_pending=False
_content_reload_state="starting"
_content_reload_last_result="never"
_content_reload_last_error="none"
_content_reload_last_run="never"
_content_reload_last_duration=0.0
_content_reload_count=0
_content_reload_interrupted=0

_settings_store=None
_settings_loaded=None
_settings_state="starting"
_settings_last_saved="never"
_settings_last_error="none"
_settings_save_count=0

_self_test_runner=None
_self_test_active=False
_self_test_pending=False
_self_test_cancel_pending=False
_self_test_step="none"
_self_test_last_result="never"
_self_test_last_error="none"
_self_test_last_run="never"
_self_test_count=0
_self_test_interrupted=0
_self_test_report="{}"

maintenance_mode=MAINTENANCE_MODE_DEFAULT
_maintenance_state="starting"
_maintenance_last_result="never"
_maintenance_last_error="none"
_maintenance_since="never"
_maintenance_rejected_count=0

def pick_opening_line()->str:
    hr=datetime.now().hour
    if 5<=hr<12: return random.choice(MORNING_LINES)
    if 12<=hr<17: return random.choice(AFTERNOON_LINES)
    if 17<=hr<21: return random.choice(EVENING_LINES)
    return random.choice(NIGHT_LINES)

def _try_load_prompts():
    global SYSTEM_PROMPT,MORNING_LINES,AFTERNOON_LINES,EVENING_LINES,NIGHT_LINES,GOODBYE_LINES,IDLE_LINES
    try:
        if os.path.isfile(PROMPTS_PATH):
            with open(PROMPTS_PATH,"r",encoding="utf-8") as f: data=json.load(f)
            SYSTEM_PROMPT=data.get("SYSTEM_PROMPT",SYSTEM_PROMPT)
            MORNING_LINES=data.get("MORNING_LINES",MORNING_LINES)
            AFTERNOON_LINES=data.get("AFTERNOON_LINES",AFTERNOON_LINES)
            EVENING_LINES=data.get("EVENING_LINES",EVENING_LINES)
            NIGHT_LINES=data.get("NIGHT_LINES",NIGHT_LINES)
            GOODBYE_LINES=data.get("GOODBYE_LINES",GOODBYE_LINES)
            IDLE_LINES=data.get("IDLE_LINES",IDLE_LINES)
            print(f"[prompts] Loaded from {PROMPTS_PATH}")
    except Exception as e: print("[prompts] Failed:",e)
_try_load_prompts()

def _canned_speech_lines(pack=None):
    """Return each configured opening/goodbye line once, in stable order."""
    if pack is not None:
        configured=list(pack.canned_lines)
    else:
        configured=list(dict.fromkeys(
            str(line).strip()
            for group in (
                MORNING_LINES,
                AFTERNOON_LINES,
                EVENING_LINES,
                NIGHT_LINES,
                GOODBYE_LINES,
                IDLE_LINES,
            )
            for line in group
            if str(line).strip()
        ))
    if _scene_library is not None:
        configured.extend(
            str(step.parameters["text"])
            for scene_name in _scene_library.names
            for step in _scene_library.get(scene_name).steps
            if step.action is SceneAction.SPEAK
        )
    if SELF_TEST_ENABLED and SELF_TEST_LINE:
        configured.append(SELF_TEST_LINE)
    return list(dict.fromkeys(configured))

def _personality_names():
    if _personality_library is not None:
        return _personality_library.names
    return ("legacy",)

def _personality_active_name():
    return _personality_active.name if _personality_active is not None else "legacy"

EXIT_RE=re.compile(r"\b(good\s?bye|bye|farewell|that'?s all|we('re| are) done|stop now|quit|exit|shut\s?down)\b",re.I)

def _make_mqtt_client():
    try:
        from paho.mqtt import client as mqtt_client
        client_id=f"holiday_{DEVICE_NAME}"
        try: return mqtt_client.Client(client_id=client_id, clean_session=True, transport="tcp")
        except TypeError: return mqtt_client.Client(client_id)
    except Exception as e: print("[mqtt] client unavailable:",e); return None

mqttc=_make_mqtt_client()
_mqtt_connected=False
_runtime_ready=False
controller=None
_health=None
_watchdog=None

def _maintenance_stop_requested():
    interrupt=(
        getattr(controller,"maintenance_interrupt_event",None)
        if controller is not None
        else None
    )
    return bool(
        maintenance_mode
        or (interrupt is not None and interrupt.is_set())
    )

def mqtt_pub(topic,payload,retain=False):
    try:
        if mqttc: mqttc.publish(f"{MQTT_BASE}/{topic}",payload,retain=retain)
    except Exception as e: print("[mqtt publish]",e)
def mqtt_pub_abs(topic,payload,retain=False):
    try:
        if mqttc: mqttc.publish(topic,payload,retain=retain)
    except Exception: pass

def _health_set(name,state,detail="",critical=False,publish=True):
    if _health is not None:
        _health.set_component(name,state,detail,critical,publish=publish)

def _health_latency(name,seconds):
    if _health is not None:
        _health.record_latency(name,seconds)

def _health_increment(name,amount=1):
    if _health is not None:
        _health.increment(name,amount)

def _publish_watchdog_snapshot(snapshot=None):
    if snapshot is None and _watchdog is not None:
        snapshot=_watchdog.snapshot()
    if snapshot is None: return
    age=snapshot.controller_age_seconds
    mqtt_pub("watchdog/enabled","ON" if snapshot.enabled else "OFF",retain=True)
    mqtt_pub("watchdog/state",snapshot.state.value,retain=True)
    mqtt_pub("watchdog/controller_age",_metric_text(age),retain=True)
    mqtt_pub("watchdog/last_feed",snapshot.last_feed,retain=True)
    mqtt_pub("watchdog/feed_count",str(snapshot.feed_count),retain=True)
    mqtt_pub("watchdog/last_error",snapshot.last_error or "none",retain=True)

def _watchdog_changed(snapshot:WatchdogSnapshot):
    if snapshot.state is WatchdogState.DISABLED:
        state=ComponentState.DISABLED; detail="not running under systemd watchdog"
        critical=False
    elif snapshot.state is WatchdogState.STARTING:
        state=ComponentState.STARTING; detail=snapshot.last_error or "controller heartbeat pending"
        critical=False
    elif snapshot.state is WatchdogState.READY:
        state=ComponentState.READY
        detail=(
            f"systemd watchdog active; controller stale limit "
            f"{float(snapshot.stale_after_seconds or 0.0):.1f}s"
        )
        critical=False
    elif snapshot.state is WatchdogState.STALE:
        state=ComponentState.FAILED; detail=snapshot.last_error
        critical=True
    elif snapshot.state is WatchdogState.ERROR:
        state=ComponentState.DEGRADED; detail=snapshot.last_error
        critical=False
    else:
        state=ComponentState.STOPPING; detail="watchdog stopping"
        critical=False
    _health_set("watchdog",state,detail,critical)
    _publish_watchdog_snapshot(snapshot)

def _init_systemd_watchdog():
    global _watchdog
    _watchdog=ControllerWatchdog.from_environment(
        stale_after_seconds=WATCHDOG_CONTROLLER_STALE_SEC,
        changed=_watchdog_changed,
    )
    _watchdog.notifier.status("warming skeleton runtime")
    _watchdog_changed(_watchdog.snapshot())

def _controller_heartbeat(state):
    if _watchdog is not None:
        _watchdog.pulse(getattr(state,"value",state))

def _publish_health_snapshot(snapshot:HealthSnapshot):
    telemetry=snapshot.telemetry
    mqtt_pub("health/status",snapshot.state.value,retain=True)
    mqtt_pub("health/ok","ON" if snapshot.state.value == "healthy" else "OFF",retain=True)
    reasons=("; ".join(snapshot.reasons) or "none")[:255]
    mqtt_pub("health/reasons",reasons,retain=True)
    mqtt_pub("health/components",snapshot.component_attributes_json(),retain=True)
    mqtt_pub("health/heartbeat",str(snapshot.heartbeat),retain=True)
    mqtt_pub("health/last_update",snapshot.timestamp,retain=True)
    mqtt_pub("health/cpu_temperature",_metric_text(telemetry.cpu_temperature_c),retain=True)
    mqtt_pub("health/load_1m",_metric_text(telemetry.load_1m),retain=True)
    mqtt_pub("health/memory_percent",_metric_text(telemetry.memory_percent),retain=True)
    mqtt_pub("health/disk_percent",_metric_text(telemetry.disk_percent),retain=True)
    mqtt_pub("health/uptime",_metric_text(telemetry.uptime_seconds,0),retain=True)
    mqtt_pub("health/throttled","ON" if telemetry.currently_throttled else "OFF",retain=True)
    mqtt_pub("health/throttle_flags",",".join(telemetry.throttle_flags) or "none",retain=True)
    mqtt_pub("health/audio_dropped_frames",str(snapshot.counters.get("audio_dropped_frames",0)),retain=True)
    _publish_watchdog_snapshot()
    for name in ("tts_first_audio","greeting_first_audio","response_first_audio","llm_reply"):
        metrics=snapshot.latencies.get(name)
        mqtt_pub(f"health/latency/{name}_average",_metric_text(metrics.average if metrics else None),retain=True)
        mqtt_pub(f"health/latency/{name}_p95",_metric_text(metrics.p95 if metrics else None),retain=True)
        mqtt_pub(f"health/latency/{name}_samples",str(metrics.count if metrics else 0),retain=True)
    mqtt_pub(
        "ready",
        "ON" if (_runtime_ready and snapshot.operational) else "OFF",
        retain=True,
    )

def _metric_text(value,decimals=3):
    if value is None: return "None"
    return f"{float(value):.{int(decimals)}f}"

def _enqueue(kind,payload=None,source="mqtt"):
    if controller is None:
        print(f"[controller] dropped {kind.value}; runtime not ready")
        return False
    accepted=controller.enqueue(kind,payload,source)
    if not accepted:
        print(f"[controller] coalesced or dropped {kind.value} from {source}")
    return accepted

_MAINTENANCE_BLOCKED_EVENTS={
    EventKind.TRIGGER,
    EventKind.SAY,
    EventKind.BLINK,
    EventKind.FLICKER,
    EventKind.PLAY_SCENE,
    EventKind.RUN_SELF_TEST,
}

def _record_maintenance_result(result,error="none"):
    global _maintenance_last_result,_maintenance_last_error
    _maintenance_last_result=str(result)
    _maintenance_last_error=str(error or "none")[:255]
    _publish_maintenance_state()

def _maintenance_reject(action):
    global _maintenance_rejected_count
    _maintenance_rejected_count+=1
    _record_maintenance_result(
        "blocked",
        f"maintenance lockout blocked {str(action or 'command')}",
    )
    return False

def _maintenance_topic_action(topic):
    blocked={
        "/motion/trigger/set":"motion trigger",
        "/say/set":"speech",
        "/blink/set":"blink",
        "/flicker/set":"flicker",
        "/scene/play/set":"scene",
        "/personality/default_scene/play/set":"personality scene",
        "/self_test/run/set":"self-test",
    }
    return next((label for suffix,label in blocked.items() if topic.endswith(suffix)),None)

def _on_message(client,userdata,msg):
    t=msg.topic; p=msg.payload.decode("utf-8","ignore").strip()
    if t.endswith("/maintenance/set"):
        _request_maintenance_mode(p)
        return
    blocked_action=_maintenance_topic_action(t)
    if _maintenance_stop_requested() and blocked_action:
        _maintenance_reject(blocked_action)
        return
    if t.endswith("/content/reload/set"):
        _request_content_reload()
        return
    if t.endswith("/self_test/run/set"):
        _request_self_test()
        return
    if t.endswith("/self_test/stop/set"):
        _stop_self_test()
        return
    if _self_test_active and controller is not None:
        controller.interrupt_self_test()
    if t.endswith("/personality/set"):
        _request_personality_switch(p)
        return
    if t.endswith("/personality/default_scene/play/set"):
        scene=_personality_active.default_scene if _personality_active is not None else ""
        if scene:
            _enqueue(EventKind.PLAY_SCENE,scene)
        else:
            _record_personality_result("error","active personality has no default scene")
        return
    if t.endswith("/scene/play/set"):
        if p: _enqueue(EventKind.PLAY_SCENE,p)
        return
    if t.endswith("/scene/stop/set"): _enqueue(EventKind.STOP_SCENE); return
    if t.endswith("/say/set"):
        if p: _enqueue(EventKind.SAY,p)
        return
    if t.endswith("/motion/trigger/set"): _enqueue(EventKind.TRIGGER,source="mqtt"); return
    if t.endswith("/eyes/dim/set"):
        try: v=float(p); v=v/100.0 if v>1.0 else v; _enqueue(EventKind.SET_EYES_DIM,v)
        except: pass; return
    if t.endswith("/eyes/full/set"):
        try: v=float(p); v=v/100.0 if v>1.0 else v; _enqueue(EventKind.SET_EYES_FULL,v)
        except: pass; return
    if t.endswith("/blink/set"): _enqueue(EventKind.BLINK); return
    if t.endswith("/flicker/set"): _enqueue(EventKind.FLICKER); return
    if t.endswith("/volume/set"):
        try: v=float(p); v=v/100.0 if v>2.0 else v; _enqueue(EventKind.SET_VOLUME,v)
        except: pass; return
    if t.endswith("/motion/enabled/set"): _enqueue(EventKind.SET_MOTION_ENABLED,p); return
    if t.endswith("/idle_life/enabled/set"): _enqueue(EventKind.SET_IDLE_LIFE_ENABLED,p); return
    if t.endswith("/night_mode/set"): _enqueue(EventKind.SET_NIGHT_MODE,p); return
    if t.endswith("/restart/set"): _enqueue(EventKind.RESTART); return

def _on_connect(client,userdata,flags,rc,properties=None):
    global _mqtt_connected; _mqtt_connected=(rc==0)
    print(f"[mqtt] on_connect rc={rc}")
    if _mqtt_connected:
        _health_set("mqtt",ComponentState.READY,"connected")
        mqtt_pub("availability","online",retain=True); mqtt_pub("status","starting",retain=True)
        publish_mqtt_discovery()
        subs=["say/set","eyes/dim/set","eyes/full/set","blink/set","flicker/set","volume/set",
              "motion/trigger/set","motion/enabled/set","idle_life/enabled/set",
              "scene/play/set","scene/stop/set","personality/set",
              "personality/default_scene/play/set","self_test/run/set",
              "self_test/stop/set","content/reload/set","night_mode/set",
              "maintenance/set","restart/set"]
        for path in subs:
            try: client.subscribe(f"{MQTT_BASE}/{path}")
            except Exception as e: print("[mqtt subscribe]",e)
        mqtt_pub("ready","ON" if _runtime_ready else "OFF")
        if _idle_life is not None: _publish_idle_life_ready_state()
        if _scene_library is not None: _publish_scene_ready_state()
        _publish_personality_state()
        _publish_operator_controls()
        _publish_maintenance_state()
        _publish_settings_state()
        _publish_self_test_state()
        _publish_content_reload_state()
        _publish_watchdog_snapshot()
        if _health is not None: _health.publish_now(sample=False)
    else:
        _health_set("mqtt",ComponentState.FAILED,f"connection rc={rc}")

def _on_disconnect(client,userdata,rc,properties=None):
    global _mqtt_connected; _mqtt_connected=False
    print(f"[mqtt] on_disconnect rc={rc}")
    _health_set("mqtt",ComponentState.DEGRADED,f"disconnected rc={rc}")

def mqtt_connect():
    if mqttc is None: return
    try:
        mqttc.on_connect=_on_connect; mqttc.on_disconnect=_on_disconnect; mqttc.on_message=_on_message
        try: mqttc.will_set(f"{MQTT_BASE}/availability","offline",retain=True)
        except Exception: pass
        if MQTT_USER:
            try: mqttc.username_pw_set(MQTT_USER,MQTT_PASS)
            except Exception: pass
        mqttc.connect(MQTT_HOST,MQTT_PORT,keepalive=60); mqttc.loop_start()
        for _ in range(30): time.sleep(0.05)
    except Exception as e: print("[mqtt connect]",e)

_pca=None; _eyes_ch=None; _jaw=None
_eyes_effect_thread=None; _eyes_effect_stop=threading.Event(); _eyes_lock=threading.Lock()

def eyes_set(frac:float):
    frac=clamp(float(frac),0.0,1.0)
    if _maintenance_stop_requested(): frac=0.0
    if EYES_INVERT: frac=1.0-frac
    with _eyes_lock:
        if _eyes_ch is None: return False
        try:
            _eyes_ch.duty_cycle=int(0xFFFF*frac)
            return True
        except Exception as e:
            print("[eyes set]",e)
            return False

def _stop_eyes_effect():
    global _eyes_effect_thread,_eyes_effect_stop
    _eyes_effect_stop.set()
    if _eyes_effect_thread and _eyes_effect_thread.is_alive():
        try: _eyes_effect_thread.join(timeout=0.5)
        except: pass
    _eyes_effect_stop=threading.Event(); _eyes_effect_thread=None

def eyes_off():    _stop_eyes_effect(); eyes_set(0.0)
def eyes_idle():   _stop_eyes_effect(); eyes_set(EYES_IDLE_FRAC)
def eyes_listen(): _stop_eyes_effect(); eyes_set(EYES_LISTEN_FRAC)
def eyes_speak():  _stop_eyes_effect(); eyes_set(EYES_SPEAK_FRAC)

def _eyes_effect_wait(seconds):
    deadline=time.monotonic()+max(0.0,float(seconds))
    while True:
        if _eyes_effect_stop.is_set() or _maintenance_stop_requested(): return True
        remaining=deadline-time.monotonic()
        if remaining <= 0: return False
        if _eyes_effect_stop.wait(min(0.02,remaining)): return True

def eyes_blink(count=6, period_ms=120, low=0.0, high=None, blocking=False):
    global _eyes_effect_thread,_eyes_effect_stop
    _stop_eyes_effect(); high = EYES_SPEAK_FRAC if high is None else clamp(high,0,1)
    def run():
        for _ in range(max(1,int(count))):
            if _eyes_effect_stop.is_set() or _maintenance_stop_requested(): break
            eyes_set(high)
            if _eyes_effect_wait(max(0.01,period_ms/1000.0/2)): break
            eyes_set(low)
            if _eyes_effect_wait(max(0.01,period_ms/1000.0/2)): break
        eyes_set(0.0 if _maintenance_stop_requested() else EYES_IDLE_FRAC)
    _eyes_effect_thread=threading.Thread(target=run,daemon=True); _eyes_effect_thread.start()
    if blocking: _eyes_effect_thread.join()

def eyes_flicker(duration_s=5.0, base=0.2, span=0.7, step_ms=60, blocking=False):
    global _eyes_effect_thread,_eyes_effect_stop
    _stop_eyes_effect(); base=clamp(base,0,1); span=clamp(span,0,1-base); start=time.time()
    def run():
        while (
            time.time()-start<duration_s
            and not _eyes_effect_stop.is_set()
            and not _maintenance_stop_requested()
        ):
            eyes_set(base+random.random()*span)
            if _eyes_effect_wait(max(0.02,step_ms/1000.0)): break
        eyes_set(0.0 if _maintenance_stop_requested() else EYES_IDLE_FRAC)
    _eyes_effect_thread=threading.Thread(target=run,daemon=True); _eyes_effect_thread.start()
    if blocking: _eyes_effect_thread.join()

def _jaw_set(frac:float):
    try:
        if _jaw is None: return False
        if _maintenance_stop_requested(): frac=JAW_REST_FRAC
        _jaw.fraction=clamp(frac,0,1)
        return True
    except Exception as e:
        print("[jaw]",e)
        return False

def jaw_env_from_wav(path:str):
    try:
        wf=wave.open(path,"rb"); rate=wf.getframerate(); nchan=wf.getnchannels(); hop=int(rate*0.02)
        env=[]
        while True:
            frames=wf.readframes(hop)
            if not frames: break
            data=np.frombuffer(frames,dtype=np.int16)
            if nchan>1: data=data.reshape(-1,nchan)[:,0]
            rms=float(np.sqrt(np.mean(data.astype(np.float32)**2))) if data.size else 0.0
            env.append(rms)
        wf.close()
        if not env: env=[0.0]
        arr=np.array(env,dtype=np.float32); m=float(np.percentile(arr,95) or 1.0)
        return np.clip(arr/m,0.0,1.0)
    except Exception as e:
        print("[jaw env]",e); return np.array([0.0],dtype=np.float32)

def _stop_wait(stop_event,duration,quantum=0.01):
    deadline=time.monotonic()+max(0.0,float(duration))
    while True:
        if stop_event is not None and stop_event.is_set(): return True
        remaining=deadline-time.monotonic()
        if remaining <= 0: return False
        time.sleep(min(max(0.001,float(quantum)),remaining))

def jaw_drive_by_env(
    env:np.ndarray,
    rest=JAW_REST_FRAC,
    mx=JAW_MAX_FRAC,
    period=0.02,
    stop_event=None,
):
    for v in env:
        if stop_event is not None and stop_event.is_set(): break
        _jaw_set(rest+(mx-rest)*float(v))
        if _stop_wait(stop_event,period): break
    _jaw_set(rest)

def jaw_chatter_fallback(text:str,stop_event=None):
    dur=clamp(1.1+0.05*len(text),1.0,6.0); end=time.time()+dur
    rest,open_=JAW_REST_FRAC,JAW_MAX_FRAC
    while time.time()<end:
        if stop_event is not None and stop_event.is_set(): break
        _jaw_set(open_)
        if _stop_wait(stop_event,0.09): break
        _jaw_set(rest)
        if _stop_wait(stop_event,0.07): break
    _jaw_set(rest)

def _amplify_wav_inplace(path:str, volume:float):
    try:
        volume=float(volume); volume=(volume/100.0) if volume>2.0 else volume; volume=clamp(volume,0,2)
        if abs(volume-1.0)<1e-3: return
        with wave.open(path,"rb") as wf: params=wf.getparams(); frames=wf.readframes(wf.getnframes())
        data=np.frombuffer(frames,dtype=np.int16).astype(np.float32)
        data=np.clip(np.round(data*volume),-32768,32767).astype(np.int16)
        with wave.open(path,"wb") as wf2: wf2.setparams(params); wf2.writeframes(data.tobytes())
    except Exception as e: print("[volume]",e)

def _speech_volume():
    return clamp(VOLUME*PERSONALITY_VOLUME_MULTIPLIER,0.0,2.0)

_speech_lock=threading.Lock()
_speech_engine=None
_llm_client=None
_barge_in_count=0
_barge_in_matcher=BargeInMatcher(
    stop_commands=BARGE_IN_STOP_COMMANDS,
    listen_commands=BARGE_IN_LISTEN_COMMANDS,
    wake_words=BARGE_IN_WAKE_WORDS,
    require_wake_word=BARGE_IN_REQUIRE_WAKE_WORD,
)

def _barge_in_supported():
    return bool(
        BARGE_IN_ENABLED
        and not _maintenance_stop_requested()
        and _speech_engine is not None
        and stt_enabled
        and sd is not None
        and vosk is not None
        and _VOSK_MODEL is not None
        and in_idx is not None
    )

def _publish_barge_in_capability():
    if _maintenance_stop_requested():
        state="maintenance"
    elif _barge_in_supported():
        state="ready"
    elif not BARGE_IN_ENABLED:
        state="disabled"
    elif _speech_engine is None:
        state="legacy_tts"
    elif not stt_enabled or in_idx is None:
        state="no_microphone"
    else:
        state="unavailable"
    mqtt_pub("barge_in/enabled","ON" if state == "ready" else "OFF",retain=True)
    mqtt_pub("barge_in/active","OFF",retain=True)
    mqtt_pub("barge_in/state",state,retain=True)
    mqtt_pub("barge_in/count",str(_barge_in_count),retain=True)
    if state == "ready":
        _health_set("barge_in",ComponentState.READY,"command monitor ready")
    elif state in ("disabled","maintenance"):
        detail=(
            "operator maintenance lockout"
            if state == "maintenance"
            else "disabled by configuration"
        )
        _health_set("barge_in",ComponentState.DISABLED,detail)
    else:
        _health_set("barge_in",ComponentState.DEGRADED,state)

def _start_barge_in_monitor():
    if not _barge_in_supported(): return None

    def recognizer_factory(grammar_json):
        recognizer=vosk.KaldiRecognizer(_VOSK_MODEL,VOSK_RATE,grammar_json)
        try: recognizer.SetWords(False)
        except Exception: pass
        return recognizer

    monitor=BargeInMonitor(
        audio_module=sd,
        recognizer_factory=recognizer_factory,
        matcher=_barge_in_matcher,
        input_device=in_idx,
        capture_rate=BARGE_IN_CAPTURE_RATE,
        recognition_rate=VOSK_RATE,
        blocksize=SD_BLOCKSIZE,
        energy_threshold=BARGE_IN_ENERGY_GATE,
        minimum_voiced_seconds=BARGE_IN_MIN_VOICED_SEC,
        partial_confirmations=BARGE_IN_PARTIAL_CONFIRMATIONS,
        parent_stop_event=(
            AnyStopEvent(
                controller.stop_event,
                controller.maintenance_interrupt_event,
            )
            if controller is not None
            else None
        ),
    ).start()
    mqtt_pub("barge_in/active","ON")
    mqtt_pub("barge_in/state","listening")
    return monitor

def _finish_barge_in_monitor(monitor):
    global _barge_in_count
    if monitor is None: return None
    result=monitor.stop()
    mqtt_pub("barge_in/active","OFF")
    if monitor.error:
        print(f"[barge-in] microphone monitor failed: {monitor.error}")
        mqtt_pub("barge_in/state","error")
        _health_set("barge_in",ComponentState.DEGRADED,f"monitor failed: {monitor.error}")
    else:
        mqtt_pub("barge_in/state","ready")
        _health_set("barge_in",ComponentState.READY,"command monitor ready")
    if result is not None:
        _barge_in_count+=1
        _transcript_add("command",result.transcript)
        mqtt_pub("barge_in/last_command",result.transcript)
        mqtt_pub("barge_in/last_action",result.action.value)
        mqtt_pub("barge_in/latency",f"{result.detected_seconds:.3f}")
        mqtt_pub("barge_in/count",str(_barge_in_count))
        print(
            f"[barge-in] {result.transcript!r} -> {result.action.value} "
            f"in {result.detected_seconds:.3f}s"
        )
    return result

def _legacy_speak_with_jaw(text:str,stop_event=None):
    """Compatibility path for installs that have not added piper-tts yet."""
    if stop_event is not None and stop_event.is_set(): return
    try:
        p=subprocess.Popen([PIPER_BIN,"-m",PIPER_MODEL,"-f",TTS_WAV,"-q"],stdin=subprocess.PIPE,text=True,
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        p.stdin.write(text)
        p.stdin.close()
        while p.poll() is None:
            if stop_event is not None and stop_event.is_set():
                p.terminate()
                try: p.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    p.kill(); p.wait(timeout=0.5)
                return
            time.sleep(0.02)
        if p.returncode != 0:
            raise RuntimeError(f"Piper exited with status {p.returncode}")
    except Exception as e:
        print("[TTS legacy]",e)
        if stop_event is None or not stop_event.is_set():
            jaw_chatter_fallback(text,stop_event)
        return
    if stop_event is not None and stop_event.is_set(): return
    _amplify_wav_inplace(TTS_WAV,_speech_volume())
    env=jaw_env_from_wav(TTS_WAV)
    try:
        player=subprocess.Popen(
            ["aplay","-q",TTS_WAV],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print("[aplay]",e); return
    try:
        if float(np.max(env) if env.size else 0.0)<0.05:
            jaw_chatter_fallback(text,stop_event)
        else:
            jaw_drive_by_env(env,stop_event=stop_event)
        while player.poll() is None:
            if stop_event is not None and stop_event.is_set():
                player.terminate()
                break
            time.sleep(0.02)
        try: player.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            player.kill(); player.wait(timeout=0.5)
    finally:
        _jaw_set(JAW_REST_FRAC)

def speak_phrases_with_jaw(
    phrases,
    first_audio=None,
    abort=None,
    stop_event=None,
    allow_legacy_fallback=True,
    streaming_result=None,
):
    """Speak an iterable without releasing the output stream between phrases."""
    speaking=False
    seen=[]
    barge_result=None
    runtime_stop=AnyStopEvent(
        controller.stop_event if controller is not None else None,
        controller.maintenance_interrupt_event if controller is not None else None,
        stop_event,
    )

    def report_first_audio(seconds):
        mqtt_pub("tts/first_audio",f"{seconds:.3f}")
        _health_latency("tts_first_audio",seconds)
        if first_audio is not None:
            first_audio(seconds)

    def marked_phrases():
        nonlocal speaking
        for phrase in phrases:
            phrase=str(phrase).strip()
            if not phrase: continue
            seen.append(phrase)
            if not speaking:
                speaking=True
                mqtt_pub("speaking","ON")
            yield phrase

    with _speech_lock:
        marked=marked_phrases()
        try:
            if _speech_engine is not None:
                monitor=_start_barge_in_monitor()
                metrics=None
                streaming_complete=False
                streaming_aborted=False
                try:
                    stop_signal=AnyStopEvent(
                        runtime_stop,
                        monitor.interrupt_event if monitor is not None else None,
                    )
                    metrics=_speech_engine.speak_phrases(
                        marked,
                        stop_event=stop_signal,
                        first_audio=report_first_audio,
                        phrase_started=(
                            monitor.set_expected_speech if monitor is not None else None
                        ),
                    )
                    streaming_complete=True
                except SpeechEngineError as e:
                    print("[TTS streaming]",e)
                    _set_legacy_speech_health(f"streaming playback failed: {e}")
                    if e.audio_started or (controller is not None and controller.stop_event.is_set()):
                        if abort is not None: abort()
                        streaming_aborted=True
                finally:
                    if monitor is not None:
                        barge_result=_finish_barge_in_monitor(monitor)

                if streaming_aborted:
                    return barge_result
                if streaming_complete:
                    if streaming_result is not None:
                        streaming_result(metrics)
                    _health_set("speech",ComponentState.READY,"warm streaming Piper",True)
                    mqtt_pub("tts/speak_time",f"{metrics.total_seconds:.3f}")
                    mqtt_pub("tts/audio_time",f"{metrics.audio_seconds:.3f}")
                    mqtt_pub("tts/cache_hit","ON" if metrics.cached_phrases else "OFF")
                    if metrics.interrupted and abort is not None: abort()
                    return barge_result
                if barge_result is not None:
                    if abort is not None: abort()
                    return barge_result
                if not allow_legacy_fallback:
                    if abort is not None: abort()
                    return barge_result
                mqtt_pub("tts/cache_hit","OFF")
                for phrase in seen:
                    if runtime_stop.is_set(): break
                    _legacy_speak_with_jaw(phrase,runtime_stop)
            if _speech_engine is None and not allow_legacy_fallback:
                if abort is not None: abort()
                return barge_result
            mqtt_pub("tts/cache_hit","OFF")
            for phrase in marked:
                if runtime_stop.is_set(): break
                _legacy_speak_with_jaw(phrase,runtime_stop)
        finally:
            _jaw_set(JAW_REST_FRAC)
            if speaking:
                mqtt_pub("speaking","OFF")

def speak_with_jaw(
    text:str,
    first_audio=None,
    stop_event=None,
    allow_legacy_fallback=True,
    streaming_result=None,
):
    if not text: return None
    return speak_phrases_with_jaw(
        [text],
        first_audio=first_audio,
        stop_event=stop_event,
        allow_legacy_fallback=allow_legacy_fallback,
        streaming_result=streaming_result,
    )

def _publish_llm_metrics(result):
    if result is None: return
    metrics=result.metrics
    mqtt_pub("llm/first_token",f"{metrics.first_token_seconds:.3f}")
    mqtt_pub("llm/first_phrase",f"{metrics.first_phrase_seconds:.3f}")
    mqtt_pub("llm/reply_time",f"{metrics.total_seconds:.3f}")
    mqtt_pub("llm/phrase_count",str(metrics.phrases_emitted))
    _health_latency("llm_reply",metrics.total_seconds)

def _publish_memory_turns(memory):
    mqtt_pub("llm/memory_turns",str(memory.turn_count if memory is not None else 0))

def stream_llm_reply(user_text:str,memory=None):
    fallback=LLM_FALLBACK_LINE
    if _llm_client is None:
        controller.set_state(RuntimeState.SPEAKING)
        return fallback,speak_with_jaw(fallback)

    history=memory.messages() if memory is not None else None
    foreground_stop=AnyStopEvent(
        controller.stop_event,
        controller.maintenance_interrupt_event,
    )
    reply=_llm_client.start_reply(user_text,foreground_stop,history=history)
    delivered=[]

    def phrases():
        for phrase in reply:
            if controller is not None: controller.heartbeat()
            if not delivered:
                controller.set_state(RuntimeState.SPEAKING)
            delivered.append(phrase)
            yield phrase

    def first_audio_started(_tts_seconds):
        elapsed=time.monotonic()-reply.started_at
        mqtt_pub("llm/first_audio",f"{elapsed:.3f}")
        _health_latency("response_first_audio",elapsed)

    try:
        barge_result=speak_phrases_with_jaw(
            phrases(),
            first_audio=first_audio_started,
            abort=reply.cancel,
        )
    except Exception as e:
        print("[LLM speech pipeline]",e)
        reply.cancel()
        barge_result=None

    result=reply.result or reply.wait(timeout=0.25)
    _publish_llm_metrics(result)
    if result is not None and result.error:
        print("[LLM]",result.error)
        _health_set("ollama",ComponentState.DEGRADED,f"reply failed: {result.error}")
    elif result is not None and not result.metrics.interrupted:
        _health_set("ollama",ComponentState.READY,f"{OLLAMA_MODEL} responding")

    if foreground_stop.is_set():
        reply.cancel()
        return (result.text if result is not None else ""),barge_result

    if barge_result is not None:
        reply.cancel()
        result=reply.result or reply.wait(timeout=0.25)
        completed=(result.text if result is not None else " ".join(delivered)).strip()
        return completed,barge_result

    if not delivered:
        controller.set_state(RuntimeState.SPEAKING)
        return fallback,speak_with_jaw(fallback)

    completed=(result.text if result is not None else " ".join(delivered)).strip()
    if memory is not None and memory.remember_reply(user_text,result):
        _publish_memory_turns(memory)
    return completed,None

stt_enabled=True; _VOSK_MODEL=None
if vosk is None:
    stt_enabled=False; print("[stt] disabled (vosk not available)")
else:
    try:
        if not os.path.isdir(MODEL_PATH): raise RuntimeError(f"model path missing: {MODEL_PATH}")
        _VOSK_MODEL=vosk.Model(MODEL_PATH)
    except Exception as e:
        stt_enabled=False; print(f"[stt] disabled: {e}")

def pick_input_device():
    if sd is None: return None,"no-sounddevice"
    try:
        for idx,d in enumerate(sd.query_devices()):
            if d.get("max_input_channels",0)>0: return idx,d.get("name","mic")
    except Exception as e: print("[audio]",e)
    return None,"none"
in_idx,in_name=pick_input_device()
if in_idx is None: stt_enabled=False; print("[audio] listening disabled (no input device)")
else: print(f"Mic selected: {in_name}")

def _recognized_text(result_json):
    try:
        txt=(json.loads(result_json).get("text") or "").strip()
    except Exception:
        return ""
    return txt if txt and len(txt.split())>=MIN_TEXT_LEN else ""

def record_once(input_index:int,capture_rate:int,timeout_s:float,stop_event=None)->str:
    if not stt_enabled or sd is None or _VOSK_MODEL is None: return ""
    rec=vosk.KaldiRecognizer(_VOSK_MODEL,VOSK_RATE); rec.SetWords(True)
    q=queue.Queue(maxsize=128); rs_state={"previous":np.zeros(0,np.int16),"phase":0.0}
    gate=SpeechGate(
        sample_rate=VOSK_RATE,
        energy_threshold=ENERGY_GATE,
        preroll_seconds=PREROLL_SEC,
        minimum_voiced_seconds=MIN_VOICED_SEC,
        end_silence_seconds=END_SILENCE_SEC,
    )
    start_deadline=time.monotonic()+timeout_s
    utterance_deadline=None
    def cb(indata,frames,time_info,status):
        try: q.put_nowait(bytes(indata))
        except queue.Full: _health_increment("audio_dropped_frames")
    try:
        stream=sd.RawInputStream(samplerate=capture_rate,blocksize=SD_BLOCKSIZE,device=input_index,dtype="int16",channels=1,callback=cb)
        with stream:
            while True:
                now=time.monotonic()
                if controller is not None: controller.heartbeat()
                if stop_event is not None and stop_event.is_set(): return ""
                if not gate.speaking and now>=start_deadline: break
                if gate.speaking and (
                    (utterance_deadline is not None and now>=utterance_deadline)
                    or gate.silence_complete(now)
                ):
                    break
                try: data=q.get(timeout=0.10)
                except queue.Empty: continue
                chunk=np.frombuffer(data,dtype=np.int16)
                rs=resample_linear_int16(chunk,capture_rate,VOSK_RATE,rs_state)
                gated=gate.process(rs,now)
                if gated.speech_started:
                    utterance_deadline=now+MAX_UTTERANCE_SEC
                if gated.audio.size and rec.AcceptWaveform(gated.audio.tobytes()):
                    txt=_recognized_text(rec.Result())
                    if txt: return txt
    except Exception as e:
        print(f"[audio] capture failed: {e}")
        _health_set("microphone",ComponentState.FAILED,f"capture failed: {e}")
        return ""
    return _recognized_text(rec.FinalResult())

motion_enabled=True; motion_count=0
_idle_life=None; _idle_life_count=0; _idle_life_interrupted=0
_motion_timer=None; _motion_timer_lock=threading.Lock()

def _cancel_motion_timer():
    global _motion_timer
    with _motion_timer_lock:
        if _motion_timer is not None:
            _motion_timer.cancel()
            _motion_timer=None

def _confirm_motion():
    global _motion_timer
    with _motion_timer_lock:
        _motion_timer=None
    if (
        motion_enabled
        and not _maintenance_stop_requested()
        and getattr(pir,"motion_detected",False)
    ):
        _enqueue(EventKind.TRIGGER,source="pir")

def _schedule_motion_trigger():
    global _motion_timer
    with _motion_timer_lock:
        if _motion_timer is not None:
            _motion_timer.cancel()
        _motion_timer=threading.Timer(MOTION_HOLD_SEC,_confirm_motion)
        _motion_timer.daemon=True
        _motion_timer.start()

class _DummyPIR:
    motion_detected=False
    def close(self): pass
pir=None
if gpiozero is not None:
    try:
        pir=gpiozero.MotionSensor(PIR_PIN,queue_len=5,sample_rate=25,threshold=0.5)
        def _pir_on():
            global motion_count; motion_count+=1
            locked=_maintenance_stop_requested()
            if controller is not None and not locked:
                controller.interrupt_idle()
                controller.interrupt_scene()
                controller.interrupt_self_test()
                controller.interrupt_content_reload()
            mqtt_pub("motion","ON"); mqtt_pub("motion/count",str(motion_count))
            if not locked: _schedule_motion_trigger()
        def _pir_off():
            mqtt_pub("motion","OFF"); _cancel_motion_timer()
        pir.when_motion=_pir_on; pir.when_no_motion=_pir_off; print("PIR ready")
    except Exception as e: print("[pir]",e)
if pir is None:
    pir=_DummyPIR(); print("[pir] disabled; using dummy")

if busio and pca9685_mod and ada_servo_mod and board:
    try:
        i2c=busio.I2C(board.SCL,board.SDA)
        _pca=pca9685_mod.PCA9685(i2c); _pca.frequency=PCA_FREQ
        _eyes_ch=_pca.channels[EYES_CH]
        _jaw=ada_servo_mod.Servo(_pca.channels[JAW_CH],min_pulse=JAW_MIN_US,max_pulse=JAW_MAX_US)
    except Exception as e:
        print("[PCA]",e); _pca=None; _eyes_ch=None; _jaw=None
else:
    print("[PCA] libs unavailable; eyes/jaw disabled")

def eyes_safe_off_startup():
    try:
        if _eyes_ch is not None:
            _eyes_ch.duty_cycle=int(0xFFFF*(0.0 if not EYES_INVERT else 1.0))
    except Exception as e: print("[eyes startup off]",e)
eyes_safe_off_startup()
if '_jaw' in globals() and _jaw:
    try: _jaw.fraction=JAW_REST_FRAC
    except Exception as e: print("[jaw init]",e)

def publish_mqtt_discovery():
    for topic,payload in discovery_messages(DEVICE_NAME,_personality_names()):
        mqtt_pub_abs(topic,"" if payload is None else json.dumps(payload),retain=True)

night_mode=False; _day={"listen":None,"speak":None,"vol":None}

def _publish_operator_controls():
    mqtt_pub("eyes/dim",str(int(round(100*EYES_LISTEN_FRAC))),retain=True)
    mqtt_pub("eyes/full",str(int(round(100*EYES_SPEAK_FRAC))),retain=True)
    mqtt_pub("volume",str(int(round(100*VOLUME))),retain=True)
    mqtt_pub("motion/enabled","ON" if motion_enabled else "OFF",retain=True)
    mqtt_pub("idle_life/enabled","ON" if IDLE_LIFE_ENABLED else "OFF",retain=True)
    mqtt_pub("night_mode","ON" if night_mode else "OFF",retain=True)

def _publish_maintenance_state():
    mqtt_pub("maintenance/enabled","ON" if maintenance_mode else "OFF",retain=True)
    mqtt_pub("maintenance/state",_maintenance_state,retain=True)
    mqtt_pub("maintenance/last_result",_maintenance_last_result,retain=True)
    mqtt_pub("maintenance/last_error",_maintenance_last_error,retain=True)
    mqtt_pub("maintenance/since",_maintenance_since,retain=True)
    mqtt_pub("maintenance/rejected_count",str(_maintenance_rejected_count),retain=True)

def _publish_settings_state():
    mqtt_pub("settings/state",_settings_state,retain=True)
    mqtt_pub("settings/last_saved",_settings_last_saved,retain=True)
    mqtt_pub("settings/last_error",_settings_last_error,retain=True)
    mqtt_pub("settings/save_count",str(_settings_save_count),retain=True)

def _day_profile():
    if night_mode:
        return DayProfile(
            eyes_dim=EYES_LISTEN_FRAC if _day["listen"] is None else _day["listen"],
            eyes_full=EYES_SPEAK_FRAC if _day["speak"] is None else _day["speak"],
            volume=VOLUME if _day["vol"] is None else _day["vol"],
        )
    return DayProfile(
        eyes_dim=EYES_LISTEN_FRAC,
        eyes_full=EYES_SPEAK_FRAC,
        volume=VOLUME,
    )

def _current_operator_settings():
    return OperatorSettings(
        personality=_personality_active_name(),
        motion_enabled=bool(motion_enabled),
        idle_life_enabled=bool(IDLE_LIFE_ENABLED),
        night_mode=bool(night_mode),
        eyes_dim=clamp(float(EYES_LISTEN_FRAC),0.0,1.0),
        eyes_full=clamp(float(EYES_SPEAK_FRAC),0.0,1.0),
        volume=clamp(float(VOLUME),0.0,2.0),
        day_profile=_day_profile(),
        maintenance_mode=bool(maintenance_mode),
    )

def _apply_restored_settings(settings):
    global PERSONALITY_REQUESTED,motion_enabled,IDLE_LIFE_ENABLED,night_mode
    global maintenance_mode
    global EYES_LISTEN_FRAC,EYES_SPEAK_FRAC,VOLUME,_day
    PERSONALITY_REQUESTED=settings.personality
    motion_enabled=settings.motion_enabled
    IDLE_LIFE_ENABLED=settings.idle_life_enabled
    night_mode=settings.night_mode
    maintenance_mode=settings.maintenance_mode
    EYES_LISTEN_FRAC=settings.eyes_dim
    EYES_SPEAK_FRAC=settings.eyes_full
    VOLUME=settings.volume
    _day={
        "listen":settings.day_profile.eyes_dim if night_mode else None,
        "speak":settings.day_profile.eyes_full if night_mode else None,
        "vol":settings.day_profile.volume if night_mode else None,
    }

def _init_persistent_settings():
    global _settings_store,_settings_loaded,_settings_state
    global _settings_last_saved,_settings_last_error
    if not PERSIST_SETTINGS_ENABLED:
        _settings_store=None; _settings_loaded=None
        _settings_state="disabled"; _settings_last_saved="never"; _settings_last_error="none"
        _health_set("settings",ComponentState.DISABLED,"disabled by configuration")
        _publish_settings_state()
        return
    _settings_store=OperatorSettingsStore(PERSIST_SETTINGS_PATH)
    try:
        settings=_settings_store.load()
        _settings_loaded=settings
        if settings is None:
            _settings_state="empty"; _settings_last_saved="never"; _settings_last_error="none"
            _health_set("settings",ComponentState.READY,"no saved operator override")
            print(f"[settings] no saved state at {PERSIST_SETTINGS_PATH}; using configured defaults")
        else:
            _apply_restored_settings(settings)
            _settings_state="restored"
            _settings_last_saved=settings.updated_at
            _settings_last_error="none"
            _health_set("settings",ComponentState.READY,f"restored {settings.updated_at}")
            print(f"[settings] restored operator state from {PERSIST_SETTINGS_PATH}")
    except SettingsConfigError as error:
        _settings_loaded=None
        _settings_state="error"; _settings_last_saved="never"; _settings_last_error=str(error)[:255]
        _health_set("settings",ComponentState.DEGRADED,str(error))
        print(f"[settings] {error}; using configured defaults")
    _publish_settings_state()

def _persist_operator_settings():
    global _settings_loaded,_settings_state,_settings_last_saved
    global _settings_last_error,_settings_save_count
    if _settings_store is None: return False
    try:
        saved=_settings_store.save(_current_operator_settings())
        _settings_loaded=saved
        _settings_state="saved"; _settings_last_saved=saved.updated_at
        _settings_last_error="none"; _settings_save_count+=1
        _health_set("settings",ComponentState.READY,f"saved {saved.updated_at}")
        _publish_settings_state()
        return True
    except Exception as error:
        _settings_state="error"; _settings_last_error=str(error)[:255]
        _health_set("settings",ComponentState.DEGRADED,str(error))
        _publish_settings_state()
        print(f"[settings] save failed: {error}")
        return False

def _set_eyes_dim(v):
    global EYES_LISTEN_FRAC
    if not math.isfinite(float(v)): return
    EYES_LISTEN_FRAC=clamp(v,0,1); eyes_listen()
    mqtt_pub("eyes/dim",str(int(round(100*EYES_LISTEN_FRAC))),retain=True)
    _persist_operator_settings()
def _set_eyes_full(v):
    global EYES_SPEAK_FRAC
    if not math.isfinite(float(v)): return
    EYES_SPEAK_FRAC=clamp(v,0,1); eyes_speak()
    mqtt_pub("eyes/full",str(int(round(100*EYES_SPEAK_FRAC))),retain=True)
    _persist_operator_settings()
def _set_volume(v):
    global VOLUME
    if not math.isfinite(float(v)): return
    VOLUME=clamp(v,0,2)
    mqtt_pub("volume",str(int(round(100*VOLUME))),retain=True)
    _persist_operator_settings()
def _set_motion_enabled(payload):
    global motion_enabled; motion_enabled=str(payload).lower() in ("on","true","1","yes")
    if not motion_enabled: _cancel_motion_timer()
    mqtt_pub("motion/enabled","ON" if motion_enabled else "OFF",retain=True)
    _publish_idle_life_ready_state()
    _persist_operator_settings()
def _set_idle_life_enabled(payload):
    global IDLE_LIFE_ENABLED
    IDLE_LIFE_ENABLED=str(payload).lower() in ("on","true","1","yes")
    if _idle_life is not None: _idle_life.set_enabled(IDLE_LIFE_ENABLED)
    _publish_idle_life_ready_state()
    _persist_operator_settings()
def _toggle_night_mode(payload):
    global night_mode,EYES_LISTEN_FRAC,EYES_SPEAK_FRAC,VOLUME,_day
    want=str(payload).lower() in ("on","true","1","yes")
    if want and not night_mode:
        _day["listen"],_day["speak"],_day["vol"]=EYES_LISTEN_FRAC,EYES_SPEAK_FRAC,VOLUME
        EYES_LISTEN_FRAC=clamp(EYES_LISTEN_FRAC*0.35,0,1); EYES_SPEAK_FRAC=clamp(EYES_SPEAK_FRAC*0.6,0,1); VOLUME=clamp(VOLUME*0.6,0,2)
        night_mode=True
    elif not want and night_mode:
        if _day["listen"] is not None: EYES_LISTEN_FRAC=_day["listen"]
        if _day["speak"]  is not None: EYES_SPEAK_FRAC=_day["speak"]
        if _day["vol"]    is not None: VOLUME=_day["vol"]
        night_mode=False
    _publish_operator_controls()
    _persist_operator_settings()

def _init_maintenance_mode():
    global _maintenance_state,_maintenance_last_result,_maintenance_last_error
    global _maintenance_since
    if controller is not None:
        controller.set_maintenance_active(maintenance_mode)
    if maintenance_mode:
        _cancel_motion_timer()
        _stop_eyes_effect()
        _jaw_set(JAW_REST_FRAC)
        eyes_off()
        _maintenance_state="locked"
        _maintenance_last_result="restored" if _settings_loaded is not None else "configured"
        _health_set("maintenance",ComponentState.DISABLED,"operator lockout active")
    else:
        _maintenance_state="ready"
        _maintenance_last_result="ready"
        _health_set("maintenance",ComponentState.READY,"outputs unlocked")
    _maintenance_last_error="none"
    _maintenance_since=datetime.now(timezone.utc).isoformat(timespec="seconds")
    _publish_maintenance_state()

def _maintenance_value(payload):
    if isinstance(payload,bool): return payload
    value=str(payload).strip().lower()
    if value in ("on","true","1","yes"): return True
    if value in ("off","false","0","no"): return False
    return None

def _request_maintenance_mode(payload):
    global _maintenance_state,_maintenance_last_result,_maintenance_last_error
    want=_maintenance_value(payload)
    if want is None:
        _record_maintenance_result(
            "error",
            "maintenance value must be ON or OFF",
        )
        return False
    if controller is None:
        _record_maintenance_result("not_ready","controller is not ready")
        return False
    if not controller.request_maintenance(want,"mqtt"):
        _record_maintenance_result("error","controller rejected maintenance request")
        return False
    _maintenance_state="locking" if want else "unlocking"
    _maintenance_last_result="queued"
    _maintenance_last_error="none"
    _publish_maintenance_state()
    return True

def _set_maintenance_mode(payload):
    global maintenance_mode,_maintenance_state,_maintenance_last_result
    global _maintenance_last_error,_maintenance_since
    want=_maintenance_value(payload)
    if want is None:
        _record_maintenance_result("error","maintenance value must be ON or OFF")
        return
    changed=want != maintenance_mode
    maintenance_mode=want
    if controller is not None:
        controller.set_maintenance_active(want)
    _cancel_motion_timer()
    _stop_eyes_effect()
    _jaw_set(JAW_REST_FRAC)
    if want:
        eyes_off()
        _maintenance_state="locked"
        _maintenance_last_result="locked" if changed else "unchanged"
        _health_set("maintenance",ComponentState.DISABLED,"operator lockout active")
    else:
        eyes_idle()
        _maintenance_state="ready"
        _maintenance_last_result="unlocked" if changed else "unchanged"
        _health_set("maintenance",ComponentState.READY,"outputs unlocked")
    _maintenance_last_error="none"
    _maintenance_since=datetime.now(timezone.utc).isoformat(timespec="seconds")
    _publish_operator_controls()
    _publish_maintenance_state()
    _publish_idle_life_ready_state()
    _publish_scene_ready_state()
    _publish_self_test_state()
    _publish_barge_in_capability()
    if changed:
        saved=_persist_operator_settings()
        if not saved:
            _maintenance_last_result=(
                "locked_unsaved" if want else "unlocked_unsaved"
            )
            _maintenance_last_error=(
                _settings_last_error
                if _settings_store is not None
                else "settings persistence disabled; restart uses configured default"
            )
            _publish_maintenance_state()

_transcript=None
def _transcript_start():
    global _transcript; _transcript={"startts":time.time(),"utterances":[]}
def _transcript_add(role,text):
    try:
        if _transcript is not None: _transcript["utterances"].append({"t":time.time(),"role":role,"text":text})
    except: pass
def _who(role):
    return {'assistant':'Skeleton','user':'Visitor','command':'Command'}.get(role, role or 'Unknown')
def _transcript_pretty():
    try:
        lines=[]
        for u in (_transcript.get('utterances') or []):
            who=_who(u.get('role')); txt=u.get('text','')
            lines.append(f'**{who}**: "{txt}"')
        return "\n".join(lines)
    except Exception: return ""
def _transcript_publish_and_clear():
    global _transcript
    try:
        if _transcript:
            _transcript["endts"]=time.time()
            pretty=_transcript_pretty()
            if pretty: mqtt_pub("transcript",pretty)
    except Exception as e: print("[transcript]",e)
    _transcript=None

def _init_health_monitor():
    global _health
    _health=RuntimeHealthMonitor(
        publisher=_publish_health_snapshot,
        interval_seconds=HEALTH_INTERVAL_SEC,
        latency_window=HEALTH_LATENCY_WINDOW,
        temperature_warning_c=HEALTH_TEMP_WARN_C,
        temperature_critical_c=HEALTH_TEMP_CRITICAL_C,
        disk_warning_percent=HEALTH_DISK_WARN_PERCENT,
        disk_critical_percent=HEALTH_DISK_CRITICAL_PERCENT,
    )
    _health.set_component("runtime",ComponentState.STARTING,"startup checks",True,publish=False)
    _health.set_component("system",ComponentState.STARTING,"telemetry pending",True,publish=False)
    _health.set_component("mqtt",ComponentState.STARTING,"connection pending",False,publish=False)
    _health.set_component("speech",ComponentState.STARTING,"Piper warmup pending",True,publish=False)
    _health.set_component("ollama",ComponentState.STARTING,"model warmup pending",False,publish=False)
    _health.set_component(
        "personality",
        ComponentState.STARTING if PERSONALITIES_ENABLED else ComponentState.DISABLED,
        "personality file pending" if PERSONALITIES_ENABLED else "disabled by configuration",
        False,
        publish=False,
    )
    _health.set_component(
        "scenes",
        ComponentState.STARTING if SCENES_ENABLED else ComponentState.DISABLED,
        "scene file pending" if SCENES_ENABLED else "disabled by configuration",
        False,
        publish=False,
    )
    _health.set_component(
        "microphone",
        ComponentState.READY if stt_enabled and in_idx is not None else ComponentState.FAILED,
        in_name if stt_enabled and in_idx is not None else "Vosk or input device unavailable",
        False,
        publish=False,
    )
    _health.set_component(
        "animation",
        ComponentState.READY if _pca is not None and _eyes_ch is not None and _jaw is not None else ComponentState.FAILED,
        "PCA9685, eyes, and jaw ready" if _pca is not None and _eyes_ch is not None and _jaw is not None else "PCA9685 animation unavailable",
        False,
        publish=False,
    )
    _health.set_component(
        "motion",
        ComponentState.FAILED if isinstance(pir,_DummyPIR) else ComponentState.READY,
        "PIR unavailable; MQTT trigger remains available" if isinstance(pir,_DummyPIR) else "PIR ready",
        False,
        publish=False,
    )
    _health.set_component(
        "self_test",
        ComponentState.STARTING if SELF_TEST_ENABLED else ComponentState.DISABLED,
        "operator self-test pending" if SELF_TEST_ENABLED else "disabled by configuration",
        False,
        publish=False,
    )
    _health.set_component(
        "content_reload",
        ComponentState.STARTING if CONTENT_RELOAD_ENABLED else ComponentState.DISABLED,
        "live content reload pending" if CONTENT_RELOAD_ENABLED else "disabled by configuration",
        False,
        publish=False,
    )
    _health.set_component(
        "maintenance",
        ComponentState.STARTING,
        "operator lockout state pending",
        False,
        publish=False,
    )
    if OLLAMA_HEALTHCHECK_ENABLED and requests is not None:
        _health.add_probe("ollama",_probe_ollama,critical=False)

def _ollama_health_url():
    parsed=urlsplit(OLLAMA_URL)
    return urlunsplit((parsed.scheme,parsed.netloc,"/api/tags","",""))

def _probe_ollama():
    if requests is None:
        return ComponentState.FAILED,"requests unavailable"
    response=requests.get(_ollama_health_url(),timeout=(1,2))
    response.raise_for_status()
    data=response.json()
    models=data.get("models",[]) if isinstance(data,dict) else []
    names={
        str(item.get("name") or item.get("model") or "")
        for item in models
        if isinstance(item,dict)
    }
    if OLLAMA_MODEL not in names:
        return ComponentState.DEGRADED,f"Ollama ready; {OLLAMA_MODEL} not listed"
    return ComponentState.READY,f"{OLLAMA_MODEL} ready"

def _do_mqtt_connect():
    if mqttc is not None:
        mqtt_connect()
        mqtt_pub("llm/memory_turns","0")
        if not _mqtt_connected:
            _health_set("mqtt",ComponentState.FAILED,"connection unavailable")
    else:
        _health_set("mqtt",ComponentState.FAILED,"MQTT client unavailable")

def _warm_ollama():
    if not requests:
        _health_set("ollama",ComponentState.FAILED,"requests unavailable")
        return False
    try:
        response=requests.post(OLLAMA_URL,json={"model":OLLAMA_MODEL,"messages":[{"role":"user","content":"warming"}],"stream":False,
                                       "keep_alive":KEEP_ALIVE,"options":{"num_predict":1}},timeout=(2,10))
        response.raise_for_status()
        _health_set("ollama",ComponentState.READY,f"{OLLAMA_MODEL} warm")
        return True
    except Exception as e:
        print("[ollama warmup]",e)
        _health_set("ollama",ComponentState.FAILED,f"warmup failed: {e}")
        return False

def _init_llm_client():
    global _llm_client
    if requests is None:
        print("[LLM] requests unavailable; using spoken fallback")
        _health_set("ollama",ComponentState.FAILED,"requests unavailable")
        return
    _llm_client=OllamaStreamingClient(
        http_client=requests,
        url=OLLAMA_URL,
        model=OLLAMA_MODEL,
        system_prompt=SYSTEM_PROMPT,
        keep_alive=KEEP_ALIVE,
        options=OLLAMA_OPTS,
        timeout=OLLAMA_TIMEOUT,
        minimum_phrase_chars=LLM_PHRASE_MIN_CHARS,
        soft_phrase_chars=LLM_PHRASE_SOFT_CHARS,
        maximum_phrase_chars=LLM_PHRASE_MAX_CHARS,
    )

def _build_llm_client_for_pack(pack):
    if requests is None: return None
    reply=pack.reply
    options={
        "num_predict":reply.maximum_tokens,
        "num_thread":4,
        "temperature":reply.temperature,
        "repeat_penalty":reply.repeat_penalty,
        "num_ctx":reply.context_tokens,
    }
    return OllamaStreamingClient(
        http_client=requests,
        url=OLLAMA_URL,
        model=OLLAMA_MODEL,
        system_prompt=pack.system_prompt,
        keep_alive=KEEP_ALIVE,
        options=options,
        timeout=OLLAMA_TIMEOUT,
        minimum_phrase_chars=reply.phrase_minimum,
        soft_phrase_chars=reply.phrase_soft,
        maximum_phrase_chars=reply.phrase_maximum,
    )

def _build_barge_in_matcher_for_pack(pack):
    return BargeInMatcher(
        stop_commands=pack.barge_in.stop_commands,
        listen_commands=pack.barge_in.listen_commands,
        wake_words=pack.barge_in.wake_words,
        require_wake_word=pack.barge_in.require_wake_word,
    )

def _build_idle_life_for_pack(pack):
    return IdleLifeScheduler(
        minimum_interval=IDLE_LIFE_MIN_SEC,
        maximum_interval=IDLE_LIFE_MAX_SEC,
        mutter_chance=IDLE_MUTTER_CHANCE,
        mutter_lines=pack.idle_lines,
        enabled=IDLE_LIFE_ENABLED,
    )

def _apply_personality_globals(pack):
    global SYSTEM_PROMPT,MORNING_LINES,AFTERNOON_LINES,EVENING_LINES,NIGHT_LINES
    global GOODBYE_LINES,IDLE_LINES,LLM_FALLBACK_LINE,LLM_MEMORY_TURNS
    global LLM_CONTEXT_TOKENS,LLM_MAXIMUM_TOKENS,LLM_TEMPERATURE
    global LLM_REPEAT_PENALTY,LLM_PHRASE_MIN_CHARS,LLM_PHRASE_SOFT_CHARS
    global LLM_PHRASE_MAX_CHARS,OLLAMA_OPTS,PERSONALITY_VOLUME_MULTIPLIER
    global BARGE_IN_STOP_COMMANDS,BARGE_IN_LISTEN_COMMANDS,BARGE_IN_WAKE_WORDS
    global BARGE_IN_REQUIRE_WAKE_WORD
    SYSTEM_PROMPT=pack.system_prompt
    MORNING_LINES=list(pack.opening_lines["morning"])
    AFTERNOON_LINES=list(pack.opening_lines["afternoon"])
    EVENING_LINES=list(pack.opening_lines["evening"])
    NIGHT_LINES=list(pack.opening_lines["night"])
    GOODBYE_LINES=list(pack.goodbye_lines)
    IDLE_LINES=list(pack.idle_lines)
    LLM_FALLBACK_LINE=pack.fallback_line
    LLM_MEMORY_TURNS=pack.reply.memory_turns
    LLM_CONTEXT_TOKENS=pack.reply.context_tokens
    LLM_MAXIMUM_TOKENS=pack.reply.maximum_tokens
    LLM_TEMPERATURE=pack.reply.temperature
    LLM_REPEAT_PENALTY=pack.reply.repeat_penalty
    LLM_PHRASE_MIN_CHARS=pack.reply.phrase_minimum
    LLM_PHRASE_SOFT_CHARS=pack.reply.phrase_soft
    LLM_PHRASE_MAX_CHARS=pack.reply.phrase_maximum
    OLLAMA_OPTS={
        "num_predict":LLM_MAXIMUM_TOKENS,
        "num_thread":4,
        "temperature":LLM_TEMPERATURE,
        "repeat_penalty":LLM_REPEAT_PENALTY,
        "num_ctx":LLM_CONTEXT_TOKENS,
    }
    PERSONALITY_VOLUME_MULTIPLIER=pack.voice.volume_multiplier
    BARGE_IN_STOP_COMMANDS=pack.barge_in.stop_commands
    BARGE_IN_LISTEN_COMMANDS=pack.barge_in.listen_commands
    BARGE_IN_WAKE_WORDS=pack.barge_in.wake_words
    BARGE_IN_REQUIRE_WAKE_WORD=pack.barge_in.require_wake_word

def _personality_ready_state():
    if not PERSONALITIES_ENABLED: return "disabled"
    if _personality_library is None: return "error"
    if _personality_active is None: return "starting"
    return "ready"

def _publish_personality_state():
    names=list(_personality_names())
    metadata=(
        _personality_library.metadata()
        if _personality_library is not None
        else {"names":names,"active_default":"legacy","personalities":{}}
    )
    mqtt_pub("personality/state",_personality_ready_state(),retain=True)
    mqtt_pub("personality/active",_personality_active_name(),retain=True)
    mqtt_pub("personality/library_count",str(len(names)),retain=True)
    mqtt_pub("personality/library",json.dumps(metadata),retain=True)
    default_scene=(
        _personality_active.default_scene
        if _personality_active is not None and _personality_active.default_scene
        else "none"
    )
    mqtt_pub("personality/default_scene",default_scene,retain=True)
    mqtt_pub("personality/switch_count",str(_personality_switch_count),retain=True)
    mqtt_pub("personality/last_result",_personality_last_result,retain=True)
    mqtt_pub("personality/last_error",_personality_last_error,retain=True)

def _record_personality_result(result,error="none"):
    global _personality_last_result,_personality_last_error
    _personality_last_result=str(result)
    _personality_last_error=str(error)[:255]
    mqtt_pub("personality/last_result",_personality_last_result,retain=True)
    mqtt_pub("personality/last_error",_personality_last_error,retain=True)

def _init_personality_library():
    global _personality_library,_personality_active,_personality_load_error
    global _personality_last_result,_personality_last_error,_barge_in_matcher
    if not PERSONALITIES_ENABLED:
        _personality_library=None; _personality_active=None; _personality_load_error=""
        _personality_last_result="disabled"; _personality_last_error="none"
        _health_set("personality",ComponentState.DISABLED,"disabled by configuration")
        _publish_personality_state()
        return
    try:
        library=PersonalityLibrary.load(PERSONALITIES_PATH)
        warning=""
        try:
            selected=library.select(PERSONALITY_REQUESTED or None)
        except PersonalityConfigError as error:
            warning=str(error)
            selected=library.select()
        _personality_library=library
        _personality_active=selected
        _personality_load_error=warning
        _apply_personality_globals(selected)
        _barge_in_matcher=_build_barge_in_matcher_for_pack(selected)
        _personality_last_result="fallback" if warning else "loaded"
        _personality_last_error=warning or "none"
        state=ComponentState.DEGRADED if warning else ComponentState.READY
        detail=warning or f"{selected.name}; {len(library)} packs loaded"
        _health_set("personality",state,detail)
        print(f"[personality] {selected.name} active; {len(library)} packs from {PERSONALITIES_PATH}")
        if warning: print(f"[personality] {warning}; using {selected.name}")
        if _settings_loaded is not None and _settings_loaded.personality != selected.name:
            _persist_operator_settings()
    except PersonalityConfigError as error:
        _personality_library=None; _personality_active=None
        _personality_load_error=str(error)
        _personality_last_result="error"; _personality_last_error=str(error)
        print(f"[personality] {error}; using legacy prompt configuration")
        _health_set("personality",ComponentState.DEGRADED,str(error))
    _publish_personality_state()

def _validate_personality_scenes():
    if _personality_library is None or _scene_library is None: return
    errors=_personality_library.validate_scenes(_scene_library.names)
    if errors:
        detail="; ".join(errors)
        print(f"[personality] {detail}")
        _health_set("personality",ComponentState.DEGRADED,detail)

def _publish_personality_cache(metrics):
    cache_state="ready" if metrics.failed_entries == 0 else "partial"
    mqtt_pub("tts/cache_state",cache_state,retain=True)
    mqtt_pub("tts/cache_entries",str(metrics.total_entries),retain=True)
    mqtt_pub("tts/cache_warmup_time",f"{metrics.warmup_seconds:.3f}",retain=True)
    mqtt_pub("tts/cache_memory_kb",f"{metrics.pcm_bytes/1024.0:.1f}",retain=True)
    for error in metrics.errors:
        print(f"[TTS cache] skipped {error}")

def _switch_personality(name):
    global _personality_active,_personality_switch_count,_personality_last_result
    global _personality_last_error,_llm_client,_barge_in_matcher,_idle_life
    if _personality_library is None:
        error=_personality_load_error or "personality library is unavailable"
        _personality_last_result="error"; _personality_last_error=error
        _publish_personality_state(); return
    try:
        pack=_personality_library.select(name)
        if _personality_active is not None and pack.name == _personality_active.name:
            _personality_last_result="unchanged"; _personality_last_error="none"
            _publish_personality_state(); return
        if (
            pack.default_scene
            and _scene_library is not None
            and _scene_library.get(pack.default_scene) is None
        ):
            raise PersonalityConfigError(
                f"{pack.name}: unknown default scene {pack.default_scene!r}"
            )

        mqtt_pub("personality/state","switching",retain=True)
        new_llm=_build_llm_client_for_pack(pack)
        new_matcher=_build_barge_in_matcher_for_pack(pack)
        new_idle=_build_idle_life_for_pack(pack)
        cache_metrics=None
        wanted_lines=_canned_speech_lines(pack)
        if _speech_engine is not None and TTS_CANNED_CACHE:
            mqtt_pub("tts/cache_state","warming",retain=True)
            cache_metrics=_speech_engine.cache_phrases(wanted_lines)

        _apply_personality_globals(pack)
        _personality_active=pack
        _llm_client=new_llm
        _barge_in_matcher=new_matcher
        _idle_life=new_idle
        if _speech_engine is not None and TTS_CANNED_CACHE:
            _speech_engine.retain_cached_phrases(wanted_lines)
            cache_metrics=replace(
                cache_metrics,
                total_entries=_speech_engine.cache_entries,
                pcm_bytes=_speech_engine.cache_pcm_bytes,
            )
            _publish_personality_cache(cache_metrics)
        _personality_switch_count+=1
        _personality_last_result="switched"; _personality_last_error="none"
        _health_set("personality",ComponentState.READY,f"{pack.name} active; {len(_personality_library)} packs loaded")
        _publish_memory_turns(None)
        _publish_barge_in_capability()
        _publish_idle_life_ready_state()
        _publish_personality_state()
        _persist_operator_settings()
        print(f"[personality] switched to {pack.name}")
    except Exception as error:
        _personality_last_result="error"; _personality_last_error=str(error)[:255]
        _health_set("personality",ComponentState.DEGRADED,str(error))
        if _speech_engine is not None and TTS_CANNED_CACHE:
            try:
                _speech_engine.retain_cached_phrases(_canned_speech_lines())
            except Exception: pass
        _publish_personality_state()
        print(f"[personality] switch failed: {error}")

def _request_personality_switch(name):
    requested=str(name or "").strip().lower()
    if not requested:
        _record_personality_result("error","personality name cannot be empty")
        return False
    if _personality_library is None and requested == "legacy":
        _record_personality_result("unchanged")
        return True
    if controller is None or not can_switch_personality(controller.state):
        state=controller.state.value if controller is not None else "starting"
        _record_personality_result("busy",f"cannot switch while runtime is {state}")
        return False
    accepted=_enqueue(EventKind.SET_PERSONALITY,requested)
    if not accepted:
        _record_personality_result("error","personality switch queue is full")
    return accepted

def _configured_output_device():
    if AUDIO_OUTPUT_DEVICE is None: return None
    try: return int(AUDIO_OUTPUT_DEVICE)
    except ValueError: return AUDIO_OUTPUT_DEVICE

def _legacy_piper_available():
    return os.path.isfile(PIPER_BIN) and os.access(PIPER_BIN,os.X_OK) and os.path.isfile(PIPER_MODEL)

def _set_legacy_speech_health(detail):
    if _legacy_piper_available():
        _health_set("speech",ComponentState.DEGRADED,f"legacy Piper: {detail}",True)
    else:
        _health_set("speech",ComponentState.FAILED,f"streaming and legacy Piper unavailable: {detail}",True)

def _init_speech_engine():
    global _speech_engine
    if sd is None:
        print("[TTS] sounddevice unavailable; using legacy Piper process")
        mqtt_pub("tts/engine","legacy",retain=True)
        mqtt_pub("tts/cache_state","legacy",retain=True)
        mqtt_pub("tts/cache_entries","0",retain=True)
        _set_legacy_speech_health("sounddevice unavailable")
        return
    started=time.monotonic()
    try:
        _speech_engine=PiperSpeechEngine.load(
            model_path=PIPER_MODEL,
            config_path=PIPER_CONFIG,
            audio_module=sd,
            jaw_set=_jaw_set,
            volume_getter=_speech_volume,
            rest_fraction=JAW_REST_FRAC,
            maximum_fraction=JAW_MAX_FRAC,
            output_device=_configured_output_device(),
            frame_ms=TTS_FRAME_MS,
        )
        loaded_at=time.monotonic()
        warmup_seconds=_speech_engine.warm_up()
        elapsed=loaded_at-started
        mqtt_pub("tts/engine","streaming",retain=True)
        _health_set("speech",ComponentState.READY,"warm streaming Piper",True)
        mqtt_pub("tts/model_load_time",f"{elapsed:.3f}",retain=True)
        mqtt_pub("tts/warmup_time",f"{warmup_seconds:.3f}",retain=True)
        if TTS_CANNED_CACHE:
            mqtt_pub("tts/cache_state","warming",retain=True)
            cache_metrics=_speech_engine.cache_phrases(_canned_speech_lines())
            cache_state="ready" if cache_metrics.failed_entries == 0 else "partial"
            mqtt_pub("tts/cache_state",cache_state,retain=True)
            mqtt_pub("tts/cache_entries",str(cache_metrics.total_entries),retain=True)
            mqtt_pub("tts/cache_warmup_time",f"{cache_metrics.warmup_seconds:.3f}",retain=True)
            mqtt_pub("tts/cache_memory_kb",f"{cache_metrics.pcm_bytes/1024.0:.1f}",retain=True)
            print(
                f"[TTS cache] {cache_metrics.total_entries}/"
                f"{cache_metrics.requested_entries} canned lines ready in "
                f"{cache_metrics.warmup_seconds:.3f}s "
                f"({cache_metrics.pcm_bytes/1024.0:.1f} KiB)"
            )
            for error in cache_metrics.errors:
                print(f"[TTS cache] skipped {error}")
        else:
            mqtt_pub("tts/cache_state","disabled",retain=True)
            mqtt_pub("tts/cache_entries","0",retain=True)
            mqtt_pub("tts/cache_warmup_time","0.000",retain=True)
            mqtt_pub("tts/cache_memory_kb","0.0",retain=True)
        print(
            f"[TTS] Piper voice warm and output stream ready in "
            f"{time.monotonic() - started:.3f}s"
        )
    except Exception as e:
        _speech_engine=None
        mqtt_pub("tts/engine","legacy",retain=True)
        mqtt_pub("tts/cache_state","legacy",retain=True)
        mqtt_pub("tts/cache_entries","0",retain=True)
        print(f"[TTS] warm engine unavailable; using legacy Piper process: {e}")
        _set_legacy_speech_health(str(e))

def _scene_ready_state():
    if _scene_active: return "running"
    if _maintenance_stop_requested(): return "locked"
    if not SCENES_ENABLED: return "disabled"
    if _scene_load_error: return "error"
    if _scene_library is None or _scene_runner is None: return "starting"
    if len(_scene_library) == 0: return "no_scenes"
    return "ready"

def _publish_scene_ready_state():
    mqtt_pub("scene/active","ON" if _scene_active else "OFF",retain=True)
    mqtt_pub("scene/state",_scene_ready_state(),retain=True)
    mqtt_pub("scene/current",_scene_current,retain=True)
    mqtt_pub("scene/step",_scene_step,retain=True)
    mqtt_pub("scene/count",str(_scene_count),retain=True)
    mqtt_pub("scene/interrupted",str(_scene_interrupted),retain=True)
    names=list(_scene_library.names) if _scene_library is not None else []
    mqtt_pub("scene/library_count",str(len(names)),retain=True)
    mqtt_pub(
        "scene/library",
        json.dumps({
            "names":names,
            "scenes":{
                name:{
                    "description":_scene_library.get(name).description,
                    "steps":len(_scene_library.get(name).steps),
                }
                for name in names
            },
        }),
        retain=True,
    )

def _scene_progress(scene,index,step):
    global _scene_current,_scene_step
    if controller is not None: controller.heartbeat()
    _scene_current=scene.name
    _scene_step=f"{index}/{len(scene.steps)}:{step.action.value}"
    mqtt_pub("scene/current",_scene_current,retain=True)
    mqtt_pub("scene/step",_scene_step,retain=True)

def _init_scene_engine():
    global _scene_library,_scene_runner,_scene_load_error
    if not SCENES_ENABLED:
        _scene_library=None; _scene_runner=None; _scene_load_error=""
        _health_set("scenes",ComponentState.DISABLED,"disabled by configuration")
        _publish_scene_ready_state()
        return
    try:
        _scene_library=SceneLibrary.load(SCENES_PATH)
        _scene_runner=SceneRunner(
            executor=_execute_scene_step,
            maximum_seconds=SCENE_MAX_SECONDS,
            progress=_scene_progress,
        )
        _scene_load_error=""
        state=ComponentState.READY if len(_scene_library) else ComponentState.DISABLED
        detail=f"{len(_scene_library)} scenes loaded" if len(_scene_library) else "no scenes configured"
        _health_set("scenes",state,detail)
        print(f"[scenes] {detail} from {SCENES_PATH}")
    except SceneConfigError as e:
        _scene_library=None; _scene_runner=None; _scene_load_error=str(e)
        print(f"[scenes] {e}")
        mqtt_pub("scene/last_error",str(e)[:255],retain=True)
        _health_set("scenes",ComponentState.DEGRADED,str(e))
    _publish_scene_ready_state()

def _scene_requires_streaming_speech():
    return bool(
        _scene_library is not None
        and any(
            step.action is SceneAction.SPEAK
            for scene_name in _scene_library.names
            for step in _scene_library.get(scene_name).steps
        )
    )

def _prepare_scene_sounds():
    global _scene_sound_cache,_scene_sound_errors
    _scene_sound_cache={}; _scene_sound_errors={}
    if _scene_library is None: return
    for name in _scene_library.referenced_sounds:
        try:
            path=resolve_sound_path(SCENE_SOUND_DIR,name)
            if not path.is_file(): raise SceneConfigError(f"sound cue not found: {name}")
            if _speech_engine is not None:
                _scene_sound_cache[name]=load_wav_pcm16(
                    path,
                    _speech_engine.sample_rate,
                    maximum_seconds=SCENE_MAX_SECONDS,
                )
        except Exception as e:
            _scene_sound_errors[name]=str(e)
            print(f"[scenes] sound {name!r} unavailable: {e}")
    scene_speech_requires_streaming=_scene_requires_streaming_speech()
    limitations=[]
    if scene_speech_requires_streaming and _speech_engine is None:
        limitations.append("scene speech unavailable on legacy Piper")
    if _scene_sound_errors:
        limitations.append(f"{len(_scene_sound_errors)} sound cue errors")
    if limitations:
        detail=f"{len(_scene_library)} scenes; {'; '.join(limitations)}"
        _health_set("scenes",ComponentState.DEGRADED,detail)
        errors=list(_scene_sound_errors.values())
        if scene_speech_requires_streaming and _speech_engine is None:
            errors.insert(0,"scene speech requires streaming Piper")
        mqtt_pub("scene/last_error","; ".join(errors)[:255],retain=True)
    elif len(_scene_library):
        detail=f"{len(_scene_library)} scenes ready; {len(_scene_sound_cache)} cues preloaded"
        _health_set("scenes",ComponentState.READY,detail)

def _content_reload_ready_state():
    if not CONTENT_RELOAD_ENABLED: return "disabled"
    if _content_reload_active: return "reloading"
    if _content_reload_pending: return "queued"
    return _content_reload_state

def _publish_content_reload_state():
    mqtt_pub("content_reload/active","ON" if _content_reload_active else "OFF",retain=True)
    mqtt_pub("content_reload/state",_content_reload_ready_state(),retain=True)
    mqtt_pub("content_reload/last_result",_content_reload_last_result,retain=True)
    mqtt_pub("content_reload/last_error",_content_reload_last_error,retain=True)
    mqtt_pub("content_reload/last_run",_content_reload_last_run,retain=True)
    mqtt_pub("content_reload/last_duration",f"{_content_reload_last_duration:.3f}",retain=True)
    mqtt_pub("content_reload/count",str(_content_reload_count),retain=True)
    mqtt_pub("content_reload/interrupted",str(_content_reload_interrupted),retain=True)

def _init_content_reload():
    global _content_reload_state,_content_reload_last_result,_content_reload_last_error
    if not CONTENT_RELOAD_ENABLED:
        _content_reload_state="disabled"
        _content_reload_last_result="disabled"
        _content_reload_last_error="none"
        _health_set("content_reload",ComponentState.DISABLED,"disabled by configuration")
    elif not PERSONALITIES_ENABLED and not SCENES_ENABLED:
        _content_reload_state="disabled"
        _content_reload_last_result="disabled"
        _content_reload_last_error="no enabled content libraries"
        _health_set("content_reload",ComponentState.DISABLED,"no enabled content libraries")
    else:
        _content_reload_state="ready"
        _content_reload_last_result="never"
        _content_reload_last_error="none"
        _health_set("content_reload",ComponentState.READY,"live content reload ready")
    _publish_content_reload_state()

def _request_content_reload():
    global _content_reload_pending,_content_reload_last_result,_content_reload_last_error
    if not CONTENT_RELOAD_ENABLED or (not PERSONALITIES_ENABLED and not SCENES_ENABLED):
        _content_reload_last_result="disabled"
        _content_reload_last_error="live content reload is disabled"
        _publish_content_reload_state()
        return False
    if controller is None:
        _content_reload_last_result="not_ready"
        _content_reload_last_error="controller is not ready"
        _publish_content_reload_state()
        return False
    if (
        _content_reload_active
        or _content_reload_pending
        or controller.state not in (
            RuntimeState.IDLE,
            RuntimeState.COOLDOWN,
            RuntimeState.MAINTENANCE,
        )
    ):
        _content_reload_last_result="busy"
        _content_reload_last_error=f"controller is {controller.state.value}"
        _publish_content_reload_state()
        return False
    _content_reload_pending=True
    accepted=_enqueue(EventKind.RELOAD_CONTENT,source="mqtt")
    if accepted:
        _content_reload_last_result="queued"
        _content_reload_last_error="none"
    else:
        _content_reload_pending=False
        _content_reload_last_result="busy"
        _content_reload_last_error="controller queue rejected the request"
    _publish_content_reload_state()
    return accepted

def _reload_additional_canned_lines():
    lines=[]
    if not PERSONALITIES_ENABLED:
        for group in (
            MORNING_LINES,AFTERNOON_LINES,EVENING_LINES,NIGHT_LINES,
            GOODBYE_LINES,IDLE_LINES,
        ):
            lines.extend(group)
    if SELF_TEST_ENABLED and SELF_TEST_LINE:
        lines.append(SELF_TEST_LINE)
    return tuple(dict.fromkeys(str(line).strip() for line in lines if str(line).strip()))

def _restore_active_speech_cache():
    if _speech_engine is not None and TTS_CANNED_CACHE:
        try: _speech_engine.retain_cached_phrases(_canned_speech_lines())
        except Exception as error: print(f"[content reload] cache rollback failed: {error}")

def _run_content_reload():
    global _content_reload_active,_content_reload_pending,_content_reload_state
    global _content_reload_last_result,_content_reload_last_error
    global _content_reload_last_run,_content_reload_last_duration
    global _content_reload_count,_content_reload_interrupted
    global _personality_library,_personality_active,_personality_load_error
    global _personality_last_result,_personality_last_error
    global _scene_library,_scene_runner,_scene_sound_cache,_scene_sound_errors
    global _scene_load_error,_llm_client,_barge_in_matcher,_idle_life

    _content_reload_pending=False
    if not CONTENT_RELOAD_ENABLED:
        _content_reload_last_result="disabled"
        _content_reload_last_error="live content reload is disabled"
        _publish_content_reload_state()
        return

    started=time.monotonic()
    _content_reload_count+=1
    _content_reload_active=True
    _content_reload_state="reloading"
    _content_reload_last_result="running"
    _content_reload_last_error="none"
    _publish_content_reload_state()
    controller.set_state(RuntimeState.CONTENT_RELOAD)
    interrupt=controller.content_reload_interrupt_event

    def reload_interrupted():
        controller.heartbeat()
        return interrupt.is_set()

    committed=False
    try:
        bundle=prepare_content(
            personalities_enabled=PERSONALITIES_ENABLED,
            personalities_path=PERSONALITIES_PATH,
            requested_personality=PERSONALITY_REQUESTED,
            current_personality=_personality_active_name(),
            scenes_enabled=SCENES_ENABLED,
            scenes_path=SCENES_PATH,
            sound_directory=SCENE_SOUND_DIR,
            sound_sample_rate=(
                _speech_engine.sample_rate if _speech_engine is not None else VOSK_RATE
            ),
            scene_maximum_seconds=SCENE_MAX_SECONDS,
            cache_sounds=_speech_engine is not None,
            additional_canned_lines=_reload_additional_canned_lines(),
            interrupted=reload_interrupted,
        )

        active=bundle.active_personality
        new_llm=_build_llm_client_for_pack(active) if active is not None else _llm_client
        new_matcher=_build_barge_in_matcher_for_pack(active) if active is not None else _barge_in_matcher
        new_idle=_build_idle_life_for_pack(active) if active is not None else _idle_life
        new_runner=(
            SceneRunner(
                executor=_execute_scene_step,
                maximum_seconds=SCENE_MAX_SECONDS,
                progress=_scene_progress,
            )
            if bundle.scenes is not None
            else None
        )

        cache_metrics=None
        if _speech_engine is not None and TTS_CANNED_CACHE:
            mqtt_pub("tts/cache_state","warming",retain=True)
            cache_metrics=_speech_engine.cache_phrases(
                bundle.canned_lines,
                stop_event=interrupt,
                progress=controller.heartbeat,
            )
            if cache_metrics.interrupted or interrupt.is_set():
                raise ContentReloadInterrupted("reload interrupted while caching speech")
            if cache_metrics.failed_entries:
                raise ContentReloadError(
                    "; ".join(cache_metrics.errors) or "canned speech preload failed"
                )
        if interrupt.is_set():
            raise ContentReloadInterrupted("reload interrupted before commit")

        if _speech_engine is not None and TTS_CANNED_CACHE:
            _speech_engine.retain_cached_phrases(bundle.canned_lines)
            cache_metrics=replace(
                cache_metrics,
                total_entries=_speech_engine.cache_entries,
                pcm_bytes=_speech_engine.cache_pcm_bytes,
            )

        # All fallible preparation is complete. These assignments are the
        # transaction's commit point and run on the serialized controller.
        _personality_library=bundle.personalities
        _personality_active=active
        _personality_load_error=""
        _scene_library=bundle.scenes
        _scene_runner=new_runner
        _scene_sound_cache=dict(bundle.sound_cache)
        _scene_sound_errors={}
        _scene_load_error=""
        if active is not None:
            _apply_personality_globals(active)
            _llm_client=new_llm
            _barge_in_matcher=new_matcher
            _idle_life=new_idle
            _personality_last_result="reloaded"
            _personality_last_error="none"
        committed=True

        if cache_metrics is not None:
            _publish_personality_cache(cache_metrics)

        _content_reload_state="ready"
        _content_reload_last_result="reloaded"
        _content_reload_last_error="none"
        _health_set("content_reload",ComponentState.READY,"active content reloaded")
        if active is not None:
            _health_set("personality",ComponentState.READY,f"{active.name}; {len(bundle.personalities)} packs loaded")
        if bundle.scenes is not None:
            if _speech_engine is None and _scene_requires_streaming_speech():
                _health_set("scenes",ComponentState.DEGRADED,f"{len(bundle.scenes)} scenes; scene speech unavailable on legacy Piper")
            else:
                _health_set("scenes",ComponentState.READY,f"{len(bundle.scenes)} scenes ready; {len(_scene_sound_cache)} cues preloaded")
        publish_mqtt_discovery()
        _publish_personality_state()
        _publish_scene_ready_state()
        _publish_barge_in_capability()
        _publish_idle_life_ready_state()
        print(f"[content reload] committed {len(_personality_names())} personalities and {len(_scene_library) if _scene_library is not None else 0} scenes")
    except ContentReloadInterrupted as error:
        _restore_active_speech_cache()
        _content_reload_interrupted+=1
        _content_reload_state="ready"
        _content_reload_last_result="interrupted"
        _content_reload_last_error=str(error)[:255]
        _health_set("content_reload",ComponentState.READY,"reload interrupted; active content unchanged")
        print(f"[content reload] {error}; keeping active content")
    except Exception as error:
        if committed:
            _content_reload_state="ready"
            _content_reload_last_result="reloaded"
            _content_reload_last_error=f"content committed; status update failed: {error}"[:255]
            _health_set("content_reload",ComponentState.DEGRADED,_content_reload_last_error)
            print(f"[content reload] content committed; status update failed: {error}")
        else:
            _restore_active_speech_cache()
            _content_reload_state="error"
            _content_reload_last_result="error"
            _content_reload_last_error=str(error)[:255]
            _health_set("content_reload",ComponentState.DEGRADED,f"reload failed; active content unchanged: {error}")
            print(f"[content reload] failed: {error}; keeping active content")
    finally:
        _content_reload_active=False
        _content_reload_last_run=datetime.now(timezone.utc).isoformat(timespec="seconds")
        _content_reload_last_duration=max(0.0,time.monotonic()-started)
        _publish_content_reload_state()

def _scene_eye_level(level):
    level=clamp(float(level),0.0,1.0)
    return min(level,clamp(EYES_SPEAK_FRAC,0.0,1.0)) if night_mode else level

def _play_legacy_scene_sound(path,stop_signal,animate_jaw):
    try:
        process=subprocess.Popen(
            ["aplay","-q",str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        raise RuntimeError(f"cannot start sound cue: {e}") from e
    envelope=jaw_env_from_wav(str(path)) if animate_jaw else None
    position=0
    interrupted=False
    try:
        while process.poll() is None:
            if stop_signal.wait(0.02):
                interrupted=True
                process.terminate()
                break
            if envelope is not None and position < envelope.size:
                level=float(envelope[position]); position+=1
                _jaw_set(JAW_REST_FRAC+(JAW_MAX_FRAC-JAW_REST_FRAC)*level)
        try: process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=0.5)
        if not interrupted and process.returncode:
            raise RuntimeError(f"aplay exited with status {process.returncode}")
        return interrupted
    finally:
        _jaw_set(JAW_REST_FRAC)

def _play_scene_sound(parameters,stop_signal):
    name=str(parameters["file"])
    if name in _scene_sound_errors:
        raise RuntimeError(_scene_sound_errors[name])
    path=resolve_sound_path(SCENE_SOUND_DIR,name)
    if _speech_engine is None:
        return _play_legacy_scene_sound(path,stop_signal,bool(parameters["jaw"]))
    pcm=_scene_sound_cache.get(name)
    if pcm is None:
        pcm=load_wav_pcm16(path,_speech_engine.sample_rate,SCENE_MAX_SECONDS)
        _scene_sound_cache[name]=pcm
    monitor=_start_barge_in_monitor()
    try:
        combined=AnyStopEvent(
            stop_signal,
            monitor.interrupt_event if monitor is not None else None,
        )
        metrics=_speech_engine.play_pcm16(
            pcm,
            stop_event=combined,
            animate_jaw=bool(parameters["jaw"]),
            volume_multiplier=float(parameters["volume"]),
        )
    finally:
        barge_result=_finish_barge_in_monitor(monitor) if monitor is not None else None
    return bool(metrics.interrupted or barge_result is not None)

def _execute_scene_step(step,stop_signal):
    parameters=step.parameters
    if step.action is SceneAction.SPEAK:
        if _speech_engine is None:
            raise RuntimeError("scene speech requires the interruptible streaming Piper path")
        result=speak_with_jaw(
            str(parameters["text"]),
            stop_event=stop_signal,
            allow_legacy_fallback=False,
        )
        return bool(stop_signal.is_set() or result is not None)
    if step.action is SceneAction.EYES:
        eyes_set(_scene_eye_level(parameters["level"]))
        duration=float(parameters["duration"])
        return stop_signal.wait(duration) if duration > 0 else False
    if step.action is SceneAction.BLINK:
        half=float(parameters["period"])/2.0
        for _ in range(int(parameters["count"])):
            eyes_set(_scene_eye_level(parameters["high"]))
            if stop_signal.wait(half): return True
            eyes_set(_scene_eye_level(parameters["low"]))
            if stop_signal.wait(half): return True
        return False
    if step.action is SceneAction.FLICKER:
        end=time.monotonic()+float(parameters["duration"])
        while time.monotonic()<end:
            level=float(parameters["base"])+random.random()*float(parameters["span"])
            eyes_set(_scene_eye_level(level))
            if stop_signal.wait(min(float(parameters["step"]),max(0.0,end-time.monotonic()))):
                return True
        return False
    if step.action is SceneAction.JAW:
        amount=float(parameters["level"])
        _jaw_set(JAW_REST_FRAC+(JAW_MAX_FRAC-JAW_REST_FRAC)*amount)
        interrupted=stop_signal.wait(float(parameters["duration"]))
        _jaw_set(JAW_REST_FRAC)
        return interrupted
    if step.action is SceneAction.SOUND:
        return _play_scene_sound(parameters,stop_signal)
    raise RuntimeError(f"unsupported scene action: {step.action.value}")

def _run_scene(name):
    global _scene_count,_scene_interrupted,_scene_active,_scene_current,_scene_step
    if _scene_library is None or _scene_runner is None:
        error=_scene_load_error or "scene engine is not ready"
        mqtt_pub("scene/last_result","error",retain=True)
        mqtt_pub("scene/last_error",error[:255],retain=True)
        return
    scene=_scene_library.get(name)
    if scene is None:
        available=", ".join(_scene_library.names) or "none"
        error=f"unknown scene {str(name)!r}; available: {available}"
        mqtt_pub("scene/last_result","error",retain=True)
        mqtt_pub("scene/last_error",error[:255],retain=True)
        return

    _scene_count+=1
    _scene_active=True; _scene_current=scene.name; _scene_step="starting"
    _publish_scene_ready_state()
    controller.set_state(RuntimeState.SCENE)
    try:
        result=_scene_runner.run(scene,controller.scene_interrupt_event)
    finally:
        _jaw_set(JAW_REST_FRAC)
        eyes_idle()
        _scene_active=False; _scene_current="none"; _scene_step="none"

    mqtt_pub("scene/last_result",result.outcome,retain=True)
    mqtt_pub("scene/last_duration",f"{result.duration_seconds:.3f}",retain=True)
    if result.interrupted:
        _scene_interrupted+=1
        mqtt_pub("scene/interrupted",str(_scene_interrupted),retain=True)
    if result.error:
        mqtt_pub("scene/last_error",result.error[:255],retain=True)
        _health_set("scenes",ComponentState.DEGRADED,f"{scene.name}: {result.error}")
    elif result.timed_out:
        message=f"{scene.name} exceeded {SCENE_MAX_SECONDS:g}s limit"
        mqtt_pub("scene/last_error",message,retain=True)
        _health_set("scenes",ComponentState.DEGRADED,message)
    elif not _scene_sound_errors and not (
        _scene_requires_streaming_speech() and _speech_engine is None
    ):
        mqtt_pub("scene/last_error","none",retain=True)
        _health_set("scenes",ComponentState.READY,f"{len(_scene_library)} scenes ready")
    _publish_scene_ready_state()

def _self_test_ready_state():
    if _self_test_active: return "running"
    if _self_test_pending: return "queued"
    if _maintenance_stop_requested(): return "locked"
    if not SELF_TEST_ENABLED: return "disabled"
    if _self_test_runner is None: return "starting"
    return "ready"

def _publish_self_test_state():
    mqtt_pub("self_test/active","ON" if _self_test_active else "OFF",retain=True)
    mqtt_pub("self_test/state",_self_test_ready_state(),retain=True)
    mqtt_pub("self_test/step",_self_test_step,retain=True)
    mqtt_pub("self_test/last_result",_self_test_last_result,retain=True)
    mqtt_pub("self_test/last_error",_self_test_last_error,retain=True)
    mqtt_pub("self_test/last_run",_self_test_last_run,retain=True)
    mqtt_pub("self_test/count",str(_self_test_count),retain=True)
    mqtt_pub("self_test/interrupted",str(_self_test_interrupted),retain=True)
    mqtt_pub("self_test/report",_self_test_report,retain=True)

def _self_test_progress(index,total,step):
    global _self_test_step
    if controller is not None: controller.heartbeat()
    _self_test_step=f"{index}/{total}:{step.name}"
    mqtt_pub("self_test/step",_self_test_step,retain=True)

def _self_test_eyes(stop_signal):
    if _eyes_ch is None:
        raise SelfTestStepSkipped("PCA9685 eye channel unavailable")
    level=clamp(SELF_TEST_EYES_FRAC,0.05,0.35)
    if night_mode:
        level=min(level,clamp(EYES_SPEAK_FRAC,0.0,1.0))
    if level <= 0:
        raise SelfTestStepSkipped("night-mode eye limit is zero")
    interval=clamp(SELF_TEST_STEP_SEC,0.10,1.0)
    for scale in (0.45,1.0):
        if not eyes_set(level*scale):
            raise RuntimeError("eye PWM write failed")
        if stop_signal.wait(interval):
            raise SelfTestInterrupted()
        if not eyes_set(0.0):
            raise RuntimeError("eye PWM reset failed")
        if stop_signal.wait(interval/2.0):
            raise SelfTestInterrupted()
    return f"two PWM pulses up to {level*100:.0f}%"

def _self_test_jaw(stop_signal):
    if _jaw is None:
        raise SelfTestStepSkipped("PCA9685 jaw servo unavailable")
    travel=clamp(SELF_TEST_JAW_FRAC,0.05,0.35)
    target=JAW_REST_FRAC+(JAW_MAX_FRAC-JAW_REST_FRAC)*travel
    interval=clamp(SELF_TEST_STEP_SEC,0.10,1.0)
    for _ in range(2):
        if not _jaw_set(target):
            raise RuntimeError("jaw servo write failed")
        if stop_signal.wait(interval):
            raise SelfTestInterrupted()
        if not _jaw_set(JAW_REST_FRAC):
            raise RuntimeError("jaw servo reset failed")
        if stop_signal.wait(interval/2.0):
            raise SelfTestInterrupted()
    return f"two movements at {travel*100:.0f}% travel"

def _self_test_speaker(stop_signal):
    if _speech_engine is None:
        raise SelfTestStepSkipped("interruptible streaming Piper unavailable")
    if not SELF_TEST_LINE:
        raise SelfTestStepSkipped("SELF_TEST_LINE is empty")
    completed=[]
    barge_result=speak_with_jaw(
        SELF_TEST_LINE,
        stop_event=stop_signal,
        allow_legacy_fallback=False,
        streaming_result=completed.append,
    )
    if stop_signal.is_set() or barge_result is not None:
        raise SelfTestInterrupted()
    if not completed:
        raise RuntimeError("streaming Piper playback did not complete")
    if completed[0].interrupted:
        raise SelfTestInterrupted()
    return "streaming Piper audio played"

def _init_operator_self_test():
    global _self_test_runner
    if not SELF_TEST_ENABLED:
        _self_test_runner=None
        _health_set("self_test",ComponentState.DISABLED,"disabled by configuration")
        _publish_self_test_state()
        return
    _self_test_runner=SelfTestRunner(
        maximum_seconds=SELF_TEST_MAX_SECONDS,
        progress=_self_test_progress,
    )
    _health_set("self_test",ComponentState.READY,"manual output test ready")
    _publish_self_test_state()

def _record_self_test_request(result,error="none"):
    global _self_test_last_result,_self_test_last_error
    _self_test_last_result=str(result)
    _self_test_last_error=str(error or "none")[:255]
    mqtt_pub("self_test/last_result",_self_test_last_result,retain=True)
    mqtt_pub("self_test/last_error",_self_test_last_error,retain=True)

def _request_self_test():
    global _self_test_pending
    if not SELF_TEST_ENABLED or _self_test_runner is None:
        _record_self_test_request("disabled","operator self-test is disabled")
        return False
    if controller is None:
        _record_self_test_request("not_ready","controller is not ready")
        return False
    if (
        _self_test_active
        or _self_test_pending
        or controller.state not in (RuntimeState.IDLE,RuntimeState.COOLDOWN)
    ):
        _record_self_test_request("busy",f"controller is {controller.state.value}")
        return False
    _self_test_pending=True
    accepted=_enqueue(EventKind.RUN_SELF_TEST,source="mqtt")
    if accepted:
        _record_self_test_request("queued")
        _publish_self_test_state()
    else:
        _self_test_pending=False
        _record_self_test_request("busy","controller queue rejected the request")
    return accepted

def _stop_self_test():
    global _self_test_cancel_pending
    if controller is not None and _self_test_active:
        controller.interrupt_self_test()
        mqtt_pub("self_test/state","stopping",retain=True)
        return True
    if _self_test_pending:
        _self_test_cancel_pending=True
        mqtt_pub("self_test/state","stopping",retain=True)
        return True
    return False

def _run_self_test():
    global _self_test_active,_self_test_step,_self_test_last_result
    global _self_test_last_error,_self_test_last_run,_self_test_count
    global _self_test_interrupted,_self_test_report
    global _self_test_pending,_self_test_cancel_pending
    _self_test_pending=False
    if _self_test_runner is None:
        _record_self_test_request("disabled","operator self-test is not ready")
        return

    if _self_test_cancel_pending:
        _self_test_cancel_pending=False
        _self_test_count+=1; _self_test_interrupted+=1
        _self_test_last_result="interrupted"; _self_test_last_error="none"
        _self_test_last_run=datetime.now(timezone.utc).isoformat(timespec="seconds")
        _self_test_report=(
            '{"duration_seconds":0.0,"interrupted":true,"outcome":"interrupted",'
            '"steps":[],"timed_out":false}'
        )
        _health_set("self_test",ComponentState.READY,"queued run cancelled")
        _publish_self_test_state()
        return

    _self_test_count+=1
    _self_test_active=True; _self_test_step="starting"
    _publish_self_test_state()
    controller.set_state(RuntimeState.SELF_TEST)
    try:
        result=_self_test_runner.run(
            (
                SelfTestStep("eyes",_self_test_eyes),
                SelfTestStep("jaw",_self_test_jaw),
                SelfTestStep("speaker",_self_test_speaker),
            ),
            controller.self_test_interrupt_event,
        )
        _self_test_last_result=result.outcome
        _self_test_last_error=result.error or (
            f"exceeded {SELF_TEST_MAX_SECONDS:g}s limit" if result.timed_out else "none"
        )
        _self_test_report=result.report_json()
        if result.interrupted:
            _self_test_interrupted+=1
        if result.outcome in ("failed","timed_out"):
            _health_set(
                "self_test",
                ComponentState.DEGRADED,
                _self_test_last_error or result.outcome,
            )
        elif result.outcome == "degraded":
            _health_set("self_test",ComponentState.DEGRADED,"one or more output tests unavailable")
        else:
            _health_set("self_test",ComponentState.READY,f"last run {result.outcome}")
    except Exception as error:
        _self_test_last_result="failed"
        _self_test_last_error=str(error)[:255]
        _self_test_report=json.dumps({"outcome":"failed","error":str(error)[:255]})
        _health_set("self_test",ComponentState.DEGRADED,_self_test_last_error)
    finally:
        _jaw_set(JAW_REST_FRAC)
        eyes_idle()
        _self_test_active=False; _self_test_step="none"
        _self_test_cancel_pending=False
        _self_test_last_run=datetime.now(timezone.utc).isoformat(timespec="seconds")
        _publish_self_test_state()

def _idle_life_ready_state():
    if _idle_life is None: return "starting"
    if not _idle_life.enabled: return "disabled"
    if _maintenance_stop_requested(): return "locked"
    if not motion_enabled: return "disarmed"
    return "ready"

def _publish_idle_life_ready_state():
    mqtt_pub(
        "idle_life/enabled",
        "ON" if (_idle_life is not None and _idle_life.enabled) else "OFF",
        retain=True,
    )
    mqtt_pub("idle_life/state",_idle_life_ready_state(),retain=True)

def _init_idle_life():
    global _idle_life
    _idle_life=IdleLifeScheduler(
        minimum_interval=IDLE_LIFE_MIN_SEC,
        maximum_interval=IDLE_LIFE_MAX_SEC,
        mutter_chance=IDLE_MUTTER_CHANCE,
        mutter_lines=IDLE_LINES,
        enabled=IDLE_LIFE_ENABLED,
    )
    mqtt_pub("idle_life/active","OFF",retain=True)
    mqtt_pub("idle_life/count",str(_idle_life_count),retain=True)
    mqtt_pub("idle_life/interrupted",str(_idle_life_interrupted),retain=True)
    _publish_idle_life_ready_state()

def _idle_eye_target():
    target=clamp(IDLE_EYE_PULSE_FRAC,0.0,1.0)
    if night_mode:
        target=min(target,clamp(EYES_LISTEN_FRAC,0.0,1.0))
    return max(clamp(EYES_IDLE_FRAC,0.0,1.0),target)

def _idle_eye_pulse(interrupt_event):
    if interrupt_event.is_set(): return True
    eyes_set(_idle_eye_target())
    interrupted=interrupt_event.wait(max(0.01,IDLE_EYE_PULSE_MS/1000.0))
    eyes_idle()
    return interrupted

def _idle_jaw_twitch(interrupt_event):
    if interrupt_event.is_set(): return True
    amount=clamp(IDLE_JAW_TWITCH_FRAC,0.0,1.0)
    target=JAW_REST_FRAC+(JAW_MAX_FRAC-JAW_REST_FRAC)*amount
    _jaw_set(target)
    interrupted=interrupt_event.wait(max(0.01,IDLE_JAW_TWITCH_MS/1000.0))
    _jaw_set(JAW_REST_FRAC)
    return interrupted

def _run_idle_decision(decision,interrupt_event):
    # Legacy aplay cannot stop mid-file, so never let a mutter delay a visitor.
    if decision.action is IdleAction.MUTTER and _speech_engine is None:
        decision=IdleDecision(IdleAction.JAW_TWITCH)
        mqtt_pub("idle_life/last_action","jaw_twitch_legacy_fallback")
    if decision.action is IdleAction.EYE_PULSE:
        return _idle_eye_pulse(interrupt_event)
    if decision.action is IdleAction.JAW_TWITCH:
        return _idle_jaw_twitch(interrupt_event)
    eyes_set(_idle_eye_target())
    result=speak_with_jaw(
        decision.text,
        stop_event=interrupt_event,
        allow_legacy_fallback=False,
    )
    eyes_idle()
    return bool(interrupt_event.is_set() or result is not None)

def _idle_tick(interrupt_event):
    global _idle_life_count,_idle_life_interrupted
    if _idle_life is None: return
    environment_clear=not bool(getattr(pir,"motion_detected",False))
    decision=_idle_life.poll(
        armed=motion_enabled and environment_clear and not _maintenance_stop_requested()
    )
    if decision is None or interrupt_event.is_set(): return

    _idle_life_count+=1
    mqtt_pub("idle_life/active","ON")
    mqtt_pub("idle_life/state","running")
    mqtt_pub("idle_life/last_action",decision.action.value)
    mqtt_pub("idle_life/count",str(_idle_life_count))
    controller.set_state(RuntimeState.IDLE_LIFE)
    interrupted=False
    try:
        interrupted=_run_idle_decision(decision,interrupt_event)
    finally:
        _jaw_set(JAW_REST_FRAC)
        eyes_idle()
        mqtt_pub("idle_life/active","OFF")
        if interrupted or interrupt_event.is_set():
            _idle_life_interrupted+=1
            mqtt_pub("idle_life/interrupted",str(_idle_life_interrupted))
        if not controller.stop_event.is_set():
            controller.set_state(RuntimeState.IDLE)
            _publish_idle_life_ready_state()

def _snooze_idle_life():
    if _idle_life is not None: _idle_life.snooze()

def _runtime_state_changed(state):
    mqtt_pub("status",state.value,retain=True)
    if state is RuntimeState.ERROR:
        _health_set("runtime",ComponentState.FAILED,"controller error",True)
    if _maintenance_stop_requested() or state is RuntimeState.MAINTENANCE:
        eyes_off()
    elif state in (RuntimeState.GREETING,RuntimeState.SPEAKING):
        eyes_speak()
    elif state in (RuntimeState.LISTENING,RuntimeState.THINKING):
        eyes_listen()
    elif state in (
        RuntimeState.EFFECT,
        RuntimeState.SCENE,
        RuntimeState.IDLE_LIFE,
        RuntimeState.SELF_TEST,
    ):
        return
    else:
        eyes_idle()

def _say_goodbye():
    if _maintenance_stop_requested(): return None
    gb=random.choice(GOODBYE_LINES) if GOODBYE_LINES else "Goodbye."
    _transcript_add("assistant",gb)
    controller.set_state(RuntimeState.SPEAKING)
    return speak_with_jaw(gb)

def _barge_in_ends_visit(result):
    return bool(result is not None and result.action is BargeInAction.END_VISIT)

def _conversation_loop(memory):
    foreground_stop=AnyStopEvent(
        controller.stop_event,
        controller.maintenance_interrupt_event,
    )
    while not foreground_stop.is_set():
        controller.set_state(RuntimeState.LISTENING)
        text=record_once(
            in_idx or 0,
            44100,
            SPEECH_START_TIMEOUT,
            foreground_stop,
        )
        if foreground_stop.is_set(): return
        if not text:
            _say_goodbye(); return
        _transcript_add("user",text)
        if EXIT_RE.search(text):
            _say_goodbye(); return
        controller.set_state(RuntimeState.THINKING)
        reply,barge_result=stream_llm_reply(text,memory)
        if foreground_stop.is_set(): return
        if reply: _transcript_add("assistant",reply)
        if _barge_in_ends_visit(barge_result): return

_last_talk=0.0
def _manual_trigger():
    global _last_talk
    now=time.monotonic()
    if (now-_last_talk)<MOTION_COOLDOWN_SEC:
        controller.set_state(RuntimeState.COOLDOWN)
        return
    _transcript_start()
    memory=None
    try:
        opener=pick_opening_line()
        memory=ConversationMemory(LLM_MEMORY_TURNS,opening_line=opener)
        _publish_memory_turns(memory)
        _transcript_add("assistant",opener)
        controller.set_state(RuntimeState.GREETING)
        greeting_started=time.monotonic()
        def greeting_first_audio(_seconds):
            elapsed=time.monotonic()-greeting_started
            mqtt_pub("tts/greeting_first_audio",f"{elapsed:.3f}")
            _health_latency("greeting_first_audio",elapsed)
        barge_result=speak_with_jaw(
            opener,
            first_audio=greeting_first_audio,
        )
        if (
            not _maintenance_stop_requested()
            and not _barge_in_ends_visit(barge_result)
        ):
            _conversation_loop(memory)
    finally:
        if memory is not None: memory.clear()
        _publish_memory_turns(None)
        _last_talk=time.monotonic()
        _transcript_publish_and_clear()
        if not controller.stop_event.is_set() and not _maintenance_stop_requested():
            controller.set_state(RuntimeState.COOLDOWN)

def _handle_event(event):
    if maintenance_mode and event.kind in _MAINTENANCE_BLOCKED_EVENTS:
        _maintenance_reject(event.kind.value)
    elif event.kind is EventKind.SET_MAINTENANCE_MODE:
        _set_maintenance_mode(event.payload)
    elif event.kind is EventKind.TRIGGER:
        _manual_trigger()
    elif event.kind is EventKind.SET_PERSONALITY:
        _switch_personality(event.payload)
    elif event.kind is EventKind.PLAY_SCENE:
        _run_scene(event.payload)
    elif event.kind is EventKind.STOP_SCENE:
        _publish_scene_ready_state()
    elif event.kind is EventKind.RUN_SELF_TEST:
        _run_self_test()
    elif event.kind is EventKind.RELOAD_CONTENT:
        _run_content_reload()
    elif event.kind is EventKind.SAY:
        _transcript_start()
        try:
            _transcript_add("assistant",str(event.payload))
            controller.set_state(RuntimeState.SPEAKING)
            speak_with_jaw(str(event.payload))
        finally:
            _transcript_publish_and_clear()
    elif event.kind is EventKind.BLINK:
        controller.set_state(RuntimeState.EFFECT)
        eyes_blink(6,120,blocking=True)
    elif event.kind is EventKind.FLICKER:
        controller.set_state(RuntimeState.EFFECT)
        eyes_flicker(5.0,0.2,0.7,60,blocking=True)
    elif event.kind is EventKind.SET_EYES_DIM:
        _set_eyes_dim(float(event.payload))
    elif event.kind is EventKind.SET_EYES_FULL:
        _set_eyes_full(float(event.payload))
    elif event.kind is EventKind.SET_VOLUME:
        _set_volume(float(event.payload))
    elif event.kind is EventKind.SET_MOTION_ENABLED:
        _set_motion_enabled(event.payload)
    elif event.kind is EventKind.SET_IDLE_LIFE_ENABLED:
        _set_idle_life_enabled(event.payload)
    elif event.kind is EventKind.SET_NIGHT_MODE:
        _toggle_night_mode(event.payload)
    elif event.kind is EventKind.RESTART:
        mqtt_pub("availability","offline",retain=True)
        controller.request_stop("mqtt-restart")

    _snooze_idle_life()
    if not controller.stop_event.is_set():
        controller.set_state(
            RuntimeState.MAINTENANCE if maintenance_mode else RuntimeState.IDLE
        )

def _signal_handler(sig,frame):
    print(f"[signal] {signal.Signals(sig).name}; stopping")
    if controller is not None:
        controller.request_stop("signal")

def _cleanup():
    global _runtime_ready
    _runtime_ready=False
    _cancel_motion_timer()
    try:
        if _watchdog is not None: _watchdog.stop("skeleton runtime stopping")
    except Exception: pass
    try:
        if _health is not None:
            _health.set_component("runtime",ComponentState.STOPPING,"service stopping",True,publish=False)
            _health.publish_now(sample=False)
            _health.stop()
    except Exception: pass
    try: _transcript_publish_and_clear()
    except Exception: pass
    try: mqtt_pub("ready","OFF",retain=True)
    except Exception: pass
    try: mqtt_pub("barge_in/active","OFF",retain=True)
    except Exception: pass
    try: mqtt_pub("idle_life/active","OFF",retain=True)
    except Exception: pass
    try: mqtt_pub("idle_life/state","stopping",retain=True)
    except Exception: pass
    try: mqtt_pub("scene/active","OFF",retain=True)
    except Exception: pass
    try: mqtt_pub("scene/state","stopping",retain=True)
    except Exception: pass
    try: mqtt_pub("self_test/active","OFF",retain=True)
    except Exception: pass
    try: mqtt_pub("self_test/state","stopping",retain=True)
    except Exception: pass
    try: mqtt_pub("maintenance/state","stopping",retain=True)
    except Exception: pass
    try: mqtt_pub("personality/state","stopping",retain=True)
    except Exception: pass
    try: mqtt_pub("content_reload/active","OFF",retain=True)
    except Exception: pass
    try: mqtt_pub("content_reload/state","stopping",retain=True)
    except Exception: pass
    try: mqtt_pub("availability","offline",retain=True)
    except Exception: pass
    try: _jaw_set(JAW_REST_FRAC)
    except Exception: pass
    try:
        if _speech_engine: _speech_engine.close()
    except Exception: pass
    try: eyes_off()
    except Exception: pass
    try: pir.close()
    except Exception: pass
    try:
        if _pca: _pca.deinit()
    except Exception: pass
    try:
        if mqttc: mqttc.loop_stop(); mqttc.disconnect()
    except Exception: pass

def main():
    global controller,_runtime_ready
    _init_health_monitor()
    _init_systemd_watchdog()
    _init_persistent_settings()
    controller=SkeletonController(
        _handle_event,
        _runtime_state_changed,
        idle_handler=_idle_tick,
        heartbeat=_controller_heartbeat,
        initial_state=(
            RuntimeState.MAINTENANCE if maintenance_mode else RuntimeState.IDLE
        ),
    )
    controller.set_maintenance_active(maintenance_mode)
    controller.heartbeat()
    signal.signal(signal.SIGINT,_signal_handler)
    signal.signal(signal.SIGTERM,_signal_handler)
    _init_maintenance_mode()
    _init_personality_library()
    _do_mqtt_connect()
    _init_scene_engine()
    _validate_personality_scenes()
    _init_speech_engine()
    _init_operator_self_test()
    _prepare_scene_sounds()
    _init_content_reload()
    _init_llm_client()
    _warm_ollama()
    _publish_barge_in_capability()
    _init_idle_life()
    _runtime_ready=True
    _health_set("runtime",ComponentState.READY,"controller ready",True,publish=False)
    controller.heartbeat()
    if _watchdog is not None:
        _watchdog.ready("skeleton controller ready")
        _watchdog.start()
    if _health is not None: _health.start()
    else: mqtt_pub("ready","ON",retain=True)
    print("👀 Waiting for motion or MQTT commands…")
    try:
        controller.run_forever()
    except Exception as e:
        print("[controller]",e)
        _health_set("runtime",ComponentState.FAILED,str(e),True)
        controller.request_stop("controller-error")
        raise
    finally:
        _cleanup()

if __name__ == "__main__":
    main()
