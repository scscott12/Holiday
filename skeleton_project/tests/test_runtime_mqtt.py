import unittest
from types import SimpleNamespace
from unittest import mock

import skeleton_all_in_one_mqtt as runtime


class MqttSecurityTests(unittest.TestCase):
    def test_tls_and_credentials_are_applied_before_connect(self):
        client = mock.Mock()
        with mock.patch.multiple(
            runtime,
            MQTT_USER="skeleton",
            MQTT_PASS="secret",
            MQTT_TLS=True,
            MQTT_CA_CERT="/tmp/ca.pem",
            MQTT_CERTFILE=None,
            MQTT_KEYFILE=None,
        ):
            runtime._configure_mqtt_security(client)

        client.username_pw_set.assert_called_once_with("skeleton", "secret")
        client.tls_set.assert_called_once_with(
            ca_certs="/tmp/ca.pem",
            certfile=None,
            keyfile=None,
        )

    def test_oversized_command_payload_is_rejected(self):
        message = SimpleNamespace(
            topic="holiday/skeleton/say/set",
            payload=b"x" * 65,
        )
        enqueue = mock.Mock()
        journal = mock.Mock()
        with mock.patch.multiple(
            runtime,
            MQTT_MAX_PAYLOAD_BYTES=64,
            _enqueue=enqueue,
            _event_journal_record=journal,
        ):
            runtime._on_message(None, None, message)

        enqueue.assert_not_called()
        journal.assert_called_once()


if __name__ == "__main__":
    unittest.main()
