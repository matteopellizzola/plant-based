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

    def test_light_summary_uses_only_valid_nonnegative_readings(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            store.save("node", "measurements", {"light": {"valid": True, "lux": 120.5}})
            store.save("node", "measurements", {"light": {"valid": False, "lux": 999}})
            store.save("node", "measurements", {"light": {"valid": True, "lux": -1}})
            store.save("node", "measurements", {"light": {"valid": True, "lux": 240.5}})

            summary = store.light_summary("node")
            self.assertEqual(summary["count"], 2)
            self.assertEqual(summary["minimum"], 120.5)
            self.assertEqual(summary["maximum"], 240.5)
            self.assertEqual(summary["average"], 180.5)
            self.assertEqual(summary["latest"], 240.5)

    def test_store_saves_node_and_plant_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            store.save("node", "state", {"state": "online"})
            store.set_node("node", "Serra")
            store.set_plant("node", 0, "Basilico", "Ocimum", "cucina", "vaso piccolo")
            store.save("node", "measurements", {"soil": [{"channel": 0, "moisture_percent": 45}]})

            self.assertEqual(store.node_name("node"), "Serra")
            self.assertEqual(store.plants(), [("node", 0, "Basilico", "Ocimum", "cucina", "vaso piccolo", None)])
            self.assertEqual(store.find_plants("basilico")[0][2], "Basilico")
            self.assertEqual(store.latest_measurements("node")["soil"][0]["moisture_percent"], 45)
            self.assertEqual(store.rename_plant("BASILICO", "Basilico cucina"), 1)
            self.assertEqual(store.find_plants("basilico cucina")[0][2], "Basilico cucina")

    def test_store_rejects_unknown_nodes_and_occupied_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            with self.assertRaisesRegex(ValueError, "Nodo sconosciuto"):
                store.set_node("missing", "Balcone")
            store.save("node", "state", {"state": "online"})
            store.set_plant("node", 0, "Basilico")
            with self.assertRaisesRegex(ValueError, "Esiste già una pianta"):
                store.set_plant("node", 1, "Basilico")

    def test_store_rejects_empty_and_duplicate_names(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            store.save("node", "state", {"state": "online"})
            with self.assertRaises(ValueError):
                store.set_plant("node", 0, " ")
            store.set_plant("node", 0, "Basilico")
            with self.assertRaisesRegex(ValueError, "Esiste già"):
                store.set_plant("node", 1, "basilico")

    def test_store_reports_low_moisture_and_missing_readings(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            store.save("node", "state", {"state": "online"})
            store.set_plant("node", 0, "Basilico", threshold_percent=35)
            store.set_plant("node", 1, "Rosmarino")
            store.save("node", "measurements", {"soil": [{"channel": 0, "moisture_percent": 20}]})

            self.assertEqual(
                store.plant_alerts(),
                [
                    ("alert", "Basilico", "node", 0, "umidità del terreno 20.0% (soglia 35%)"),
                    ("info", "Rosmarino", "node", 1, "umidità del terreno non disponibile"),
                ],
            )

    def test_store_reports_no_alert_when_moisture_is_above_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            store.save("node", "state", {"state": "online"})
            store.set_plant("node", 0, "Basilico", threshold_percent=35)
            store.save("node", "measurements", {"soil": [{"channel": 0, "moisture_percent": 40}]})

            self.assertEqual(store.plant_alerts(), [])

    def test_watering_records_history_and_explicitly_closes_low_moisture_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            store.save("node", "state", {"state": "online"})
            store.set_plant("node", 0, "Basilico", threshold_percent=60)
            store.save("node", "measurements", {"soil": [{"channel": 0, "moisture_percent": 59.9}]})

            self.assertTrue(store.open_plant_warning("node", 0))
            store.save("node", "measurements", {"soil": [{"channel": 0, "moisture_percent": 60.1}]})
            self.assertIn("ancora aperto", store.plant_alerts()[0][-1])

            watered_at = store.record_watering("node", 0, 42)
            self.assertIsNone(store.plant_warning("node", 0))
            self.assertEqual(store.last_watering("node", 0), watered_at)

    def test_watering_all_plants_closes_each_warning_and_records_every_plant(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            store.save("node", "state", {"state": "online"})
            store.set_plant("node", 0, "Basilico", threshold_percent=60)
            store.set_plant("node", 1, "Rosmarino", threshold_percent=40)
            store.open_plant_warning("node", 0)
            store.open_plant_warning("node", 1)

            count, watered_at = store.record_watering_for_all_plants(42)

            self.assertEqual(count, 2)
            self.assertIsNone(store.plant_warning("node", 0))
            self.assertIsNone(store.plant_warning("node", 1))
            self.assertEqual(store.last_watering("node", 0), watered_at)
            self.assertEqual(store.last_watering("node", 1), watered_at)

    def test_store_updates_threshold_for_configured_plant(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            store.save("node", "state", {"state": "online"})
            store.set_plant("node", 0, "Basilico")

            store.set_plant_threshold("node", 0, 99)

            self.assertEqual(store.plants()[0][-1], 99)

    def test_store_moves_plant_and_preserves_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            store.save("node-1", "state", {"state": "online"})
            store.save("node-2", "state", {"state": "online"})
            store.set_plant("node-1", 0, "Basilico", "Ocimum", "cucina", "vaso piccolo", 35)

            store.move_plant("node-1", 0, "node-2", 2)

            self.assertEqual(store.channel_plant("node-1", 0), None)
            self.assertEqual(
                store.channel_plant("node-2", 2),
                ("node-2", 2, "Basilico", "Ocimum", "cucina", "vaso piccolo", 35),
            )

    def test_store_can_delete_plant_and_node_data_selectively(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            store.save("node-1", "state", {"state": "online"})
            store.save("node-1", "measurements", {"air": {"valid": True, "temperature_c": 22}})
            store.save("node-1", "measurements", {"soil": [{"channel": 0, "moisture_percent": 45}]})
            store.set_plant("node-1", 0, "Basilico")
            store.set_plant("node-1", 1, "Rosmarino")
            store.set_node("node-1", "Serra")

            self.assertTrue(store.delete_plant("node-1", 0))
            self.assertIsNone(store.channel_plant("node-1", 0))

            deleted = store.delete_node("node-1", clear_configuration=True, clear_last_state=True, clear_history=True)
            self.assertTrue(deleted["configuration"])
            self.assertTrue(deleted["state"])
            self.assertTrue(deleted["history"])
            self.assertEqual(store.known_nodes(), [])
            self.assertEqual(store.plants(), [])
            self.assertEqual(store.history("node-1"), [])

    def test_settings_requires_token_and_authorized_users(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_ALLOWED_USER_IDS": ""}, clear=False):
            with self.assertRaises(ValueError):
                Settings.from_environment()
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_ALLOWED_USER_IDS": "12, 34"}, clear=False):
            settings = Settings.from_environment()
            self.assertEqual(settings.allowed_user_ids, frozenset({12, 34}))

    def test_settings_parses_recap_and_quiet_hours(self):
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "token",
            "TELEGRAM_ALLOWED_USER_IDS": "12",
            "TIMEZONE": "Europe/Rome",
            "DAILY_RECAP_TIME": "08:15",
            "QUIET_HOURS_START": "22:00",
            "QUIET_HOURS_END": "07:30",
        }, clear=False):
            settings = Settings.from_environment()
        self.assertEqual(settings.daily_recap_time.isoformat(), "08:15:00")
        self.assertEqual(settings.quiet_hours_start.isoformat(), "22:00:00")
        self.assertEqual(settings.quiet_hours_end.isoformat(), "07:30:00")

    def test_settings_rejects_incomplete_quiet_hours(self):
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "token",
            "TELEGRAM_ALLOWED_USER_IDS": "12",
            "QUIET_HOURS_START": "22:00",
            "QUIET_HOURS_END": "",
        }, clear=False):
            with self.assertRaisesRegex(ValueError, "QUIET_HOURS_START"):
                Settings.from_environment()

    def test_store_persists_notification_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            defaults = Settings(
                mqtt_host="127.0.0.1", mqtt_port=1883, mqtt_username="", mqtt_password="",
                topic_prefix="plants", telegram_token="token", allowed_user_ids=frozenset({42}),
                database_path=Path(directory) / "hub.sqlite3",
            )
            configured = Settings(
                **{**defaults.__dict__, "timezone_name": "UTC",
                   "daily_recap_time": Settings.parse_clock("07:30", "ora"),
                   "quiet_hours_start": Settings.parse_clock("22:00", "inizio"),
                   "quiet_hours_end": Settings.parse_clock("07:00", "fine")}
            )
            store.save_notification_settings(configured)

            restored = store.notification_settings(defaults)
            self.assertEqual(restored.timezone_name, "UTC")
            self.assertEqual(restored.daily_recap_time.isoformat(), "07:30:00")
            self.assertEqual(restored.quiet_hours_start.isoformat(), "22:00:00")

    def test_store_manages_telegram_users(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            self.assertTrue(store.add_telegram_user(42))
            self.assertFalse(store.add_telegram_user(42))
            self.assertEqual(store.telegram_users(), [42])
            self.assertTrue(store.remove_telegram_user(42))
            self.assertFalse(store.remove_telegram_user(42))
            self.assertEqual(store.telegram_users(), [])


if __name__ == "__main__":
    unittest.main()
