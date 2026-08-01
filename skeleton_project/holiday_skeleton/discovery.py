"""Home Assistant MQTT discovery definitions.

Both the runtime and the standalone publisher import this module so the two
paths cannot silently drift apart.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


DiscoveryMessage = Tuple[str, Optional[Dict[str, Any]]]


def discovery_messages(device_name: str) -> List[DiscoveryMessage]:
    base = f"holiday/{device_name}"
    title = device_name.capitalize()
    device = {
        "identifiers": [f"holiday_{device_name}"],
        "name": title,
        "manufacturer": "SkeletonWorks",
        "model": "Animatronic v1",
    }
    messages: List[DiscoveryMessage] = []

    def add(component: str, key: str, payload: Dict[str, Any]) -> None:
        payload["avty_t"] = f"{base}/availability"
        payload["dev"] = device
        messages.append(
            (f"homeassistant/{component}/{device_name}/{key}/config", payload)
        )

    add("binary_sensor", "motion", {
        "name": f"{title} Motion", "uniq_id": f"holiday_{device_name}_motion",
        "stat_t": f"{base}/motion", "pl_on": "ON", "pl_off": "OFF",
    })
    add("binary_sensor", "speaking", {
        "name": f"{title} Speaking", "uniq_id": f"holiday_{device_name}_speaking",
        "stat_t": f"{base}/speaking", "pl_on": "ON", "pl_off": "OFF",
    })
    add("binary_sensor", "barge_in_enabled", {
        "name": f"{title} Barge-In Enabled",
        "uniq_id": f"holiday_{device_name}_barge_in_enabled",
        "stat_t": f"{base}/barge_in/enabled", "pl_on": "ON", "pl_off": "OFF",
    })
    add("binary_sensor", "barge_in_active", {
        "name": f"{title} Barge-In Listening",
        "uniq_id": f"holiday_{device_name}_barge_in_active",
        "stat_t": f"{base}/barge_in/active", "pl_on": "ON", "pl_off": "OFF",
    })
    add("sensor", "barge_in_state", {
        "name": f"{title} Barge-In State",
        "uniq_id": f"holiday_{device_name}_barge_in_state",
        "stat_t": f"{base}/barge_in/state",
    })
    add("sensor", "barge_in_last_command", {
        "name": f"{title} Last Barge-In Command",
        "uniq_id": f"holiday_{device_name}_barge_in_last_command",
        "stat_t": f"{base}/barge_in/last_command",
    })
    add("sensor", "barge_in_last_action", {
        "name": f"{title} Last Barge-In Action",
        "uniq_id": f"holiday_{device_name}_barge_in_last_action",
        "stat_t": f"{base}/barge_in/last_action",
    })
    add("sensor", "barge_in_latency", {
        "name": f"{title} Barge-In Latency",
        "uniq_id": f"holiday_{device_name}_barge_in_latency",
        "stat_t": f"{base}/barge_in/latency", "unit_of_measurement": "s",
    })
    add("sensor", "barge_in_count", {
        "name": f"{title} Barge-In Count",
        "uniq_id": f"holiday_{device_name}_barge_in_count",
        "stat_t": f"{base}/barge_in/count",
    })
    add("binary_sensor", "ready", {
        "name": f"{title} Ready", "uniq_id": f"holiday_{device_name}_ready",
        "stat_t": f"{base}/ready", "pl_on": "ON", "pl_off": "OFF",
    })
    add("sensor", "status", {
        "name": f"{title} Status", "uniq_id": f"holiday_{device_name}_status",
        "stat_t": f"{base}/status",
    })
    add("sensor", "reply_time", {
        "name": f"{title} Reply Time", "uniq_id": f"holiday_{device_name}_reply_time",
        "stat_t": f"{base}/llm/reply_time", "unit_of_measurement": "s",
    })
    add("sensor", "llm_first_token", {
        "name": f"{title} LLM First Token",
        "uniq_id": f"holiday_{device_name}_llm_first_token",
        "stat_t": f"{base}/llm/first_token", "unit_of_measurement": "s",
    })
    add("sensor", "llm_first_phrase", {
        "name": f"{title} LLM First Phrase",
        "uniq_id": f"holiday_{device_name}_llm_first_phrase",
        "stat_t": f"{base}/llm/first_phrase", "unit_of_measurement": "s",
    })
    add("sensor", "llm_first_audio", {
        "name": f"{title} Response First Audio",
        "uniq_id": f"holiday_{device_name}_llm_first_audio",
        "stat_t": f"{base}/llm/first_audio", "unit_of_measurement": "s",
    })
    add("sensor", "llm_phrase_count", {
        "name": f"{title} Response Phrases",
        "uniq_id": f"holiday_{device_name}_llm_phrase_count",
        "stat_t": f"{base}/llm/phrase_count",
    })
    add("sensor", "llm_memory_turns", {
        "name": f"{title} Memory Turns",
        "uniq_id": f"holiday_{device_name}_llm_memory_turns",
        "stat_t": f"{base}/llm/memory_turns",
    })
    add("sensor", "tts_engine", {
        "name": f"{title} TTS Engine", "uniq_id": f"holiday_{device_name}_tts_engine",
        "stat_t": f"{base}/tts/engine",
    })
    add("sensor", "tts_model_load_time", {
        "name": f"{title} TTS Model Load Time",
        "uniq_id": f"holiday_{device_name}_tts_model_load_time",
        "stat_t": f"{base}/tts/model_load_time", "unit_of_measurement": "s",
    })
    add("sensor", "tts_warmup_time", {
        "name": f"{title} TTS Warmup Time",
        "uniq_id": f"holiday_{device_name}_tts_warmup_time",
        "stat_t": f"{base}/tts/warmup_time", "unit_of_measurement": "s",
    })
    add("sensor", "tts_cache_state", {
        "name": f"{title} TTS Cache State",
        "uniq_id": f"holiday_{device_name}_tts_cache_state",
        "stat_t": f"{base}/tts/cache_state",
    })
    add("sensor", "tts_cache_entries", {
        "name": f"{title} TTS Cached Lines",
        "uniq_id": f"holiday_{device_name}_tts_cache_entries",
        "stat_t": f"{base}/tts/cache_entries",
    })
    add("sensor", "tts_cache_warmup_time", {
        "name": f"{title} TTS Cache Warmup Time",
        "uniq_id": f"holiday_{device_name}_tts_cache_warmup_time",
        "stat_t": f"{base}/tts/cache_warmup_time", "unit_of_measurement": "s",
    })
    add("sensor", "tts_cache_memory", {
        "name": f"{title} TTS Cache Memory",
        "uniq_id": f"holiday_{device_name}_tts_cache_memory",
        "stat_t": f"{base}/tts/cache_memory_kb", "unit_of_measurement": "KiB",
    })
    add("binary_sensor", "tts_cache_hit", {
        "name": f"{title} TTS Cache Hit",
        "uniq_id": f"holiday_{device_name}_tts_cache_hit",
        "stat_t": f"{base}/tts/cache_hit", "pl_on": "ON", "pl_off": "OFF",
    })
    add("sensor", "tts_first_audio", {
        "name": f"{title} TTS First Audio",
        "uniq_id": f"holiday_{device_name}_tts_first_audio",
        "stat_t": f"{base}/tts/first_audio", "unit_of_measurement": "s",
    })
    add("sensor", "tts_greeting_first_audio", {
        "name": f"{title} Greeting First Audio",
        "uniq_id": f"holiday_{device_name}_tts_greeting_first_audio",
        "stat_t": f"{base}/tts/greeting_first_audio", "unit_of_measurement": "s",
    })
    add("sensor", "tts_speak_time", {
        "name": f"{title} TTS Speak Time",
        "uniq_id": f"holiday_{device_name}_tts_speak_time",
        "stat_t": f"{base}/tts/speak_time", "unit_of_measurement": "s",
    })
    add("sensor", "tts_audio_time", {
        "name": f"{title} TTS Audio Time",
        "uniq_id": f"holiday_{device_name}_tts_audio_time",
        "stat_t": f"{base}/tts/audio_time", "unit_of_measurement": "s",
    })
    add("sensor", "transcript", {
        "name": f"{title} Transcript", "uniq_id": f"holiday_{device_name}_transcript",
        "stat_t": f"{base}/transcript",
    })
    add("number", "eyes_dim", {
        "name": f"{title} Eyes Dim %", "uniq_id": f"holiday_{device_name}_eyes_dim",
        "cmd_t": f"{base}/eyes/dim/set", "stat_t": f"{base}/eyes/dim",
        "min": 0, "max": 100, "step": 1, "mode": "box", "unit_of_measurement": "%",
    })
    add("number", "eyes_full", {
        "name": f"{title} Eyes Full %", "uniq_id": f"holiday_{device_name}_eyes_full",
        "cmd_t": f"{base}/eyes/full/set", "stat_t": f"{base}/eyes/full",
        "min": 0, "max": 100, "step": 1, "mode": "box", "unit_of_measurement": "%",
    })
    add("number", "volume", {
        "name": f"{title} Volume %", "uniq_id": f"holiday_{device_name}_volume",
        "cmd_t": f"{base}/volume/set", "stat_t": f"{base}/volume",
        "min": 0, "max": 200, "step": 5, "mode": "box", "unit_of_measurement": "%",
    })
    add("switch", "motion_enabled", {
        "name": f"{title} Motion Enabled", "uniq_id": f"holiday_{device_name}_motion_enabled",
        "cmd_t": f"{base}/motion/enabled/set", "stat_t": f"{base}/motion/enabled",
        "pl_on": "ON", "pl_off": "OFF",
    })
    add("switch", "night_mode", {
        "name": f"{title} Night Mode", "uniq_id": f"holiday_{device_name}_night_mode",
        "cmd_t": f"{base}/night_mode/set", "stat_t": f"{base}/night_mode",
        "pl_on": "ON", "pl_off": "OFF",
    })

    # Clear the retained legacy button. MQTT buttons publish "PRESS", not text.
    messages.append((f"homeassistant/button/{device_name}/say/config", None))
    add("text", "say", {
        "name": f"{title} Say", "uniq_id": f"holiday_{device_name}_say_text",
        "cmd_t": f"{base}/say/set", "mode": "text", "min": 1, "max": 255,
    })
    add("button", "blink", {
        "name": f"{title} Blink", "uniq_id": f"holiday_{device_name}_blink_btn",
        "cmd_t": f"{base}/blink/set",
    })
    add("button", "flicker", {
        "name": f"{title} Flicker", "uniq_id": f"holiday_{device_name}_flicker_btn",
        "cmd_t": f"{base}/flicker/set",
    })
    add("button", "restart", {
        "name": f"{title} Restart Service", "uniq_id": f"holiday_{device_name}_restart_btn",
        "cmd_t": f"{base}/restart/set",
    })
    add("button", "motion_trigger", {
        "name": f"{title} Trigger Motion", "uniq_id": f"holiday_{device_name}_motion_trigger_btn",
        "cmd_t": f"{base}/motion/trigger/set",
    })
    return messages
