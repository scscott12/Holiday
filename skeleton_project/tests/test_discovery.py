import unittest

from holiday_skeleton.discovery import discovery_messages


class DiscoveryTests(unittest.TestCase):
    def test_say_is_a_text_entity_and_legacy_button_is_removed(self):
        messages = dict(discovery_messages("skeleton"))

        self.assertIsNone(messages["homeassistant/button/skeleton/say/config"])
        text = messages["homeassistant/text/skeleton/say/config"]
        self.assertEqual(text["cmd_t"], "holiday/skeleton/say/set")


if __name__ == "__main__":
    unittest.main()

