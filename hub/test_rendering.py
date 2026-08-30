import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

try:
    from app import history_text, node_status_text, sparkline
    from core import Store
except ModuleNotFoundError as error:
    history_text = None
    node_status_text = None
    sparkline = None
    Store = None
    IMPORT_ERROR = error
else:
    IMPORT_ERROR = None


@unittest.skipIf(IMPORT_ERROR is not None, f"bot dependencies unavailable: {IMPORT_ERROR}")
class RenderingTests(unittest.TestCase):
    def test_sparkline_downsamples_and_handles_flat_values(self):
        self.assertEqual(len(sparkline(list(range(30)))), 18)
        self.assertEqual(sparkline([12, 12, 12]), "▄▄▄")
        self.assertEqual(sparkline([]), "n/d")

    def test_history_text_groups_metrics_and_includes_trends(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            store.save("node", "state", {"state": "online"})
            store.set_plant("node", 0, "Basilico")
            store.save(
                "node",
                "measurements",
                {
                    "air": {"valid": True, "temperature_c": 20, "humidity_percent": 45},
                    "light": {"valid": True, "lux": 120},
                    "soil": [{"channel": 0, "moisture_percent": 35}],
                },
            )
            text = history_text(store, "node", "24h")

            self.assertIn("🌡️ ARIA", text)
            self.assertIn("💡 LUCE", text)
            self.assertIn("🌱 UMIDITÀ TERRENO", text)
            self.assertIn("Andamento:", text)

    def test_node_status_text_has_identity_and_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            store.save("node", "state", {"state": "online"})
            store.save(
                "node",
                "measurements",
                {"air": {"valid": True, "temperature_c": 22, "humidity_percent": 57}},
            )
            text = node_status_text(store)

            self.assertIn("ID tecnico: node", text)
            self.assertIn("🌿 PIANTE", text)
            self.assertIn("🌡️ ARIA", text)
            self.assertIn("Umidità aria: media 57.0%", text)
            self.assertIn("💡 LUCE", text)


if __name__ == "__main__":
    unittest.main()
