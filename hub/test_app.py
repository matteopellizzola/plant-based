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

    def test_store_keeps_measurement_history_and_air_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            store.save("node", "measurements", {"air": {"valid": True, "temperature_c": 18, "humidity_percent": 40}})
            store.save("node", "measurements", {"air": {"valid": True, "temperature_c": 22, "humidity_percent": 60}})
            store.save("node", "measurements", {"air": {"valid": False, "temperature_c": 99}})
            store.set_plant("node", 0, "Basilico")
            store.save("node", "measurements", {"soil": [{"channel": 0, "moisture_percent": 30}]})
            store.save("node", "measurements", {"soil": [{"channel": 0, "moisture_percent": 70}]})

            self.assertEqual(len(store.history("node")), 5)
            summary = store.air_summary("node")
            self.assertEqual(summary["count"], 2)
            self.assertEqual(summary["minimum"], 18)
            self.assertEqual(summary["maximum"], 22)
            self.assertEqual(summary["average"], 20)
            self.assertEqual(summary["latest"], 22)
            self.assertEqual(summary["humidity_average"], 50)
            soil_summary = store.soil_summary("node", 0)
            self.assertEqual(soil_summary["minimum"], 30)
            self.assertEqual(soil_summary["maximum"], 70)
            self.assertEqual(soil_summary["average"], 50)

    def test_store_saves_node_and_plant_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            store.set_node("node", "Serra")
            store.set_plant("node", 0, "Basilico", "Ocimum", "cucina", "vaso piccolo")
            store.save("node", "measurements", {"soil": [{"channel": 0, "moisture_percent": 45}]})

            self.assertEqual(store.node_name("node"), "Serra")
            self.assertEqual(store.plants(), [("node", 0, "Basilico", "Ocimum", "cucina", "vaso piccolo", None)])
            self.assertEqual(store.find_plants("basilico")[0][2], "Basilico")
            self.assertEqual(store.latest_measurements("node")["soil"][0]["moisture_percent"], 45)
            self.assertEqual(store.rename_plant("BASILICO", "Basilico cucina"), 1)
            self.assertEqual(store.find_plants("basilico cucina")[0][2], "Basilico cucina")

    def test_settings_requires_token_and_authorized_users(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_ALLOWED_USER_IDS": ""}, clear=False):
            with self.assertRaises(ValueError):
                Settings.from_environment()
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_ALLOWED_USER_IDS": "12, 34"}, clear=False):
            settings = Settings.from_environment()
            self.assertEqual(settings.allowed_user_ids, frozenset({12, 34}))


if __name__ == "__main__":
    unittest.main()
