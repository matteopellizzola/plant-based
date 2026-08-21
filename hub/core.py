"""Dependency-free storage and MQTT contract helpers for the hub."""

from __future__ import annotations

import json
import os
import sqlite3
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
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS node_messages (
                node TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                received_at TEXT NOT NULL,
                PRIMARY KEY (node, kind)
            )"""
        )
        self.connection.commit()

    def save(self, node: str, kind: str, payload: dict[str, Any]) -> None:
        self.connection.execute(
            """INSERT INTO node_messages(node, kind, payload, received_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(node, kind) DO UPDATE SET
                 payload=excluded.payload, received_at=excluded.received_at""",
            (node, kind, json.dumps(payload, ensure_ascii=True), utc_now()),
        )
        self.connection.commit()

    def latest(self, node: str | None = None) -> list[tuple[str, str, dict[str, Any], str]]:
        query = "SELECT node, kind, payload, received_at FROM node_messages"
        parameters: tuple[str, ...] = ()
        if node:
            query += " WHERE node = ?"
            parameters = (node,)
        query += " ORDER BY node, kind"
        rows = self.connection.execute(query, parameters).fetchall()
        return [(row[0], row[1], json.loads(row[2]), row[3]) for row in rows]
