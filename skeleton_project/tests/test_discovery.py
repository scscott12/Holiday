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


if __name__ == "__main__":
    unittest.main()
