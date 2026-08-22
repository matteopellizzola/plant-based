"""Dependency-free storage and MQTT contract helpers for the hub."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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

    @classmethod
    def from_environment(cls) -> "Settings":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        raw_ids = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
        allowed_ids = frozenset(int(value.strip()) for value in raw_ids.split(",") if value.strip())
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN non configurato")
        if not allowed_ids:
            raise ValueError("TELEGRAM_ALLOWED_USER_IDS deve contenere almeno un ID")
        return cls(
            mqtt_host=os.getenv("MQTT_HOST", "127.0.0.1"),
            mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
            mqtt_username=os.getenv("MQTT_USERNAME", ""),
            mqtt_password=os.getenv("MQTT_PASSWORD", ""),
            topic_prefix=os.getenv("MQTT_TOPIC_PREFIX", "plants").strip("/"),
            telegram_token=token,
            allowed_user_ids=allowed_ids,
            database_path=Path(os.getenv("DATABASE_PATH", "hub/data/plant_hub.sqlite3")),
        )


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
        with self.lock:
            self.connection.execute(
                """INSERT INTO node_metadata(node, name, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(node) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at""",
                (node, name.strip(), utc_now()),
            )
            self.connection.commit()

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
        if channel not in range(4):
            raise ValueError("channel deve essere compreso tra 0 e 3")
        with self.lock:
            self.connection.execute(
                """INSERT INTO plant_metadata
                   (node, channel, name, species, position, notes, threshold_percent, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(node, channel) DO UPDATE SET
                     name=excluded.name, species=excluded.species, position=excluded.position,
                     notes=excluded.notes, threshold_percent=excluded.threshold_percent,
                     updated_at=excluded.updated_at""",
                (node, channel, name.strip(), species.strip(), position.strip(), notes.strip(), threshold_percent, utc_now()),
            )
            self.connection.commit()

    def plants(self) -> list[tuple[str, int, str, str, str, str, float | None]]:
        with self.lock:
            return self.connection.execute(
                """SELECT node, channel, name, species, position, notes, threshold_percent
                   FROM plant_metadata ORDER BY node, channel"""
            ).fetchall()

    def find_plants(self, query: str) -> list[tuple[str, int, str, str, str, str, float | None]]:
        normalized_query = query.strip().casefold()
        return [plant for plant in self.plants() if plant[2].casefold() == normalized_query]

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

    def latest_measurements(self, node: str) -> dict[str, Any] | None:
        for current_node, kind, payload, _ in self.latest(node):
            if current_node == node and kind == "measurements":
                return payload
        return None

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
