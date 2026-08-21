"""Raspberry Pi hub for plant nodes and Telegram commands."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from core import Settings, Store, topic_parts

LOGGER = logging.getLogger("plant_hub")


def user_allowed(update: Update, settings: Settings) -> bool:
    user = update.effective_user
    return user is not None and user.id in settings.allowed_user_ids


async def deny_unless_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings: Settings = context.application.bot_data["settings"]
    if user_allowed(update, settings):
        return True
    if update.effective_message:
        await update.effective_message.reply_text("Accesso non autorizzato.")
    LOGGER.warning("Richiesta Telegram rifiutata da user_id=%s", update.effective_user.id if update.effective_user else "unknown")
    return False


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message and update.effective_user:
        await update.effective_message.reply_text(f"Il tuo Telegram user ID è: {update.effective_user.id}")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await deny_unless_allowed(update, context):
        return
    store: Store = context.application.bot_data["store"]
    rows = store.latest()
    if not rows:
        text = "Nessuna misura ricevuta."
    else:
        lines = []
        for node, kind, payload, received_at in rows:
            state = payload.get("state", "")
            detail = f" stato={state}" if state else ""
            lines.append(f"{node}: {kind}{detail} ({received_at})")
        text = "\n".join(lines)
    await update.effective_message.reply_text(text)


async def set_calibration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await deny_unless_allowed(update, context):
        return
    if len(context.args) < 4:
        await update.effective_message.reply_text("Uso: /cal NODE CHANNEL dry|wet|threshold VALUE")
        return
    node, channel, field, value = context.args[:4]
    if channel not in {"0", "1", "2", "3"} or field not in {"dry", "wet", "threshold"}:
        await update.effective_message.reply_text("Canale 0..3 e campo dry, wet oppure threshold.")
        return
    try:
        numeric_value = float(value)
    except ValueError:
        await update.effective_message.reply_text("Il valore deve essere numerico.")
        return
    settings: Settings = context.application.bot_data["settings"]
    client: mqtt.Client = context.application.bot_data["mqtt"]
    topic = f"{settings.topic_prefix}/{node}/config"
    client.publish(topic, json.dumps({"channel": int(channel), field: numeric_value}), qos=1)
    await update.effective_message.reply_text(f"Configurazione inviata a {node}, A{channel}.")


def build_mqtt_client(settings: Settings, store: Store) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="plant-hub")
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password)

    def on_connect(client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        if reason_code == 0:
            client.subscribe(f"{settings.topic_prefix}/+/state", qos=1)
            client.subscribe(f"{settings.topic_prefix}/+/measurements", qos=1)
            LOGGER.info("MQTT connesso a %s:%s", settings.mqtt_host, settings.mqtt_port)
        else:
            LOGGER.error("Connessione MQTT rifiutata: %s", reason_code)

    def on_message(client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        parsed = topic_parts(message.topic, settings.topic_prefix)
        if not parsed:
            return
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            LOGGER.warning("Payload MQTT non valido su %s", message.topic)
            return
        if isinstance(payload, dict):
            store.save(parsed[0], parsed[1], payload)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=60)
    return client


def main() -> None:
    load_dotenv(Path(__file__).with_name(".env"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_environment()
    store = Store(settings.database_path)
    mqtt_client = build_mqtt_client(settings, store)
    mqtt_client.loop_start()
    application = Application.builder().token(settings.telegram_token).build()
    application.bot_data.update(settings=settings, store=store, mqtt=mqtt_client)
    application.add_handler(CommandHandler("whoami", whoami))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("cal", set_calibration))
    LOGGER.info("Hub avviato; utenti autorizzati: %d", len(settings.allowed_user_ids))
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
