import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import Settings, Store, topic_parts


class HubTests(unittest.TestCase):
    def test_topic_parts_accepts_node_topics_only(self):
        self.assertEqual(topic_parts("plants/plant-node-01/state", "plants"), ("plant-node-01", "state"))
        self.assertEqual(topic_parts("/plants/node/measurements/", "plants"), ("node", "measurements"))
        self.assertIsNone(topic_parts("plants/node/config", "plants"))

    def test_store_keeps_latest_payload_per_node_and_kind(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            store.save("node", "state", {"state": "online"})
            store.save("node", "state", {"state": "offline"})
            rows = store.latest("node")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][2]["state"], "offline")

    def test_settings_requires_token_and_authorized_users(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_ALLOWED_USER_IDS": ""}, clear=False):
            with self.assertRaises(ValueError):
                Settings.from_environment()
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_ALLOWED_USER_IDS": "12, 34"}, clear=False):
            settings = Settings.from_environment()
            self.assertEqual(settings.allowed_user_ids, frozenset({12, 34}))


if __name__ == "__main__":
    unittest.main()
