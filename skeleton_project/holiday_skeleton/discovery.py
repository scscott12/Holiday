"""Home Assistant MQTT discovery definitions.

Both the runtime and the standalone publisher import this module so the two
paths cannot silently drift apart.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple


DiscoveryMessage = Tuple[str, Optional[Dict[str, Any]]]


def discovery_messages(
    device_name: str,
    personality_names: Iterable[str] = (),
) -> List[DiscoveryMessage]:
    base = f"holiday/{device_name}"
    title = device_name.capitalize()
    personality_options = list(dict.fromkeys(
        str(name).strip().lower() for name in personality_names if str(name).strip()
    )) or ["legacy"]
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
    add("binary_sensor", "idle_life_active", {
        "name": f"{title} Idle Life Active",
        "uniq_id": f"holiday_{device_name}_idle_life_active",
        "stat_t": f"{base}/idle_life/active", "pl_on": "ON", "pl_off": "OFF",
    })
    add("sensor", "idle_life_state", {
        "name": f"{title} Idle Life State",
        "uniq_id": f"holiday_{device_name}_idle_life_state",
        "stat_t": f"{base}/idle_life/state",
    })
    add("sensor", "idle_life_last_action", {
        "name": f"{title} Idle Life Last Action",
        "uniq_id": f"holiday_{device_name}_idle_life_last_action",
        "stat_t": f"{base}/idle_life/last_action",
    })
    add("sensor", "idle_life_count", {
        "name": f"{title} Idle Life Count",
        "uniq_id": f"holiday_{device_name}_idle_life_count",
        "stat_t": f"{base}/idle_life/count",
    })
    add("sensor", "idle_life_interrupted", {
        "name": f"{title} Idle Life Interrupted",
        "uniq_id": f"holiday_{device_name}_idle_life_interrupted",
        "stat_t": f"{base}/idle_life/interrupted",
    })
    add("binary_sensor", "scene_active", {
        "name": f"{title} Scene Active",
        "uniq_id": f"holiday_{device_name}_scene_active",
        "stat_t": f"{base}/scene/active", "pl_on": "ON", "pl_off": "OFF",
    })
    add("sensor", "scene_state", {
        "name": f"{title} Scene State",
        "uniq_id": f"holiday_{device_name}_scene_state",
        "stat_t": f"{base}/scene/state",
    })
    add("sensor", "scene_current", {
        "name": f"{title} Current Scene",
        "uniq_id": f"holiday_{device_name}_scene_current",
        "stat_t": f"{base}/scene/current",
    })
    add("sensor", "scene_step", {
        "name": f"{title} Scene Step",
        "uniq_id": f"holiday_{device_name}_scene_step",
        "stat_t": f"{base}/scene/step",
    })
    add("sensor", "scene_library", {
        "name": f"{title} Scene Library",
        "uniq_id": f"holiday_{device_name}_scene_library",
        "stat_t": f"{base}/scene/library_count",
        "json_attr_t": f"{base}/scene/library",
    })
    add("sensor", "scene_count", {
        "name": f"{title} Scene Count",
        "uniq_id": f"holiday_{device_name}_scene_count",
        "stat_t": f"{base}/scene/count",
    })
    add("sensor", "scene_interrupted", {
        "name": f"{title} Scenes Interrupted",
        "uniq_id": f"holiday_{device_name}_scene_interrupted",
        "stat_t": f"{base}/scene/interrupted",
    })
    add("sensor", "scene_last_result", {
        "name": f"{title} Scene Last Result",
        "uniq_id": f"holiday_{device_name}_scene_last_result",
        "stat_t": f"{base}/scene/last_result",
    })
    add("sensor", "scene_last_error", {
        "name": f"{title} Scene Last Error",
        "uniq_id": f"holiday_{device_name}_scene_last_error",
        "stat_t": f"{base}/scene/last_error",
    })
    add("sensor", "scene_last_duration", {
        "name": f"{title} Scene Last Duration",
        "uniq_id": f"holiday_{device_name}_scene_last_duration",
        "stat_t": f"{base}/scene/last_duration",
        "unit_of_measurement": "s",
    })
    add("sensor", "personality_state", {
        "name": f"{title} Personality State",
        "uniq_id": f"holiday_{device_name}_personality_state",
        "stat_t": f"{base}/personality/state",
    })
    add("sensor", "personality_library", {
        "name": f"{title} Personality Library",
        "uniq_id": f"holiday_{device_name}_personality_library",
        "stat_t": f"{base}/personality/library_count",
        "json_attr_t": f"{base}/personality/library",
    })
    add("sensor", "personality_default_scene", {
        "name": f"{title} Personality Default Scene",
        "uniq_id": f"holiday_{device_name}_personality_default_scene",
        "stat_t": f"{base}/personality/default_scene",
    })
    add("sensor", "personality_last_result", {
        "name": f"{title} Personality Last Result",
        "uniq_id": f"holiday_{device_name}_personality_last_result",
        "stat_t": f"{base}/personality/last_result",
    })
    add("sensor", "personality_last_error", {
        "name": f"{title} Personality Last Error",
        "uniq_id": f"holiday_{device_name}_personality_last_error",
        "stat_t": f"{base}/personality/last_error",
    })
    add("sensor", "personality_switch_count", {
        "name": f"{title} Personality Switch Count",
        "uniq_id": f"holiday_{device_name}_personality_switch_count",
        "stat_t": f"{base}/personality/switch_count",
    })
    add("sensor", "settings_state", {
        "name": f"{title} Saved Settings State",
        "uniq_id": f"holiday_{device_name}_settings_state",
        "stat_t": f"{base}/settings/state",
        "icon": "mdi:content-save-cog",
    })
    add("sensor", "settings_last_saved", {
        "name": f"{title} Settings Last Saved",
        "uniq_id": f"holiday_{device_name}_settings_last_saved",
        "stat_t": f"{base}/settings/last_saved",
    })
    add("sensor", "settings_last_error", {
        "name": f"{title} Settings Last Error",
        "uniq_id": f"holiday_{device_name}_settings_last_error",
        "stat_t": f"{base}/settings/last_error",
    })
    add("sensor", "settings_save_count", {
        "name": f"{title} Settings Save Count",
        "uniq_id": f"holiday_{device_name}_settings_save_count",
        "stat_t": f"{base}/settings/save_count",
    })
    add("binary_sensor", "watchdog_enabled", {
        "name": f"{title} Watchdog Enabled",
        "uniq_id": f"holiday_{device_name}_watchdog_enabled",
        "stat_t": f"{base}/watchdog/enabled",
        "pl_on": "ON", "pl_off": "OFF",
    })
    add("sensor", "watchdog_state", {
        "name": f"{title} Watchdog State",
        "uniq_id": f"holiday_{device_name}_watchdog_state",
        "stat_t": f"{base}/watchdog/state",
        "icon": "mdi:shield-refresh",
    })
    add("sensor", "watchdog_controller_age", {
        "name": f"{title} Controller Heartbeat Age",
        "uniq_id": f"holiday_{device_name}_watchdog_controller_age",
        "stat_t": f"{base}/watchdog/controller_age",
        "unit_of_measurement": "s", "state_class": "measurement",
    })
    add("sensor", "watchdog_last_feed", {
        "name": f"{title} Watchdog Last Feed",
        "uniq_id": f"holiday_{device_name}_watchdog_last_feed",
        "stat_t": f"{base}/watchdog/last_feed",
        "device_class": "timestamp",
    })
    add("sensor", "watchdog_feed_count", {
        "name": f"{title} Watchdog Feed Count",
        "uniq_id": f"holiday_{device_name}_watchdog_feed_count",
        "stat_t": f"{base}/watchdog/feed_count",
        "state_class": "total_increasing",
    })
    add("sensor", "watchdog_last_error", {
        "name": f"{title} Watchdog Last Error",
        "uniq_id": f"holiday_{device_name}_watchdog_last_error",
        "stat_t": f"{base}/watchdog/last_error",
    })
    add("binary_sensor", "ready", {
        "name": f"{title} Ready", "uniq_id": f"holiday_{device_name}_ready",
        "stat_t": f"{base}/ready", "pl_on": "ON", "pl_off": "OFF",
    })
    add("binary_sensor", "health_ok", {
        "name": f"{title} Health OK",
        "uniq_id": f"holiday_{device_name}_health_ok",
        "stat_t": f"{base}/health/ok", "pl_on": "ON", "pl_off": "OFF",
    })
    add("sensor", "health", {
        "name": f"{title} Health",
        "uniq_id": f"holiday_{device_name}_health",
        "stat_t": f"{base}/health/status",
        "json_attr_t": f"{base}/health/components",
        "icon": "mdi:heart-pulse",
    })
    add("sensor", "health_reasons", {
        "name": f"{title} Health Reasons",
        "uniq_id": f"holiday_{device_name}_health_reasons",
        "stat_t": f"{base}/health/reasons",
    })
    add("sensor", "health_last_update", {
        "name": f"{title} Health Last Update",
        "uniq_id": f"holiday_{device_name}_health_last_update",
        "stat_t": f"{base}/health/last_update",
        "device_class": "timestamp",
    })
    add("sensor", "health_heartbeat", {
        "name": f"{title} Health Heartbeat",
        "uniq_id": f"holiday_{device_name}_health_heartbeat",
        "stat_t": f"{base}/health/heartbeat",
    })
    add("sensor", "cpu_temperature", {
        "name": f"{title} CPU Temperature",
        "uniq_id": f"holiday_{device_name}_cpu_temperature",
        "stat_t": f"{base}/health/cpu_temperature",
        "unit_of_measurement": "°C", "device_class": "temperature",
        "state_class": "measurement",
    })
    add("sensor", "cpu_load", {
        "name": f"{title} CPU Load 1m",
        "uniq_id": f"holiday_{device_name}_cpu_load",
        "stat_t": f"{base}/health/load_1m",
        "state_class": "measurement",
    })
    add("sensor", "memory_use", {
        "name": f"{title} Memory Use",
        "uniq_id": f"holiday_{device_name}_memory_use",
        "stat_t": f"{base}/health/memory_percent",
        "unit_of_measurement": "%", "state_class": "measurement",
    })
    add("sensor", "disk_use", {
        "name": f"{title} Disk Use",
        "uniq_id": f"holiday_{device_name}_disk_use",
        "stat_t": f"{base}/health/disk_percent",
        "unit_of_measurement": "%", "state_class": "measurement",
    })
    add("sensor", "uptime", {
        "name": f"{title} Uptime",
        "uniq_id": f"holiday_{device_name}_uptime",
        "stat_t": f"{base}/health/uptime",
        "unit_of_measurement": "s", "device_class": "duration",
        "state_class": "total_increasing",
    })
    add("binary_sensor", "throttled", {
        "name": f"{title} Pi Throttled",
        "uniq_id": f"holiday_{device_name}_throttled",
        "stat_t": f"{base}/health/throttled", "pl_on": "ON", "pl_off": "OFF",
        "device_class": "problem",
    })
    add("sensor", "throttle_flags", {
        "name": f"{title} Pi Throttle Flags",
        "uniq_id": f"holiday_{device_name}_throttle_flags",
        "stat_t": f"{base}/health/throttle_flags",
    })
    add("sensor", "audio_dropped_frames", {
        "name": f"{title} Audio Dropped Frames",
        "uniq_id": f"holiday_{device_name}_audio_dropped_frames",
        "stat_t": f"{base}/health/audio_dropped_frames",
        "state_class": "total_increasing",
    })
    latency_labels = {
        "tts_first_audio": "TTS First Audio",
        "greeting_first_audio": "Greeting First Audio",
        "response_first_audio": "Response First Audio",
        "llm_reply": "LLM Reply",
    }
    for metric, label in latency_labels.items():
        for statistic, suffix in (("average", "Rolling Average"), ("p95", "Rolling P95")):
            key = f"health_{metric}_{statistic}"
            add("sensor", key, {
                "name": f"{title} {label} {suffix}",
                "uniq_id": f"holiday_{device_name}_{key}",
                "stat_t": f"{base}/health/latency/{metric}_{statistic}",
                "unit_of_measurement": "s", "state_class": "measurement",
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
    add("switch", "idle_life_enabled", {
        "name": f"{title} Idle Life Enabled",
        "uniq_id": f"holiday_{device_name}_idle_life_enabled",
        "cmd_t": f"{base}/idle_life/enabled/set",
        "stat_t": f"{base}/idle_life/enabled",
        "pl_on": "ON", "pl_off": "OFF",
    })
    add("switch", "night_mode", {
        "name": f"{title} Night Mode", "uniq_id": f"holiday_{device_name}_night_mode",
        "cmd_t": f"{base}/night_mode/set", "stat_t": f"{base}/night_mode",
        "pl_on": "ON", "pl_off": "OFF",
    })
    add("select", "personality", {
        "name": f"{title} Personality",
        "uniq_id": f"holiday_{device_name}_personality",
        "cmd_t": f"{base}/personality/set",
        "stat_t": f"{base}/personality/active",
        "options": personality_options,
    })

    # Clear the retained legacy button. MQTT buttons publish "PRESS", not text.
    messages.append((f"homeassistant/button/{device_name}/say/config", None))
    add("text", "say", {
        "name": f"{title} Say", "uniq_id": f"holiday_{device_name}_say_text",
        "cmd_t": f"{base}/say/set", "mode": "text", "min": 1, "max": 255,
    })
    add("text", "scene_play", {
        "name": f"{title} Play Scene",
        "uniq_id": f"holiday_{device_name}_scene_play",
        "cmd_t": f"{base}/scene/play/set", "mode": "text", "min": 1, "max": 64,
    })
    add("button", "scene_stop", {
        "name": f"{title} Stop Scene",
        "uniq_id": f"holiday_{device_name}_scene_stop",
        "cmd_t": f"{base}/scene/stop/set",
    })
    add("button", "personality_default_scene", {
        "name": f"{title} Play Personality Scene",
        "uniq_id": f"holiday_{device_name}_personality_default_scene_play",
        "cmd_t": f"{base}/personality/default_scene/play/set",
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
