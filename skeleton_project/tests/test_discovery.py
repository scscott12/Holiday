import unittest

from holiday_skeleton.discovery import discovery_messages


class DiscoveryTests(unittest.TestCase):
    def test_say_is_a_text_entity_and_legacy_button_is_removed(self):
        messages = dict(discovery_messages("skeleton"))

        self.assertIsNone(messages["homeassistant/button/skeleton/say/config"])
        text = messages["homeassistant/text/skeleton/say/config"]
        self.assertEqual(text["cmd_t"], "holiday/skeleton/say/set")

    def test_streaming_speech_latency_sensors_are_discovered(self):
        messages = dict(discovery_messages("skeleton"))

        self.assertEqual(
            messages["homeassistant/sensor/skeleton/tts_engine/config"]["stat_t"],
            "holiday/skeleton/tts/engine",
        )
        self.assertEqual(
            messages["homeassistant/sensor/skeleton/tts_first_audio/config"]["stat_t"],
            "holiday/skeleton/tts/first_audio",
        )

    def test_canned_speech_cache_sensors_are_discovered(self):
        messages = dict(discovery_messages("skeleton"))

        self.assertEqual(
            messages["homeassistant/sensor/skeleton/tts_cache_state/config"]["stat_t"],
            "holiday/skeleton/tts/cache_state",
        )
        self.assertEqual(
            messages["homeassistant/sensor/skeleton/tts_cache_entries/config"]["stat_t"],
            "holiday/skeleton/tts/cache_entries",
        )
        self.assertEqual(
            messages["homeassistant/binary_sensor/skeleton/tts_cache_hit/config"]["stat_t"],
            "holiday/skeleton/tts/cache_hit",
        )
        self.assertEqual(
            messages["homeassistant/sensor/skeleton/tts_greeting_first_audio/config"]["stat_t"],
            "holiday/skeleton/tts/greeting_first_audio",
        )

    def test_streaming_llm_latency_sensors_are_discovered(self):
        messages = dict(discovery_messages("skeleton"))

        self.assertEqual(
            messages["homeassistant/sensor/skeleton/llm_first_token/config"]["stat_t"],
            "holiday/skeleton/llm/first_token",
        )
        self.assertEqual(
            messages["homeassistant/sensor/skeleton/llm_first_phrase/config"]["stat_t"],
            "holiday/skeleton/llm/first_phrase",
        )
        self.assertEqual(
            messages["homeassistant/sensor/skeleton/llm_first_audio/config"]["stat_t"],
            "holiday/skeleton/llm/first_audio",
        )
        self.assertEqual(
            messages["homeassistant/sensor/skeleton/llm_memory_turns/config"]["stat_t"],
            "holiday/skeleton/llm/memory_turns",
        )

    def test_barge_in_state_and_metrics_are_discovered(self):
        messages = dict(discovery_messages("skeleton"))

        self.assertEqual(
            messages["homeassistant/binary_sensor/skeleton/barge_in_enabled/config"]["stat_t"],
            "holiday/skeleton/barge_in/enabled",
        )
        self.assertEqual(
            messages["homeassistant/binary_sensor/skeleton/barge_in_active/config"]["stat_t"],
            "holiday/skeleton/barge_in/active",
        )
        self.assertEqual(
            messages["homeassistant/sensor/skeleton/barge_in_latency/config"]["stat_t"],
            "holiday/skeleton/barge_in/latency",
        )

    def test_idle_life_controls_and_metrics_are_discovered(self):
        messages = dict(discovery_messages("skeleton"))

        self.assertEqual(
            messages["homeassistant/switch/skeleton/idle_life_enabled/config"]["cmd_t"],
            "holiday/skeleton/idle_life/enabled/set",
        )
        self.assertEqual(
            messages["homeassistant/binary_sensor/skeleton/idle_life_active/config"]["stat_t"],
            "holiday/skeleton/idle_life/active",
        )
        self.assertEqual(
            messages["homeassistant/sensor/skeleton/idle_life_interrupted/config"]["stat_t"],
            "holiday/skeleton/idle_life/interrupted",
        )

    def test_scene_controls_and_progress_are_discovered(self):
        messages = dict(discovery_messages("skeleton"))

        self.assertEqual(
            messages["homeassistant/text/skeleton/scene_play/config"]["cmd_t"],
            "holiday/skeleton/scene/play/set",
        )
        self.assertEqual(
            messages["homeassistant/button/skeleton/scene_stop/config"]["cmd_t"],
            "holiday/skeleton/scene/stop/set",
        )
        self.assertEqual(
            messages["homeassistant/binary_sensor/skeleton/scene_active/config"]["stat_t"],
            "holiday/skeleton/scene/active",
        )
        self.assertEqual(
            messages["homeassistant/sensor/skeleton/scene_step/config"]["stat_t"],
            "holiday/skeleton/scene/step",
        )
        self.assertEqual(
            messages["homeassistant/sensor/skeleton/scene_library/config"]["json_attr_t"],
            "holiday/skeleton/scene/library",
        )

    def test_personality_select_library_and_default_scene_are_discovered(self):
        messages = dict(discovery_messages(
            "skeleton", ("pirate", "graveyard_host", "pirate")
        ))

        selector = messages["homeassistant/select/skeleton/personality/config"]
        self.assertEqual(selector["cmd_t"], "holiday/skeleton/personality/set")
        self.assertEqual(selector["stat_t"], "holiday/skeleton/personality/active")
        self.assertEqual(selector["options"], ["pirate", "graveyard_host"])
        self.assertEqual(
            messages["homeassistant/sensor/skeleton/personality_library/config"]["json_attr_t"],
            "holiday/skeleton/personality/library",
        )
        self.assertEqual(
            messages["homeassistant/button/skeleton/personality_default_scene/config"]["cmd_t"],
            "holiday/skeleton/personality/default_scene/play/set",
        )

    def test_persistent_settings_state_is_discovered(self):
        messages = dict(discovery_messages("skeleton"))

        self.assertEqual(
            messages["homeassistant/sensor/skeleton/settings_state/config"]["stat_t"],
            "holiday/skeleton/settings/state",
        )
        self.assertEqual(
            messages["homeassistant/sensor/skeleton/settings_last_saved/config"]["stat_t"],
            "holiday/skeleton/settings/last_saved",
        )
        self.assertEqual(
            messages["homeassistant/sensor/skeleton/settings_last_error/config"]["stat_t"],
            "holiday/skeleton/settings/last_error",
        )

    def test_transactional_content_reload_is_discovered(self):
        messages = dict(discovery_messages("skeleton"))

        self.assertEqual(
            messages["homeassistant/button/skeleton/content_reload/config"]["cmd_t"],
            "holiday/skeleton/content/reload/set",
        )
        self.assertEqual(
            messages["homeassistant/binary_sensor/skeleton/content_reload_active/config"]["stat_t"],
            "holiday/skeleton/content_reload/active",
        )
        self.assertEqual(
            messages["homeassistant/sensor/skeleton/content_reload_last_duration/config"]["unit_of_measurement"],
            "s",
        )

    def test_controller_watchdog_state_is_discovered(self):
        messages = dict(discovery_messages("skeleton"))

        self.assertEqual(
            messages["homeassistant/binary_sensor/skeleton/watchdog_enabled/config"]["stat_t"],
            "holiday/skeleton/watchdog/enabled",
        )
        self.assertEqual(
            messages["homeassistant/sensor/skeleton/watchdog_state/config"]["stat_t"],
            "holiday/skeleton/watchdog/state",
        )
        self.assertEqual(
            messages["homeassistant/sensor/skeleton/watchdog_controller_age/config"]["unit_of_measurement"],
            "s",
        )
        self.assertEqual(
            messages["homeassistant/sensor/skeleton/watchdog_last_feed/config"]["device_class"],
            "timestamp",
        )

    def test_operator_self_test_controls_and_report_are_discovered(self):
        messages = dict(discovery_messages("skeleton"))

        self.assertEqual(
            messages["homeassistant/button/skeleton/self_test_run/config"]["cmd_t"],
            "holiday/skeleton/self_test/run/set",
        )
        self.assertEqual(
            messages["homeassistant/button/skeleton/self_test_stop/config"]["cmd_t"],
            "holiday/skeleton/self_test/stop/set",
        )
        self.assertEqual(
            messages["homeassistant/binary_sensor/skeleton/self_test_active/config"]["stat_t"],
            "holiday/skeleton/self_test/active",
        )
        self.assertEqual(
            messages["homeassistant/sensor/skeleton/self_test_last_result/config"]["json_attr_t"],
            "holiday/skeleton/self_test/report",
        )

    def test_health_summary_and_component_attributes_are_discovered(self):
        messages = dict(discovery_messages("skeleton"))

        health = messages["homeassistant/sensor/skeleton/health/config"]
        self.assertEqual(health["stat_t"], "holiday/skeleton/health/status")
        self.assertEqual(
            health["json_attr_t"], "holiday/skeleton/health/components"
        )
        self.assertEqual(
            messages["homeassistant/binary_sensor/skeleton/health_ok/config"]["stat_t"],
            "holiday/skeleton/health/ok",
        )

    def test_pi_telemetry_and_rolling_latency_are_discovered(self):
        messages = dict(discovery_messages("skeleton"))

        self.assertEqual(
            messages["homeassistant/sensor/skeleton/cpu_temperature/config"]["stat_t"],
            "holiday/skeleton/health/cpu_temperature",
        )
        self.assertEqual(
            messages["homeassistant/binary_sensor/skeleton/throttled/config"]["stat_t"],
            "holiday/skeleton/health/throttled",
        )
        self.assertEqual(
            messages["homeassistant/sensor/skeleton/health_response_first_audio_p95/config"]["stat_t"],
            "holiday/skeleton/health/latency/response_first_audio_p95",
        )


if __name__ == "__main__":
    unittest.main()
