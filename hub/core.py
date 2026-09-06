"""Dependency-free storage and MQTT contract helpers for the hub."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, replace
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MAX_NODE_NAME_LENGTH = 64
MAX_PLANT_NAME_LENGTH = 64
MAX_TEXT_LENGTH = 160


@dataclass(frozen=True)
class Settings:
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str
    topic_prefix: str
    telegram_token: str
    allowed_user_ids: frozenset[int]
    database_path: Path
    alert_check_interval_seconds: int = 60
    node_offline_after_seconds: int = 300
    timezone_name: str = "Europe/Rome"
    daily_recap_time: time = time(8, 0)
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None

    @classmethod
    def from_environment(cls) -> "Settings":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        raw_ids = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
        allowed_ids = frozenset(int(value.strip()) for value in raw_ids.split(",") if value.strip())
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN non configurato")
        if not allowed_ids:
            raise ValueError("TELEGRAM_ALLOWED_USER_IDS deve contenere almeno un ID")
        timezone_name = os.getenv("TIMEZONE", "Europe/Rome").strip()
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as error:
            raise ValueError("TIMEZONE deve essere un fuso orario IANA valido, per esempio Europe/Rome") from error
        daily_recap_time = cls.parse_clock(os.getenv("DAILY_RECAP_TIME", "08:00"), "DAILY_RECAP_TIME")
        quiet_start_raw = os.getenv("QUIET_HOURS_START", "").strip()
        quiet_end_raw = os.getenv("QUIET_HOURS_END", "").strip()
        if bool(quiet_start_raw) != bool(quiet_end_raw):
            raise ValueError("QUIET_HOURS_START e QUIET_HOURS_END devono essere entrambi configurati oppure entrambi vuoti")
        return cls(
            mqtt_host=os.getenv("MQTT_HOST", "127.0.0.1"),
            mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
            mqtt_username=os.getenv("MQTT_USERNAME", ""),
            mqtt_password=os.getenv("MQTT_PASSWORD", ""),
            topic_prefix=os.getenv("MQTT_TOPIC_PREFIX", "plants").strip("/"),
            telegram_token=token,
            allowed_user_ids=allowed_ids,
            database_path=Path(os.getenv("DATABASE_PATH", "hub/data/plant_hub.sqlite3")),
            alert_check_interval_seconds=max(15, int(os.getenv("ALERT_CHECK_INTERVAL_SECONDS", "60"))),
            node_offline_after_seconds=max(60, int(os.getenv("NODE_OFFLINE_AFTER_SECONDS", "300"))),
            timezone_name=timezone_name,
            daily_recap_time=daily_recap_time,
            quiet_hours_start=cls.parse_clock(quiet_start_raw, "QUIET_HOURS_START") if quiet_start_raw else None,
            quiet_hours_end=cls.parse_clock(quiet_end_raw, "QUIET_HOURS_END") if quiet_end_raw else None,
        )

    @staticmethod
    def parse_clock(value: str, variable: str) -> time:
        try:
            return time.fromisoformat(value.strip())
        except ValueError as error:
            raise ValueError(f"{variable} deve usare il formato HH:MM, per esempio 22:30") from error


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def topic_parts(topic: str, prefix: str) -> tuple[str, str] | None:
    parts = topic.strip("/").split("/")
    if len(parts) == 3 and parts[0] == prefix and parts[2] in {"state", "measurements"}:
        return parts[1], parts[2]
    return None


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS node_messages (
                node TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                received_at TEXT NOT NULL,
                PRIMARY KEY (node, kind)
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS measurement_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node TEXT NOT NULL,
                payload TEXT NOT NULL,
                received_at TEXT NOT NULL
            )"""
        )
        self.connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_measurement_history_node_time
               ON measurement_history(node, received_at)"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS node_metadata (
                node TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS plant_metadata (
                node TEXT NOT NULL,
                channel INTEGER NOT NULL CHECK(channel BETWEEN 0 AND 3),
                name TEXT NOT NULL,
                species TEXT NOT NULL DEFAULT '',
                position TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                threshold_percent REAL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (node, channel)
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS telegram_users (
                user_id INTEGER PRIMARY KEY,
                added_at TEXT NOT NULL
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS alert_states (
                alert_key TEXT PRIMARY KEY,
                active INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS app_preferences (
                preference_key TEXT PRIMARY KEY,
                preference_value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS plant_warnings (
                node TEXT NOT NULL,
                channel INTEGER NOT NULL CHECK(channel BETWEEN 0 AND 3),
                raised_at TEXT NOT NULL,
                PRIMARY KEY (node, channel)
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS plant_watering_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node TEXT NOT NULL,
                channel INTEGER NOT NULL CHECK(channel BETWEEN 0 AND 3),
                watered_at TEXT NOT NULL,
                recorded_by INTEGER
            )"""
        )
        self.connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_plant_watering_history_plant_time
               ON plant_watering_history(node, channel, watered_at DESC)"""
        )
        self.connection.commit()

    def save(self, node: str, kind: str, payload: dict[str, Any]) -> None:
        received_at = utc_now()
        with self.lock:
            self.connection.execute(
                """INSERT INTO node_messages(node, kind, payload, received_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(node, kind) DO UPDATE SET
                     payload=excluded.payload, received_at=excluded.received_at""",
                (node, kind, json.dumps(payload, ensure_ascii=True), received_at),
            )
            if kind == "measurements":
                self.connection.execute(
                    "INSERT INTO measurement_history(node, payload, received_at) VALUES (?, ?, ?)",
                    (node, json.dumps(payload, ensure_ascii=True), received_at),
                )
            self.connection.commit()

    def notification_settings(self, defaults: Settings) -> Settings:
        """Load notification preferences saved by an administrator, with env defaults."""
        with self.lock:
            rows = self.connection.execute(
                "SELECT preference_key, preference_value FROM app_preferences"
            ).fetchall()
        values = dict(rows)
        try:
            timezone_name = values.get("timezone_name", defaults.timezone_name)
            ZoneInfo(timezone_name)
            recap_time = Settings.parse_clock(values.get("daily_recap_time", defaults.daily_recap_time.isoformat()), "daily_recap_time")
            quiet_start_value = values.get("quiet_hours_start", "")
            quiet_end_value = values.get("quiet_hours_end", "")
            if bool(quiet_start_value) != bool(quiet_end_value):
                raise ValueError("fascia silenziosa incompleta")
            quiet_start = Settings.parse_clock(quiet_start_value, "quiet_hours_start") if quiet_start_value else None
            quiet_end = Settings.parse_clock(quiet_end_value, "quiet_hours_end") if quiet_end_value else None
            return replace(defaults, timezone_name=timezone_name, daily_recap_time=recap_time,
                           quiet_hours_start=quiet_start, quiet_hours_end=quiet_end)
        except (ValueError, ZoneInfoNotFoundError):
            return defaults

    def save_notification_settings(self, settings: Settings) -> None:
        values = {
            "timezone_name": settings.timezone_name,
            "daily_recap_time": settings.daily_recap_time.isoformat(timespec="minutes"),
            "quiet_hours_start": settings.quiet_hours_start.isoformat(timespec="minutes") if settings.quiet_hours_start else "",
            "quiet_hours_end": settings.quiet_hours_end.isoformat(timespec="minutes") if settings.quiet_hours_end else "",
        }
        with self.lock:
            self.connection.executemany(
                """INSERT INTO app_preferences(preference_key, preference_value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(preference_key) DO UPDATE SET preference_value=excluded.preference_value, updated_at=excluded.updated_at""",
                [(key, value, utc_now()) for key, value in values.items()],
            )
            self.connection.commit()

    def latest(self, node: str | None = None) -> list[tuple[str, str, dict[str, Any], str]]:
        with self.lock:
            query = "SELECT node, kind, payload, received_at FROM node_messages"
            parameters: tuple[str, ...] = ()
            if node:
                query += " WHERE node = ?"
                parameters = (node,)
            query += " ORDER BY node, kind"
            rows = self.connection.execute(query, parameters).fetchall()
            return [(row[0], row[1], json.loads(row[2]), row[3]) for row in rows]

    def history(
        self, node: str | None = None, since: str | None = None, limit: int = 1000
    ) -> list[tuple[str, dict[str, Any], str]]:
        """Return measurement payloads in chronological order."""
        query = "SELECT node, payload, received_at FROM measurement_history WHERE 1=1"
        parameters: list[Any] = []
        if node:
            query += " AND node = ?"
            parameters.append(node)
        if since:
            query += " AND received_at >= ?"
            parameters.append(since)
        query += " ORDER BY received_at DESC LIMIT ?"
        parameters.append(max(1, min(limit, 10000)))
        with self.lock:
            rows = self.connection.execute(query, parameters).fetchall()
        return [(row[0], json.loads(row[1]), row[2]) for row in reversed(rows)]

    def set_node(self, node: str, name: str) -> None:
        self.require_node(node)
        normalized_name = self.validate_text(name, "nome nodo", MAX_NODE_NAME_LENGTH)
        with self.lock:
            self.connection.execute(
                """INSERT INTO node_metadata(node, name, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(node) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at""",
                (node, normalized_name, utc_now()),
            )
            self.connection.commit()

    @staticmethod
    def validate_text(value: str, label: str, maximum: int) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{label} non può essere vuoto")
        if len(normalized) > maximum:
            raise ValueError(f"{label} troppo lungo (massimo {maximum} caratteri)")
        if any(ord(character) < 32 for character in normalized):
            raise ValueError(f"{label} contiene caratteri non gestibili")
        return normalized

    def known_nodes(self) -> list[tuple[str, str, str | None]]:
        with self.lock:
            rows = self.connection.execute(
                """SELECT nodes.node,
                          COALESCE(metadata.name, ''),
                          state.received_at
                   FROM (SELECT DISTINCT node FROM node_messages) AS nodes
                   LEFT JOIN node_metadata AS metadata ON metadata.node = nodes.node
                   LEFT JOIN node_messages AS state
                     ON state.node = nodes.node AND state.kind = 'state'
                   ORDER BY nodes.node"""
            ).fetchall()
        return [(node, name, received_at) for node, name, received_at in rows]

    def node_status(self, node: str) -> str:
        for current_node, kind, payload, _ in self.latest(node):
            if current_node == node and kind == "state":
                return str(payload.get("state", "offline"))
        return "offline"

    def require_node(self, node: str) -> None:
        if not node.strip() or not any(current == node for current, *_ in self.known_nodes()):
            raise ValueError(
                f"Nodo sconosciuto: {node}. Accendi il nodo e attendi il primo messaggio MQTT."
            )

    def channel_plant(self, node: str, channel: int) -> tuple[str, int, str, str, str, str, float | None] | None:
        for plant in self.plants():
            if plant[0] == node and plant[1] == channel:
                return plant
        return None

    def node_name(self, node: str) -> str:
        with self.lock:
            row = self.connection.execute(
                "SELECT name FROM node_metadata WHERE node = ?", (node,)
            ).fetchone()
        return row[0] if row else node

    def set_plant(
        self,
        node: str,
        channel: int,
        name: str,
        species: str = "",
        position: str = "",
        notes: str = "",
        threshold_percent: float | None = None,
    ) -> None:
        self.require_node(node)
        if channel not in range(4):
            raise ValueError("channel deve essere compreso tra 0 e 3")
        normalized_name = self.validate_text(name, "nome pianta", MAX_PLANT_NAME_LENGTH)
        normalized_species = self.validate_text(species, "specie", MAX_TEXT_LENGTH) if species.strip() else ""
        normalized_position = self.validate_text(position, "posizione", MAX_TEXT_LENGTH) if position.strip() else ""
        normalized_notes = self.validate_text(notes, "note", MAX_TEXT_LENGTH) if notes.strip() else ""
        existing_name = self.find_plants(normalized_name)
        if any(plant[:2] != (node, channel) for plant in existing_name):
            raise ValueError(f"Esiste già una pianta chiamata {normalized_name}")
        with self.lock:
            self.connection.execute(
                """INSERT INTO plant_metadata
                   (node, channel, name, species, position, notes, threshold_percent, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(node, channel) DO UPDATE SET
                     name=excluded.name, species=excluded.species, position=excluded.position,
                     notes=excluded.notes, threshold_percent=excluded.threshold_percent,
                     updated_at=excluded.updated_at""",
                (node, channel, normalized_name, normalized_species, normalized_position, normalized_notes, threshold_percent, utc_now()),
            )
            self.connection.commit()

    def set_plant_threshold(self, node: str, channel: int, threshold_percent: float) -> None:
        if not 0 <= threshold_percent <= 100:
            raise ValueError("La soglia deve essere compresa tra 0 e 100%")
        with self.lock:
            cursor = self.connection.execute(
                "UPDATE plant_metadata SET threshold_percent = ?, updated_at = ? WHERE node = ? AND channel = ?",
                (threshold_percent, utc_now(), node, channel),
            )
            self.connection.commit()
        if cursor.rowcount != 1:
            raise ValueError("Pianta non configurata per il canale indicato")

    def plants(self) -> list[tuple[str, int, str, str, str, str, float | None]]:
        with self.lock:
            return self.connection.execute(
                """SELECT node, channel, name, species, position, notes, threshold_percent
                   FROM plant_metadata ORDER BY node, channel"""
            ).fetchall()

    def find_plants(self, query: str) -> list[tuple[str, int, str, str, str, str, float | None]]:
        normalized_query = query.strip().casefold()
        return [plant for plant in self.plants() if plant[2].casefold() == normalized_query]

    def add_telegram_user(self, user_id: int) -> bool:
        if user_id <= 0:
            raise ValueError("L'ID Telegram deve essere un intero positivo")
        with self.lock:
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO telegram_users(user_id, added_at) VALUES (?, ?)",
                (user_id, utc_now()),
            )
            self.connection.commit()
        return cursor.rowcount == 1

    def remove_telegram_user(self, user_id: int) -> bool:
        with self.lock:
            cursor = self.connection.execute("DELETE FROM telegram_users WHERE user_id = ?", (user_id,))
            self.connection.commit()
        return cursor.rowcount == 1

    def telegram_users(self) -> list[int]:
        with self.lock:
            rows = self.connection.execute("SELECT user_id FROM telegram_users ORDER BY user_id").fetchall()
        return [row[0] for row in rows]

    def rename_plant(self, current_name: str, new_name: str) -> int:
        matches = self.find_plants(current_name)
        if len(matches) != 1 or not new_name.strip() or self.find_plants(new_name):
            return 0
        node, channel = matches[0][:2]
        with self.lock:
            self.connection.execute(
                """UPDATE plant_metadata SET name = ?, updated_at = ?
                   WHERE node = ? AND channel = ?""",
                (new_name.strip(), utc_now(), node, channel),
            )
            self.connection.commit()
        return 1

    def delete_plant(self, node: str, channel: int) -> bool:
        with self.lock:
            cursor = self.connection.execute(
                "DELETE FROM plant_metadata WHERE node = ? AND channel = ?",
                (node, channel),
            )
            self.connection.execute("DELETE FROM plant_warnings WHERE node = ? AND channel = ?", (node, channel))
            self.connection.execute("DELETE FROM plant_watering_history WHERE node = ? AND channel = ?", (node, channel))
            self.connection.execute("DELETE FROM alert_states WHERE alert_key = ?", (f"soil-low:{node}:{channel}",))
            self.connection.commit()
        return cursor.rowcount == 1

    def delete_node(
        self,
        node: str,
        *,
        clear_configuration: bool = True,
        clear_last_state: bool = True,
        clear_history: bool = True,
    ) -> dict[str, bool]:
        node = node.strip()
        if not node:
            raise ValueError("ID nodo mancante")
        results = {"configuration": False, "state": False, "history": False}
        with self.lock:
            if clear_configuration:
                config_cursor = self.connection.execute(
                    "DELETE FROM plant_metadata WHERE node = ?",
                    (node,),
                )
                metadata_cursor = self.connection.execute(
                    "DELETE FROM node_metadata WHERE node = ?",
                    (node,),
                )
                results["configuration"] = config_cursor.rowcount > 0 or metadata_cursor.rowcount > 0
                self.connection.execute("DELETE FROM plant_warnings WHERE node = ?", (node,))
                self.connection.execute("DELETE FROM plant_watering_history WHERE node = ?", (node,))
                self.connection.execute("DELETE FROM alert_states WHERE alert_key LIKE ?", (f"soil-low:{node}:%",))
            if clear_last_state:
                state_cursor = self.connection.execute(
                    "DELETE FROM node_messages WHERE node = ? AND kind = 'state'",
                    (node,),
                )
                results["state"] = state_cursor.rowcount > 0
            if clear_history:
                measurements_cursor = self.connection.execute(
                    "DELETE FROM node_messages WHERE node = ? AND kind = 'measurements'",
                    (node,),
                )
                history_cursor = self.connection.execute(
                    "DELETE FROM measurement_history WHERE node = ?",
                    (node,),
                )
                results["history"] = measurements_cursor.rowcount > 0 or history_cursor.rowcount > 0
            self.connection.commit()
        return results

    def move_plant(self, node: str, channel: int, target_node: str, target_channel: int) -> None:
        plant = self.channel_plant(node, channel)
        if plant is None:
            raise ValueError("Pianta non disponibile")
        self.require_node(target_node)
        if target_channel not in range(4):
            raise ValueError("channel deve essere compreso tra 0 e 3")
        if (node, channel) != (target_node, target_channel) and self.channel_plant(target_node, target_channel):
            raise ValueError("Esiste già una pianta sul canale selezionato")
        with self.lock:
            self.connection.execute(
                """INSERT INTO plant_metadata
                   (node, channel, name, species, position, notes, threshold_percent, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(node, channel) DO UPDATE SET
                   name=excluded.name, species=excluded.species, position=excluded.position,
                   notes=excluded.notes, threshold_percent=excluded.threshold_percent,
                   updated_at=excluded.updated_at""",
                (target_node, target_channel, plant[2], plant[3], plant[4], plant[5], plant[6], utc_now()),
            )
            if (node, channel) != (target_node, target_channel):
                self.connection.execute(
                    "UPDATE plant_warnings SET node = ?, channel = ? WHERE node = ? AND channel = ?",
                    (target_node, target_channel, node, channel),
                )
                self.connection.execute(
                    "UPDATE plant_watering_history SET node = ?, channel = ? WHERE node = ? AND channel = ?",
                    (target_node, target_channel, node, channel),
                )
                self.connection.execute(
                    "DELETE FROM plant_metadata WHERE node = ? AND channel = ?",
                    (node, channel),
                )
            self.connection.commit()

    def latest_measurements(self, node: str) -> dict[str, Any] | None:
        for current_node, kind, payload, _ in self.latest(node):
            if current_node == node and kind == "measurements":
                return payload
        return None

    def plant_alerts(self) -> list[tuple[str, str, str, int, str]]:
        """Return current and explicitly unacknowledged plant alerts."""
        alerts: list[tuple[str, str, str, int, str]] = []
        for node, channel, name, _, _, _, threshold in self.plants():
            payload = self.latest_measurements(node) or {}
            soil = payload.get("soil", [])
            reading = next(
                (item for item in soil if isinstance(item, dict) and item.get("channel") == channel),
                None,
            ) if isinstance(soil, list) else None
            moisture = reading.get("moisture_percent") if reading else None
            if not isinstance(moisture, (int, float)) or not 0 <= moisture <= 100:
                alerts.append(("info", name, node, channel, "umidità del terreno non disponibile"))
                if threshold is not None and self.plant_warning(node, channel) is not None:
                    alerts.append(("alert", name, node, channel,
                        f"avviso di umidità bassa ancora aperto; ultima lettura non disponibile (soglia {threshold:.0f}%)"))
            elif threshold is not None and self.plant_warning(node, channel) is not None:
                alerts.append(("alert", name, node, channel,
                    f"avviso di umidità bassa ancora aperto; ultima lettura {moisture:.1f}% (soglia {threshold:.0f}%)"))
            elif threshold is not None and moisture < threshold:
                alerts.append(("alert", name, node, channel, f"umidità del terreno {moisture:.1f}% (soglia {threshold:.0f}%)"))
        return alerts

    def plant_warning(self, node: str, channel: int) -> str | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT raised_at FROM plant_warnings WHERE node = ? AND channel = ?", (node, channel)
            ).fetchone()
        return row[0] if row else None

    def open_plant_warning(self, node: str, channel: int) -> bool:
        """Open a low-moisture warning once; it remains until watering is recorded."""
        if self.channel_plant(node, channel) is None:
            raise ValueError("Pianta non configurata per il canale indicato")
        with self.lock:
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO plant_warnings(node, channel, raised_at) VALUES (?, ?, ?)",
                (node, channel, utc_now()),
            )
            self.connection.commit()
        return cursor.rowcount == 1

    def record_watering(self, node: str, channel: int, recorded_by: int | None = None) -> str:
        """Record an explicit watering and acknowledge the outstanding warning."""
        if self.channel_plant(node, channel) is None:
            raise ValueError("Pianta non configurata per il canale indicato")
        watered_at = utc_now()
        with self.lock:
            self.connection.execute(
                "INSERT INTO plant_watering_history(node, channel, watered_at, recorded_by) VALUES (?, ?, ?, ?)",
                (node, channel, watered_at, recorded_by),
            )
            self.connection.execute("DELETE FROM plant_warnings WHERE node = ? AND channel = ?", (node, channel))
            self.connection.execute("DELETE FROM alert_states WHERE alert_key = ?", (f"soil-low:{node}:{channel}",))
            self.connection.commit()
        return watered_at

    def record_watering_for_all_plants(self, recorded_by: int | None = None) -> tuple[int, str]:
        """Record one explicit watering event for every configured plant."""
        watered_at = utc_now()
        with self.lock:
            plants = self.connection.execute(
                "SELECT node, channel FROM plant_metadata ORDER BY node, channel"
            ).fetchall()
            if not plants:
                return 0, watered_at
            self.connection.executemany(
                "INSERT INTO plant_watering_history(node, channel, watered_at, recorded_by) VALUES (?, ?, ?, ?)",
                [(node, channel, watered_at, recorded_by) for node, channel in plants],
            )
            self.connection.executemany(
                "DELETE FROM plant_warnings WHERE node = ? AND channel = ?", plants
            )
            self.connection.executemany(
                "DELETE FROM alert_states WHERE alert_key = ?",
                [(f"soil-low:{node}:{channel}",) for node, channel in plants],
            )
            self.connection.commit()
        return len(plants), watered_at

    def last_watering(self, node: str, channel: int) -> str | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT watered_at FROM plant_watering_history WHERE node = ? AND channel = ? ORDER BY id DESC LIMIT 1",
                (node, channel),
            ).fetchone()
        return row[0] if row else None

    def watering_advice(
        self,
        node: str,
        channel: int,
        timezone_name: str = "Europe/Rome",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Assess one plant conservatively; this never changes configuration or alerts."""
        plant = self.channel_plant(node, channel)
        if plant is None:
            raise ValueError("Pianta non configurata per il canale indicato")
        _, _, name, _, _, _, threshold = plant
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        zone = ZoneInfo(timezone_name)
        readings: list[tuple[datetime, float]] = []
        for _, payload, received_at in self.history(node, limit=10000):
            soil = payload.get("soil", [])
            if not isinstance(soil, list):
                continue
            value = next(
                (item.get("moisture_percent") for item in soil
                 if isinstance(item, dict) and item.get("channel") == channel),
                None,
            )
            if not isinstance(value, (int, float)) or not 0 <= value <= 100:
                continue
            try:
                measured_at = datetime.fromisoformat(received_at)
            except ValueError:
                continue
            if measured_at.tzinfo is None:
                measured_at = measured_at.replace(tzinfo=timezone.utc)
            readings.append((measured_at, float(value)))

        observed_days = len({measured_at.astimezone(zone).date() for measured_at, _ in readings})
        result: dict[str, Any] = {
            "name": name,
            "node": node,
            "channel": channel,
            "threshold": threshold,
            "observed_days": observed_days,
            "required_days": 14,
            "last_watering": self.last_watering(node, channel),
            "moisture": readings[-1][1] if readings else None,
        }
        if threshold is None:
            return result | {"action": "configure", "reason": "soglia di umidità non configurata"}
        if observed_days < 14:
            return result | {"action": "collecting", "reason": "storico insufficiente"}
        if not readings:
            return result | {"action": "unavailable", "reason": "nessuna lettura valida del terreno"}

        latest_age_hours = max(0, (current - readings[-1][0]).total_seconds() / 3600)
        result["latest_age_hours"] = latest_age_hours
        if latest_age_hours > 24:
            return result | {"action": "unavailable", "reason": "ultima lettura del terreno troppo vecchia"}

        last_watering = result["last_watering"]
        watering_age_hours: float | None = None
        if last_watering:
            try:
                watered_at = datetime.fromisoformat(last_watering)
                if watered_at.tzinfo is None:
                    watered_at = watered_at.replace(tzinfo=timezone.utc)
                watering_age_hours = max(0, (current - watered_at).total_seconds() / 3600)
            except ValueError:
                pass
        result["watering_age_hours"] = watering_age_hours
        if watering_age_hours is not None and watering_age_hours < 12:
            return result | {"action": "wait", "reason": "annaffiatura registrata nelle ultime 12 ore"}
        if result["moisture"] < threshold:
            return result | {"action": "water", "reason": "umidità sotto la soglia configurata"}
        return result | {"action": "monitor", "reason": "umidità sopra la soglia configurata"}

    def offline_node_alerts(self, after_seconds: int) -> list[tuple[str, str, str]]:
        now = datetime.now(timezone.utc)
        alerts: list[tuple[str, str, str]] = []
        for node, name, received_at in self.known_nodes():
            if not received_at:
                reason = "nessuna connessione MQTT confermata"
            elif self.node_status(node) == "offline":
                reason = "il nodo ha dichiarato lo stato offline"
            else:
                try:
                    last_seen = datetime.fromisoformat(received_at)
                except ValueError:
                    continue
                if (now - last_seen).total_seconds() <= after_seconds:
                    continue
                reason = f"nessun messaggio da {(now - last_seen).total_seconds() / 60:.0f} minuti"
            alerts.append((node, name or node, reason))
        return alerts

    def update_alert_state(self, alert_key: str, active: bool) -> tuple[bool, bool | None]:
        """Store an alert state and return (changed, previous state)."""
        with self.lock:
            row = self.connection.execute(
                "SELECT active FROM alert_states WHERE alert_key = ?", (alert_key,)
            ).fetchone()
            changed = row is None or bool(row[0]) != active
            self.connection.execute(
                """INSERT INTO alert_states(alert_key, active, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(alert_key) DO UPDATE SET active=excluded.active, updated_at=excluded.updated_at""",
                (alert_key, int(active), utc_now()),
            )
            self.connection.commit()
        return changed, None if row is None else bool(row[0])

    def air_summary(self, node: str, since: str | None = None) -> dict[str, float | int | None]:
        temperatures: list[float] = []
        humidity: list[float] = []
        readings = self.history(node, since)
        for _, payload, _ in readings:
            air = payload.get("air", {})
            if not isinstance(air, dict) or not air.get("valid"):
                continue
            if isinstance(air.get("temperature_c"), (int, float)):
                temperatures.append(float(air["temperature_c"]))
            if isinstance(air.get("humidity_percent"), (int, float)):
                humidity.append(float(air["humidity_percent"]))
        return self._summary(temperatures, humidity)

    def soil_summary(
        self, node: str, channel: int, since: str | None = None
    ) -> dict[str, float | int | None]:
        moisture: list[float] = []
        for _, payload, _ in self.history(node, since):
            soil = payload.get("soil", [])
            if not isinstance(soil, list):
                continue
            for item in soil:
                if not isinstance(item, dict) or item.get("channel") != channel:
                    continue
                value = item.get("moisture_percent")
                if isinstance(value, (int, float)) and 0 <= value <= 100:
                    moisture.append(float(value))
                break
        return self._summary(moisture, [])

    def light_summary(self, node: str, since: str | None = None) -> dict[str, float | int | None]:
        lux_values: list[float] = []
        for _, payload, _ in self.history(node, since):
            light = payload.get("light", {})
            if not isinstance(light, dict) or not light.get("valid"):
                continue
            value = light.get("lux")
            if isinstance(value, (int, float)) and value >= 0:
                lux_values.append(float(value))
        return self._summary(lux_values, [])

    def daily_environment(
        self, node: str, since: str | None = None, timezone_name: str = "Europe/Rome"
    ) -> list[dict[str, str | float | None]]:
        """Return local-calendar-day averages for air temperature, humidity and light."""
        zone = ZoneInfo(timezone_name)
        buckets: dict[str, dict[str, list[float]]] = {}
        for _, payload, received_at in self.history(node, since):
            try:
                timestamp = datetime.fromisoformat(received_at)
            except ValueError:
                continue
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            day = timestamp.astimezone(zone).date().isoformat()
            values = buckets.setdefault(day, {"temperature": [], "humidity": [], "light": []})
            air = payload.get("air", {})
            if isinstance(air, dict) and air.get("valid"):
                temperature = air.get("temperature_c")
                humidity = air.get("humidity_percent")
                if isinstance(temperature, (int, float)):
                    values["temperature"].append(float(temperature))
                if isinstance(humidity, (int, float)):
                    values["humidity"].append(float(humidity))
            light = payload.get("light", {})
            if isinstance(light, dict) and light.get("valid"):
                lux = light.get("lux")
                if isinstance(lux, (int, float)) and lux >= 0:
                    values["light"].append(float(lux))
        return [
            {
                "date": day,
                **{
                    metric: sum(values[metric]) / len(values[metric]) if values[metric] else None
                    for metric in ("temperature", "humidity", "light")
                },
            }
            for day, values in sorted(buckets.items())
        ]

    @staticmethod
    def _summary(values: list[float], secondary: list[float]) -> dict[str, float | int | None]:
        return {
            "count": len(values),
            "minimum": min(values) if values else None,
            "maximum": max(values) if values else None,
            "average": sum(values) / len(values) if values else None,
            "latest": values[-1] if values else None,
            "humidity_average": sum(secondary) / len(secondary) if secondary else None,
        }
