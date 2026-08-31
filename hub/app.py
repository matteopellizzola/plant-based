"""Raspberry Pi hub for plant nodes and Telegram commands."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)

from bot_ui import HELP_TEXT, cancel_keyboard, main_keyboard, navigation_keyboard, user_admin_keyboard
from conversation_state import wizard_token, wizard_value
from core import Settings, Store, topic_parts

LOGGER = logging.getLogger("plant_hub")


NODE_NAME, PLANT_NODE, PLANT_CHANNEL, PLANT_NAME, PLANT_SPECIES, PLANT_POSITION, PLANT_NOTES, PLANT_CONFIRM, CAL_NODE, CAL_CHANNEL, CAL_FIELD, CAL_VALUE, CAL_CONFIRM = range(13)
PLANT_RENAME_NAME, PLANT_RENAME_CONFIRM = range(13, 15)
PLANT_MOVE_NODE, PLANT_MOVE_CHANNEL, PLANT_MOVE_CONFIRM = range(15, 18)
USER_ID = 18
PLANT_WATER_CONFIRM = 19
RECAP_TIME, RECAP_TIMEZONE, QUIET_HOURS = range(20, 23)
BULK_WATER_CONFIRM = 23


def is_admin(update: Update, settings: Settings) -> bool:
    user = update.effective_user
    return user is not None and user.id in settings.allowed_user_ids


def user_allowed(update: Update, settings: Settings, store: Store | None = None) -> bool:
    user = update.effective_user
    if user is None:
        return False
    return user.id in settings.allowed_user_ids or (store is not None and user.id in store.telegram_users())


async def user_management_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    settings: Settings = context.application.bot_data["settings"]
    if not is_admin(update, settings):
        await query.answer("Accesso non autorizzato.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    await query.message.reply_text(
        "Incolla l'ID numerico dell'utente da autorizzare, oppure /annulla.",
        reply_markup=cancel_keyboard(),
    )
    return USER_ID


async def user_management_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    settings: Settings = context.application.bot_data["settings"]
    if not is_admin(update, settings):
        return ConversationHandler.END
    raw_id = update.effective_message.text.strip()
    if not raw_id.isdigit() or int(raw_id) <= 0:
        await update.effective_message.reply_text(
            "Inserisci un ID Telegram numerico positivo.", reply_markup=cancel_keyboard()
        )
        return USER_ID
    user_id = int(raw_id)
    if user_id in settings.allowed_user_ids:
        message = f"L'utente {user_id} è già amministratore tramite TELEGRAM_ALLOWED_USER_IDS."
    else:
        store: Store = context.application.bot_data["store"]
        message = (
            f"Utente {user_id} autorizzato."
            if store.add_telegram_user(user_id)
            else f"L'utente {user_id} era già autorizzato."
        )
    await update.effective_message.reply_text(message, reply_markup=user_admin_keyboard())
    return ConversationHandler.END


def build_user_management_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(user_management_start, pattern=r"^users:add$")],
        states={USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_management_add)]},
        fallbacks=[CallbackQueryHandler(cancel_wizard, pattern=r"^wizard:cancel$"), CommandHandler(["annulla", "cancel"], cancel_wizard)],
        conversation_timeout=900,
        per_user=True,
        per_chat=True,
    )


def notification_settings_text(settings: Settings) -> str:
    quiet = (
        f"{settings.quiet_hours_start.isoformat(timespec='minutes')} – {settings.quiet_hours_end.isoformat(timespec='minutes')}"
        if settings.quiet_hours_start and settings.quiet_hours_end else "disattivata"
    )
    return (
        "🔔 Recap e notifiche\n\n"
        f"Recap giornaliero: {settings.daily_recap_time.isoformat(timespec='minutes')}\n"
        f"Fuso orario: {settings.timezone_name}\n"
        f"Fascia silenziosa: {quiet}\n\n"
        "Gli alert critici restano attivi anche nella fascia silenziosa."
    )


def notification_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🕗 Cambia ora recap", callback_data="admin:recap:time")],
        [InlineKeyboardButton("🌍 Cambia fuso orario", callback_data="admin:recap:timezone")],
        [InlineKeyboardButton("🌙 Imposta fascia silenziosa", callback_data="admin:recap:quiet")],
        [InlineKeyboardButton("☀️ Disattiva fascia silenziosa", callback_data="admin:recap:quiet-off")],
        [InlineKeyboardButton("⬅️ Gestione utenti", callback_data="users:list")],
        [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
    ])


def reschedule_daily_recap(application: Application, settings: Settings) -> None:
    previous_job = application.bot_data.get("daily_recap_job")
    if previous_job is not None:
        previous_job.schedule_removal()
    job = application.job_queue.run_daily(
        daily_recap_job,
        time=settings.daily_recap_time.replace(tzinfo=ZoneInfo(settings.timezone_name)),
        name="daily-recap",
    )
    application.bot_data["daily_recap_job"] = job


def save_notification_settings(context: ContextTypes.DEFAULT_TYPE, settings: Settings) -> None:
    store: Store = context.application.bot_data["store"]
    store.save_notification_settings(settings)
    context.application.bot_data["settings"] = settings
    reschedule_daily_recap(context.application, settings)


async def recap_settings_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    settings: Settings = context.application.bot_data["settings"]
    if not is_admin(update, settings):
        await query.answer("Accesso non autorizzato.", show_alert=True)
        return ConversationHandler.END
    actions = {"admin:recap:time": (RECAP_TIME, "Inserisci l'ora del recap nel formato HH:MM, per esempio 07:30."),
               "admin:recap:timezone": (RECAP_TIMEZONE, "Inserisci un fuso IANA, per esempio Europe/Rome."),
               "admin:recap:quiet": (QUIET_HOURS, "Inserisci la fascia silenziosa nel formato HH:MM-HH:MM, per esempio 22:00-07:00.")}
    state, text = actions[query.data]
    await query.answer()
    await query.message.reply_text(text, reply_markup=cancel_keyboard())
    return state


async def recap_settings_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    settings: Settings = context.application.bot_data["settings"]
    raw = update.effective_message.text.strip()
    try:
        if context.user_data.get("conversation_state") == RECAP_TIME:
            updated = replace(settings, daily_recap_time=Settings.parse_clock(raw, "Ora"))
        elif context.user_data.get("conversation_state") == RECAP_TIMEZONE:
            ZoneInfo(raw)
            updated = replace(settings, timezone_name=raw)
        else:
            start, end = (part.strip() for part in raw.split("-", 1))
            updated = replace(settings, quiet_hours_start=Settings.parse_clock(start, "Inizio fascia"), quiet_hours_end=Settings.parse_clock(end, "Fine fascia"))
    except (ValueError, IndexError, ZoneInfoNotFoundError):
        await update.effective_message.reply_text("Valore non valido. Riprova oppure usa /annulla.", reply_markup=cancel_keyboard())
        return context.user_data.get("conversation_state", RECAP_TIME)
    save_notification_settings(context, updated)
    context.user_data.pop("conversation_state", None)
    await update.effective_message.reply_text("✅ Impostazioni salvate.\n\n" + notification_settings_text(updated), reply_markup=notification_settings_keyboard())
    return ConversationHandler.END


async def recap_settings_quiet_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    settings: Settings = context.application.bot_data["settings"]
    if not is_admin(update, settings):
        await query.answer("Accesso non autorizzato.", show_alert=True)
        return
    updated = replace(settings, quiet_hours_start=None, quiet_hours_end=None)
    save_notification_settings(context, updated)
    await query.answer()
    await query.message.reply_text("✅ Fascia silenziosa disattivata.", reply_markup=notification_settings_keyboard())


def build_recap_settings_handler() -> ConversationHandler:
    async def start_and_remember(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        state = await recap_settings_start(update, context)
        if state != ConversationHandler.END:
            context.user_data["conversation_state"] = state
        return state
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(start_and_remember, pattern=r"^admin:recap:(time|timezone|quiet)$")],
        states={
            RECAP_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, recap_settings_value)],
            RECAP_TIMEZONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, recap_settings_value)],
            QUIET_HOURS: [MessageHandler(filters.TEXT & ~filters.COMMAND, recap_settings_value)],
        },
        fallbacks=[CallbackQueryHandler(cancel_wizard, pattern=r"^wizard:cancel$"), CommandHandler(["annulla", "cancel"], cancel_wizard)],
        conversation_timeout=900, per_user=True, per_chat=True,
    )


async def cancel_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("wizard", None)
    context.user_data.pop("plant_action", None)
    context.user_data.pop("conversation_state", None)
    context.user_data.pop("bulk_watering", None)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("Operazione annullata.", reply_markup=main_keyboard())
    elif update.effective_message:
        await update.effective_message.reply_text("Operazione annullata.", reply_markup=main_keyboard())
    return ConversationHandler.END


async def node_wizard_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not user_allowed(update, context.application.bot_data["settings"], context.application.bot_data["store"]):
        await query.answer("Accesso non autorizzato.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    store: Store = context.application.bot_data["store"]
    nodes = store.known_nodes()
    if not nodes:
        await query.message.reply_text("Nessun nodo conosciuto. Accendi un nodo e attendi il primo messaggio MQTT.")
        return ConversationHandler.END
    buttons = []
    for node, name, _ in nodes:
        label = f"{node} · {name or 'senza nome'} · {store.node_status(node)}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"wizard:node:select:{wizard_token(context, node)}")])
    buttons.extend([
        [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")],
        [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
    ])
    await query.message.reply_text("Scegli il nodo da configurare:", reply_markup=InlineKeyboardMarkup(buttons))
    return NODE_NAME


async def node_wizard_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    token = query.data.rsplit(":", 1)[-1]
    node = wizard_value(context, token)
    store: Store = context.application.bot_data["store"]
    if not node or not any(item[0] == node for item in store.known_nodes()):
        await query.answer("Nodo non più disponibile.", show_alert=True)
        return ConversationHandler.END
    context.user_data["wizard"] = {"type": "node", "node": node}
    await query.answer()
    await query.message.reply_text(f"Nuovo nome per {node}:\nInvia il nome oppure /annulla.", reply_markup=cancel_keyboard())
    return NODE_NAME


async def node_wizard_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    wizard = context.user_data.get("wizard", {})
    store: Store = context.application.bot_data["store"]
    node = wizard.get("node")
    name = update.effective_message.text.strip()
    try:
        Store.validate_text(name, "nome nodo", 64)
    except ValueError as error:
        await update.effective_message.reply_text(str(error), reply_markup=cancel_keyboard())
        return NODE_NAME
    wizard["name"] = name
    await update.effective_message.reply_text(
        f"Confermi?\nNodo tecnico: {node}\nNuovo nome: {name}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Conferma", callback_data="wizard:node:confirm")], [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")], [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")]]),
    )
    return NODE_NAME


async def node_wizard_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    wizard = context.user_data.get("wizard", {})
    store: Store = context.application.bot_data["store"]
    try:
        store.set_node(wizard["node"], wizard["name"])
    except (KeyError, ValueError) as error:
        await query.answer(str(error), show_alert=True)
        return ConversationHandler.END
    await query.answer()
    await query.message.reply_text(f"Nome salvato: {wizard['node']} = {wizard['name']}", reply_markup=main_keyboard())
    context.user_data.pop("wizard", None)
    return ConversationHandler.END


async def plant_wizard_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not user_allowed(update, context.application.bot_data["settings"], context.application.bot_data["store"]):
        await query.answer("Accesso non autorizzato.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    store: Store = context.application.bot_data["store"]
    nodes = store.known_nodes()
    if not nodes:
        await query.message.reply_text("Nessun nodo conosciuto. Accendi un nodo e attendi il primo messaggio MQTT.")
        return ConversationHandler.END
    buttons = [[InlineKeyboardButton(f"{node} · {name or 'senza nome'} · {store.node_status(node)}", callback_data=f"wizard:plant:node:{wizard_token(context, node)}")] for node, name, _ in nodes]
    buttons.extend([
        [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")],
        [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
    ])
    await query.message.reply_text("Scegli il nodo della pianta:", reply_markup=InlineKeyboardMarkup(buttons))
    return PLANT_NODE


async def plant_wizard_node(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    node = wizard_value(context, query.data.rsplit(":", 1)[-1])
    store: Store = context.application.bot_data["store"]
    if not node or not any(item[0] == node for item in store.known_nodes()):
        await query.answer("Nodo non più disponibile.", show_alert=True)
        return ConversationHandler.END
    context.user_data["wizard"] = {"type": "plant", "node": node}
    context.user_data["wizard_state"] = PLANT_NAME
    buttons = []
    for channel in range(4):
        plant = store.channel_plant(node, channel)
        if plant is None:
            buttons.append([InlineKeyboardButton(f"A{channel} · libero", callback_data=f"wizard:plant:channel:{channel}")])
    buttons.extend([
        [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")],
        [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
    ])
    await query.answer()
    await query.message.reply_text("Scegli un canale libero:", reply_markup=InlineKeyboardMarkup(buttons))
    return PLANT_CHANNEL


async def plant_wizard_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    channel = int(query.data.rsplit(":", 1)[-1])
    node = context.user_data.get("wizard", {}).get("node")
    store: Store = context.application.bot_data["store"]
    if node is None or channel not in range(4) or store.channel_plant(node, channel):
        await query.answer("Canale non disponibile.", show_alert=True)
        return PLANT_CHANNEL
    context.user_data["wizard"]["channel"] = channel
    await query.answer()
    await query.message.reply_text("Nome della pianta (oppure /annulla):", reply_markup=cancel_keyboard())
    return PLANT_NAME


async def plant_wizard_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    wizard = context.user_data.get("wizard", {})
    state = context.user_data.get("wizard_state", PLANT_NAME)
    text = update.effective_message.text.strip()
    prompts = {PLANT_NAME: ("name", "Specie (opzionale):"), PLANT_SPECIES: ("species", "Posizione (opzionale):"), PLANT_POSITION: ("position", "Note (opzionali):"), PLANT_NOTES: ("notes", None)}
    field, next_prompt = prompts[state]
    if state == PLANT_NAME:
        try:
            Store.validate_text(text, "nome pianta", 64)
        except ValueError as error:
            await update.effective_message.reply_text(str(error), reply_markup=cancel_keyboard())
            return PLANT_NAME
    elif len(text) > 160:
        await update.effective_message.reply_text("Testo troppo lungo (massimo 160 caratteri).", reply_markup=cancel_keyboard())
        return state
    wizard[field] = text
    if next_prompt is None:
        await update.effective_message.reply_text(
            f"Confermi?\nNodo: {wizard['node']}\nCanale: A{wizard['channel']}\nPianta: {wizard['name']}\nSpecie: {wizard.get('species') or '-'}\nPosizione: {wizard.get('position') or '-'}\nNote: {wizard.get('notes') or '-'}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Conferma", callback_data="wizard:plant:confirm"), InlineKeyboardButton("✏️ Correggi note", callback_data="wizard:plant:edit-notes")], [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")], [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")]]),
        )
        return PLANT_CONFIRM
    context.user_data["wizard_state"] = state + 1
    keyboard = [[InlineKeyboardButton("⏭️ Salta", callback_data="wizard:skip")], [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")], [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")]] if state in {PLANT_SPECIES, PLANT_POSITION, PLANT_NOTES} else cancel_keyboard().inline_keyboard
    await update.effective_message.reply_text(next_prompt, reply_markup=InlineKeyboardMarkup(keyboard))
    return state + 1


async def plant_wizard_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    state = context.user_data.get("wizard_state", PLANT_SPECIES)
    wizard = context.user_data.get("wizard", {})
    prompts = {PLANT_SPECIES: ("species", "Posizione (opzionale):"), PLANT_POSITION: ("position", "Note (opzionali):"), PLANT_NOTES: ("notes", None)}
    field, next_prompt = prompts[state]
    wizard[field] = ""
    await query.answer()
    if next_prompt is None:
        await query.message.reply_text("Inserisci le note opzionali oppure conferma direttamente.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Conferma", callback_data="wizard:plant:confirm")], [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")], [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")]]))
        return PLANT_CONFIRM
    context.user_data["wizard_state"] = state + 1
    await query.message.reply_text(next_prompt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ Salta", callback_data="wizard:skip")], [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")], [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")]]))
    return state + 1


async def plant_wizard_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    wizard = context.user_data.get("wizard", {})
    store: Store = context.application.bot_data["store"]
    try:
        store.set_plant(wizard["node"], wizard["channel"], wizard["name"], wizard.get("species", ""), wizard.get("position", ""), wizard.get("notes", ""), wizard.get("threshold"))
    except (KeyError, ValueError) as error:
        await query.answer(str(error), show_alert=True)
        return ConversationHandler.END
    await query.answer()
    await query.message.reply_text(f"Vaso salvato: {wizard['name']} ({wizard['node']}, A{wizard['channel']})", reply_markup=main_keyboard())
    context.user_data.pop("wizard", None)
    context.user_data.pop("wizard_state", None)
    return ConversationHandler.END


async def plant_wizard_edit_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    context.user_data["wizard_state"] = PLANT_NOTES
    await query.answer()
    await query.message.reply_text(
        "Nuove note (oppure premi Salta):",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭️ Salta", callback_data="wizard:skip")],
            [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")],
            [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
        ]),
    )
    return PLANT_NOTES


async def calibration_wizard_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not user_allowed(update, context.application.bot_data["settings"], context.application.bot_data["store"]):
        await query.answer("Accesso non autorizzato.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    store: Store = context.application.bot_data["store"]
    nodes = store.known_nodes()
    if not nodes:
        await query.message.reply_text("Nessun nodo conosciuto. Accendi un nodo e attendi il primo messaggio MQTT.")
        return ConversationHandler.END
    buttons = [
        [InlineKeyboardButton(f"{node} · {name or 'senza nome'} · {store.node_status(node)}", callback_data=f"wizard:cal:node:{wizard_token(context, node)}")]
        for node, name, _ in nodes
    ]
    buttons.extend([
        [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")],
        [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
    ])
    await query.message.reply_text("Scegli il nodo da calibrare:", reply_markup=InlineKeyboardMarkup(buttons))
    return CAL_NODE


async def calibration_wizard_node(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    node = wizard_value(context, query.data.rsplit(":", 1)[-1])
    store: Store = context.application.bot_data["store"]
    if not node or not any(item[0] == node for item in store.known_nodes()):
        await query.answer("Nodo non più disponibile.", show_alert=True)
        return ConversationHandler.END
    context.user_data["wizard"] = {"type": "calibration", "node": node}
    await query.answer()
    buttons = [[InlineKeyboardButton(f"A{channel}", callback_data=f"wizard:cal:channel:{channel}")] for channel in range(4)]
    buttons.extend([
        [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")],
        [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
    ])
    await query.message.reply_text("Scegli il canale del sensore:", reply_markup=InlineKeyboardMarkup(buttons))
    return CAL_CHANNEL


async def calibration_wizard_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    channel_text = query.data.rsplit(":", 1)[-1]
    if channel_text not in {"0", "1", "2", "3"} or "wizard" not in context.user_data:
        await query.answer("Canale non disponibile.", show_alert=True)
        return CAL_CHANNEL
    context.user_data["wizard"]["channel"] = int(channel_text)
    await query.answer()
    buttons = [
        [InlineKeyboardButton("Dry", callback_data="wizard:cal:field:dry")],
        [InlineKeyboardButton("Wet", callback_data="wizard:cal:field:wet")],
        [InlineKeyboardButton("Soglia", callback_data="wizard:cal:field:threshold")],
        [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")],
        [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
    ]
    await query.message.reply_text("Scegli il parametro da impostare:", reply_markup=InlineKeyboardMarkup(buttons))
    return CAL_FIELD


async def calibration_wizard_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    field = query.data.rsplit(":", 1)[-1]
    if field not in {"dry", "wet", "threshold"} or "wizard" not in context.user_data:
        await query.answer("Parametro non disponibile.", show_alert=True)
        return CAL_FIELD
    context.user_data["wizard"]["field"] = field
    await query.answer()
    label = {"dry": "Dry", "wet": "Wet", "threshold": "soglia"}[field]
    await query.message.reply_text(f"Inserisci il valore numerico per {label} (oppure /annulla):", reply_markup=cancel_keyboard())
    return CAL_VALUE


async def calibration_wizard_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    wizard = context.user_data.get("wizard", {})
    try:
        value = float(update.effective_message.text.strip())
    except ValueError:
        await update.effective_message.reply_text("Non capisco il valore. Scrivi un numero, per esempio 35.", reply_markup=cancel_keyboard())
        return CAL_VALUE
    if wizard.get("field") == "threshold" and not 0 <= value <= 100:
        await update.effective_message.reply_text("La soglia deve essere compresa tra 0 e 100%.", reply_markup=cancel_keyboard())
        return CAL_VALUE
    wizard["value"] = value
    label = {"dry": "Dry", "wet": "Wet", "threshold": "Soglia"}[wizard["field"]]
    await update.effective_message.reply_text(
        f"Confermi la calibrazione?\nNodo: {wizard['node']}\nCanale: A{wizard['channel']}\nParametro: {label}\nValore: {value:g}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Conferma", callback_data="wizard:cal:confirm")],
            [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")],
            [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
        ]),
    )
    return CAL_CONFIRM


async def calibration_wizard_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    wizard = context.user_data.get("wizard", {})
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    if wizard.get("field") == "threshold":
        try:
            store.set_plant_threshold(wizard["node"], wizard["channel"], wizard["value"])
        except ValueError as error:
            await query.answer(str(error), show_alert=True)
            return ConversationHandler.END
    client: mqtt.Client = context.application.bot_data["mqtt"]
    topic = f"{settings.topic_prefix}/{wizard['node']}/config"
    client.publish(topic, json.dumps({"channel": wizard["channel"], wizard["field"]: wizard["value"]}), qos=1)
    await query.answer()
    await query.message.reply_text(
        f"✅ Calibrazione inviata. Canale A{wizard['channel']} del nodo {wizard['node']}: {wizard['field']} = {wizard['value']:g}.",
        reply_markup=main_keyboard(),
    )
    context.user_data.pop("wizard", None)
    return ConversationHandler.END


async def plant_rename_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    token = query.data.rsplit(":", 1)[-1]
    target = wizard_value(context, token)
    store: Store = context.application.bot_data["store"]
    if not target or ":" not in target:
        await query.answer("Questa pianta non è più disponibile.", show_alert=True)
        return ConversationHandler.END
    node, channel_text = target.rsplit(":", 1)
    matches = [plant for plant in store.plants() if plant[0] == node and str(plant[1]) == channel_text]
    if not matches:
        await query.answer("Questa pianta non è più disponibile.", show_alert=True)
        return ConversationHandler.END
    plant = matches[0]
    context.user_data["plant_action"] = {"action": "rename", "node": node, "channel": plant[1], "name": plant[2]}
    await query.answer()
    await query.message.reply_text(f"Nuovo nome per {plant[2]} (oppure /annulla):", reply_markup=cancel_keyboard())
    return PLANT_RENAME_NAME


async def plant_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    token = query.data.rsplit(":", 1)[-1]
    target = wizard_value(context, token)
    store: Store = context.application.bot_data["store"]
    if not target or ":" not in target:
        await query.answer("Questa pianta non è più disponibile.", show_alert=True)
        return
    node, channel_text = target.rsplit(":", 1)
    matches = [plant for plant in store.plants() if plant[0] == node and str(plant[1]) == channel_text]
    if not matches:
        await query.answer("Questa pianta non è più disponibile.", show_alert=True)
        return
    plant = matches[0]
    context.user_data["plant_action"] = {"action": "delete", "node": node, "channel": plant[1], "name": plant[2]}
    await query.answer()
    await query.message.reply_text(
        f"Confermi l'eliminazione della pianta?\nNodo tecnico: {node}\nPianta: {plant[2]}\nCanale: A{plant[1]}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ Conferma eliminazione", callback_data="plant-action:delete-confirm")],
            [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")],
            [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
        ]),
    )


async def plant_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    action = context.user_data.get("plant_action", {})
    store: Store = context.application.bot_data["store"]
    if action.get("action") != "delete":
        await query.answer("Nessuna pianta da eliminare.", show_alert=True)
        return
    node = action.get("node")
    channel = action.get("channel")
    if node is None or channel is None or store.channel_plant(node, channel) is None:
        await query.answer("Questa pianta non è più disponibile.", show_alert=True)
        return
    if not store.delete_plant(node, channel):
        await query.answer("Impossibile eliminare la pianta.", show_alert=True)
        return
    user_id = update.effective_user.id if update.effective_user else "unknown"
    LOGGER.warning("Operazione distruttiva da user_id=%s: elimina pianta %s [nodo=%s canale=A%s]", user_id, action.get("name"), node, channel)
    await query.answer()
    await query.message.reply_text(
        f"✅ Pianta eliminata: {action.get('name')} ({node}, A{channel})",
        reply_markup=main_keyboard(),
    )
    context.user_data.pop("plant_action", None)


async def plant_rename_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    action = context.user_data.get("plant_action", {})
    name = update.effective_message.text.strip()
    try:
        Store.validate_text(name, "nome pianta", 64)
    except ValueError as error:
        await update.effective_message.reply_text(str(error), reply_markup=cancel_keyboard())
        return PLANT_RENAME_NAME
    store: Store = context.application.bot_data["store"]
    if store.find_plants(name):
        await update.effective_message.reply_text("Esiste già una pianta con questo nome.", reply_markup=cancel_keyboard())
        return PLANT_RENAME_NAME
    action["new_name"] = name
    await update.effective_message.reply_text(
        f"Confermi la rinomina?\nNome attuale: {action['name']}\nNome nuovo: {name}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Conferma", callback_data="plant-action:rename-confirm")],
            [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")],
            [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
        ]),
    )
    return PLANT_RENAME_CONFIRM


async def plant_rename_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    action = context.user_data.get("plant_action", {})
    store: Store = context.application.bot_data["store"]
    current = store.channel_plant(action.get("node", ""), action.get("channel", -1))
    if not current or current[2] != action.get("name"):
        await query.answer("Questa pianta è cambiata o non è più disponibile.", show_alert=True)
        return ConversationHandler.END
    if store.rename_plant(action["name"], action["new_name"]) != 1:
        await query.answer("Impossibile rinominare la pianta.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    await query.message.reply_text(f"✅ Pianta rinominata: {action['name']} → {action['new_name']}", reply_markup=main_keyboard())
    context.user_data.pop("plant_action", None)
    return ConversationHandler.END


async def plant_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    target = wizard_value(context, query.data.rsplit(":", 1)[-1])
    store: Store = context.application.bot_data["store"]
    plant = next((item for item in store.plants() if target == f"{item[0]}:{item[1]}"), None)
    if plant is None:
        await query.answer("Questa pianta non è più disponibile.", show_alert=True)
        return ConversationHandler.END
    node, channel, name, species, position, notes, threshold = plant
    context.user_data["wizard"] = {
        "type": "plant_edit", "node": node, "channel": channel, "name": name,
        "species": species, "position": position, "notes": notes, "threshold": threshold,
    }
    context.user_data["wizard_state"] = PLANT_NAME
    await query.answer()
    await query.message.reply_text(f"Nuovo nome della pianta (attuale: {name}, oppure /annulla):", reply_markup=cancel_keyboard())
    return PLANT_NAME


async def plant_move_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    target = wizard_value(context, query.data.rsplit(":", 1)[-1])
    store: Store = context.application.bot_data["store"]
    plant = next((item for item in store.plants() if target == f"{item[0]}:{item[1]}"), None)
    if plant is None:
        await query.answer("Questa pianta non è più disponibile.", show_alert=True)
        return ConversationHandler.END
    context.user_data["plant_action"] = {"action": "move", "node": plant[0], "channel": plant[1], "name": plant[2]}
    buttons = [
        [InlineKeyboardButton(f"{node} · {name or 'senza nome'}", callback_data=f"plant-action:move-node:{wizard_token(context, node)}")]
        for node, name, _ in store.known_nodes()
    ]
    buttons.extend([
        [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")],
        [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
    ])
    await query.answer()
    await query.message.reply_text("Scegli il nuovo nodo:", reply_markup=InlineKeyboardMarkup(buttons))
    return PLANT_MOVE_NODE


async def plant_move_node(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    node = wizard_value(context, query.data.rsplit(":", 1)[-1])
    store: Store = context.application.bot_data["store"]
    if not node or not any(item[0] == node for item in store.known_nodes()):
        await query.answer("Nodo non più disponibile.", show_alert=True)
        return ConversationHandler.END
    action = context.user_data.get("plant_action", {})
    action["target_node"] = node
    buttons = []
    for channel in range(4):
        occupied = store.channel_plant(node, channel)
        if occupied is None or (node == action.get("node") and channel == action.get("channel")):
            buttons.append([InlineKeyboardButton(f"A{channel}", callback_data=f"plant-action:move-channel:{wizard_token(context, f'{node}:{channel}')}")])
    buttons.extend([
        [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")],
        [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
    ])
    await query.answer()
    await query.message.reply_text("Scegli il nuovo canale libero:", reply_markup=InlineKeyboardMarkup(buttons))
    return PLANT_MOVE_CHANNEL


async def plant_move_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    target = wizard_value(context, query.data.rsplit(":", 1)[-1])
    if not target or ":" not in target:
        await query.answer("Canale non disponibile.", show_alert=True)
        return PLANT_MOVE_CHANNEL
    node, channel_text = target.rsplit(":", 1)
    action = context.user_data.get("plant_action", {})
    store: Store = context.application.bot_data["store"]
    channel = int(channel_text)
    if channel not in range(4) or store.channel_plant(node, channel) and (node, channel) != (action.get("node"), action.get("channel")):
        await query.answer("Canale non disponibile.", show_alert=True)
        return PLANT_MOVE_CHANNEL
    action["target_node"] = node
    action["target_channel"] = channel
    await query.answer()
    await query.message.reply_text(
        f"Confermi lo spostamento di {action['name']}?\nDa: {action['node']} A{action['channel']}\nA: {node} A{channel}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Conferma", callback_data="plant-action:move-confirm")],
            [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")],
            [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
        ]),
    )
    return PLANT_MOVE_CONFIRM


async def plant_move_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    action = context.user_data.get("plant_action", {})
    store: Store = context.application.bot_data["store"]
    try:
        store.move_plant(action["node"], action["channel"], action["target_node"], action["target_channel"])
    except (KeyError, ValueError) as error:
        await query.answer(str(error), show_alert=True)
        return ConversationHandler.END
    await query.answer()
    await query.message.reply_text(
        f"✅ Pianta spostata: {action['name']} → {action['target_node']} A{action['target_channel']}",
        reply_markup=main_keyboard(),
    )
    context.user_data.pop("plant_action", None)
    return ConversationHandler.END


async def plant_watering_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    store: Store = context.application.bot_data["store"]
    target = wizard_value(context, query.data.rsplit(":", 1)[-1])
    plant = next((item for item in store.plants() if target == f"{item[0]}:{item[1]}"), None)
    if plant is None:
        await query.answer("Questa pianta non è più disponibile.", show_alert=True)
        return ConversationHandler.END
    context.user_data["plant_action"] = {"action": "water", "node": plant[0], "channel": plant[1], "name": plant[2]}
    await query.answer()
    await query.message.reply_text(
        f"Confermi di aver annaffiato {plant[2]}?\nL'azione verrà registrata e chiuderà l'eventuale avviso aperto.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💧 Conferma annaffiatura", callback_data="plant-action:water-confirm")],
            [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")],
        ]),
    )
    return PLANT_WATER_CONFIRM


async def plant_watering_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    action = context.user_data.get("plant_action", {})
    store: Store = context.application.bot_data["store"]
    if action.get("action") != "water" or store.channel_plant(action.get("node", ""), action.get("channel", -1)) is None:
        await query.answer("Questa pianta non è più disponibile.", show_alert=True)
        return ConversationHandler.END
    user_id = update.effective_user.id if update.effective_user else None
    watered_at = store.record_watering(action["node"], action["channel"], user_id)
    await query.answer()
    await query.message.reply_text(
        f"💧 Annaffiatura registrata per {action['name']} alle {format_local_time(watered_at)}."
        " L'eventuale avviso di umidità bassa è stato chiuso.",
        reply_markup=main_keyboard(),
    )
    context.user_data.pop("plant_action", None)
    return ConversationHandler.END


def build_wizard_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(node_wizard_start, pattern=r"^wizard:node:start$"),
            CallbackQueryHandler(plant_wizard_start, pattern=r"^wizard:plant:start$"),
            CallbackQueryHandler(calibration_wizard_start, pattern=r"^wizard:cal:start$"),
        ],
        states={
            NODE_NAME: [
                CallbackQueryHandler(node_wizard_select, pattern=r"^wizard:node:select:"),
                CallbackQueryHandler(node_wizard_confirm, pattern=r"^wizard:node:confirm$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, node_wizard_name),
            ],
            PLANT_NODE: [CallbackQueryHandler(plant_wizard_node, pattern=r"^wizard:plant:node:")],
            PLANT_CHANNEL: [CallbackQueryHandler(plant_wizard_channel, pattern=r"^wizard:plant:channel:")],
            PLANT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, plant_wizard_text)],
            PLANT_SPECIES: [CallbackQueryHandler(plant_wizard_skip, pattern=r"^wizard:skip$"), MessageHandler(filters.TEXT & ~filters.COMMAND, plant_wizard_text)],
            PLANT_POSITION: [CallbackQueryHandler(plant_wizard_skip, pattern=r"^wizard:skip$"), MessageHandler(filters.TEXT & ~filters.COMMAND, plant_wizard_text)],
            PLANT_NOTES: [CallbackQueryHandler(plant_wizard_skip, pattern=r"^wizard:skip$"), MessageHandler(filters.TEXT & ~filters.COMMAND, plant_wizard_text)],
            PLANT_CONFIRM: [
                CallbackQueryHandler(plant_wizard_confirm, pattern=r"^wizard:plant:confirm$"),
                CallbackQueryHandler(plant_wizard_edit_notes, pattern=r"^wizard:plant:edit-notes$"),
            ],
            CAL_NODE: [CallbackQueryHandler(calibration_wizard_node, pattern=r"^wizard:cal:node:")],
            CAL_CHANNEL: [CallbackQueryHandler(calibration_wizard_channel, pattern=r"^wizard:cal:channel:")],
            CAL_FIELD: [CallbackQueryHandler(calibration_wizard_field, pattern=r"^wizard:cal:field:")],
            CAL_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, calibration_wizard_value)],
            CAL_CONFIRM: [CallbackQueryHandler(calibration_wizard_confirm, pattern=r"^wizard:cal:confirm$")],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_wizard, pattern=r"^wizard:cancel$"),
            CallbackQueryHandler(cancel_wizard, pattern=r"^menu:home$"),
            CommandHandler(["annulla", "cancel", "start", "help", "storico", "cal", "calibra", "node", "plant"], cancel_wizard),
        ],
        conversation_timeout=900,
        per_user=True,
        per_chat=True,
    )


def build_plant_action_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(plant_rename_start, pattern=r"^plant-action:rename:"),
            CallbackQueryHandler(plant_edit_start, pattern=r"^plant-action:edit:"),
            CallbackQueryHandler(plant_move_start, pattern=r"^plant-action:move:"),
            CallbackQueryHandler(plant_watering_start, pattern=r"^plant-action:water:"),
        ],
        states={
            PLANT_RENAME_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, plant_rename_name)],
            PLANT_RENAME_CONFIRM: [CallbackQueryHandler(plant_rename_confirm, pattern=r"^plant-action:rename-confirm$")],
            PLANT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, plant_wizard_text)],
            PLANT_SPECIES: [CallbackQueryHandler(plant_wizard_skip, pattern=r"^wizard:skip$"), MessageHandler(filters.TEXT & ~filters.COMMAND, plant_wizard_text)],
            PLANT_POSITION: [CallbackQueryHandler(plant_wizard_skip, pattern=r"^wizard:skip$"), MessageHandler(filters.TEXT & ~filters.COMMAND, plant_wizard_text)],
            PLANT_NOTES: [CallbackQueryHandler(plant_wizard_skip, pattern=r"^wizard:skip$"), MessageHandler(filters.TEXT & ~filters.COMMAND, plant_wizard_text)],
            PLANT_CONFIRM: [CallbackQueryHandler(plant_wizard_confirm, pattern=r"^wizard:plant:confirm$")],
            PLANT_MOVE_NODE: [CallbackQueryHandler(plant_move_node, pattern=r"^plant-action:move-node:")],
            PLANT_MOVE_CHANNEL: [CallbackQueryHandler(plant_move_channel, pattern=r"^plant-action:move-channel:")],
            PLANT_MOVE_CONFIRM: [CallbackQueryHandler(plant_move_confirm, pattern=r"^plant-action:move-confirm$")],
            PLANT_WATER_CONFIRM: [CallbackQueryHandler(plant_watering_confirm, pattern=r"^plant-action:water-confirm$")],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_wizard, pattern=r"^wizard:cancel$"),
            CallbackQueryHandler(cancel_wizard, pattern=r"^menu:home$"),
            CommandHandler(["annulla", "cancel"], cancel_wizard),
        ],
        conversation_timeout=900,
        per_user=True,
        per_chat=True,
    )


async def deny_unless_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    if user_allowed(update, settings, store):
        return True
    if update.effective_message:
        await update.effective_message.reply_text("Accesso non autorizzato.")
    LOGGER.warning("Richiesta Telegram rifiutata da user_id=%s", update.effective_user.id if update.effective_user else "unknown")
    return False


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message and update.effective_user:
        await update.effective_message.reply_text(f"Il tuo Telegram user ID è: {update.effective_user.id}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    if not user_allowed(update, settings, store):
        await update.effective_message.reply_text(
            "Ciao! Questo bot è protetto. Usa /whoami per conoscere il tuo ID Telegram "
            "e chiedi all'amministratore di autorizzarti."
        )
        return
    await update.effective_message.reply_text(
        "Ciao! Ti aiuto a controllare le tue piante.",
        reply_markup=main_keyboard(is_admin(update, settings)),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await deny_unless_allowed(update, context):
        return
    await update.effective_message.reply_text(HELP_TEXT, reply_markup=main_keyboard(is_admin(update, context.application.bot_data["settings"])))


async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    settings: Settings = context.application.bot_data["settings"]
    if not user_allowed(update, settings, store := context.application.bot_data["store"]):
        await query.answer("Accesso non autorizzato.", show_alert=True)
        return
    await query.answer()
    if query.data in {"users:list", "users:remove"}:
        if not is_admin(update, settings):
            await query.answer("Accesso non autorizzato.", show_alert=True)
            return
        if query.data == "users:list":
            admin_ids = sorted(settings.allowed_user_ids)
            managed_ids = store.telegram_users()
            lines = ["👥 Utenti autorizzati", "", "Amministratori (.env):"]
            lines.extend(f"• {user_id}" for user_id in admin_ids)
            lines.append("",)
            lines.append("Utenti aggiunti:")
            lines.extend(f"• {user_id}" for user_id in managed_ids) if managed_ids else lines.append("• nessuno")
            await query.message.reply_text("\n".join(lines), reply_markup=user_admin_keyboard())
            return
        managed_ids = store.telegram_users()
        if not managed_ids:
            await query.message.reply_text("Non ci sono utenti aggiunti da rimuovere.", reply_markup=user_admin_keyboard())
            return
        keyboard = [[InlineKeyboardButton(str(user_id), callback_data=f"users:remove:{user_id}")] for user_id in managed_ids]
        keyboard.append([InlineKeyboardButton("⬅️ Gestione utenti", callback_data="users:list")])
        await query.message.reply_text("Scegli l'utente da rimuovere:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    if query.data == "admin:recap":
        if not is_admin(update, settings):
            await query.answer("Accesso non autorizzato.", show_alert=True)
            return
        await query.message.reply_text(notification_settings_text(settings), reply_markup=notification_settings_keyboard())
        return
    if query.data == "admin:recap:quiet-off":
        await recap_settings_quiet_off(update, context)
        return
    if query.data and query.data.startswith("users:remove:"):
        if not is_admin(update, settings):
            await query.answer("Accesso non autorizzato.", show_alert=True)
            return
        user_id = int(query.data.rsplit(":", 1)[-1])
        message = f"Utente {user_id} rimosso." if store.remove_telegram_user(user_id) else "Utente già rimosso."
        await query.message.reply_text(message, reply_markup=user_admin_keyboard())
        return
    if query.data == "menu:plants":
        configured_plants = store.plants()
        if not configured_plants:
            await query.message.reply_text("Non hai ancora configurato nessuna pianta.")
            return
        keyboard = [
            [InlineKeyboardButton(name, callback_data=f"plant:{wizard_token(context, f'{node}:{channel}')}")]
            for node, channel, name, *_ in configured_plants
        ]
        keyboard.append([InlineKeyboardButton("⬅️ Menu", callback_data="menu:home")])
        await query.message.reply_text("Scegli una pianta:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    if query.data == "menu:status":
        nodes = store.known_nodes()
        keyboard = [
            [InlineKeyboardButton(
                f"{node} · {name or 'senza nome'} · {store.node_status(node)}",
                callback_data=f"node:{wizard_token(context, node)}",
            )]
            for node, name, _ in nodes
        ]
        keyboard.append([InlineKeyboardButton("⬅️ Menu", callback_data="menu:home")])
        await query.message.reply_text(
            node_status_text(store), reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    if query.data == "menu:alerts":
        await query.message.reply_text(
            plant_alerts_text(store), reply_markup=main_keyboard(is_admin(update, settings))
        )
        return
    if query.data == "menu:help":
        await query.message.reply_text(HELP_TEXT, reply_markup=main_keyboard())
        return
    if query.data == "menu:home":
        await query.message.reply_text("Menu principale", reply_markup=main_keyboard(is_admin(update, settings)))
        return
    if query.data and query.data.startswith("node:"):
        node = wizard_value(context, query.data.rsplit(":", 1)[-1])
        if not node or not any(item[0] == node for item in store.known_nodes()):
            await query.message.reply_text("Questo nodo non è più disponibile.", reply_markup=main_keyboard())
            return
        await query.message.reply_text(
            node_status_text(store, node),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Temperatura", callback_data=f"node-metric:{wizard_token(context, f'{node}:temperature') }"),
                    InlineKeyboardButton("Umidità", callback_data=f"node-metric:{wizard_token(context, f'{node}:humidity') }"),
                ],
                [
                    InlineKeyboardButton("Luce", callback_data=f"node-metric:{wizard_token(context, f'{node}:light') }"),
                    InlineKeyboardButton("Storico 24h", callback_data=f"node-history:{wizard_token(context, node)}:24h"),
                    InlineKeyboardButton("Storico 7g", callback_data=f"node-history:{wizard_token(context, node)}:7g"),
                ],
                [InlineKeyboardButton("🗑️ Elimina nodo", callback_data=f"node-action:delete:{wizard_token(context, node)}")],
                [InlineKeyboardButton("⬅️ Stato nodi", callback_data="menu:status")],
                [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
            ]),
        )
        return
    if query.data and query.data.startswith("node-metric:"):
        target = wizard_value(context, query.data.split(":", 1)[1])
        if not target or ":" not in target:
            await query.message.reply_text("Questo dato non è più disponibile.", reply_markup=main_keyboard())
            return
        node, metric = target.rsplit(":", 1)
        if metric not in {"temperature", "humidity", "light"} or not any(item[0] == node for item in store.known_nodes()):
            await query.message.reply_text("Questo nodo non è più disponibile.", reply_markup=main_keyboard())
            return
        await query.message.reply_text(
            node_metric_text(store, node, metric),
            reply_markup=navigation_keyboard(f"node:{wizard_token(context, node)}", "Dettaglio nodo"),
        )
        return
    if query.data and query.data.startswith("node-history:"):
        _, token, period = query.data.split(":", 2)
        node = wizard_value(context, token)
        if period not in {"24h", "7g"} or not node or not any(item[0] == node for item in store.known_nodes()):
            await query.message.reply_text("Questo storico non è più disponibile.", reply_markup=main_keyboard())
            return
        await query.message.reply_text(
            history_text(store, node, period) or f"Nessun dato valido per {store.node_name(node)} nel periodo {period}.",
            reply_markup=navigation_keyboard(f"node:{wizard_token(context, node)}", "Dettaglio nodo"),
        )
        return
    if query.data and query.data.startswith("history:"):
        _, token, period = query.data.split(":", 2)
        target = wizard_value(context, token)
        if period not in {"24h", "7g"} or not target or ":" not in target:
            await query.message.reply_text("Questo storico non è più disponibile.", reply_markup=main_keyboard())
            return
        node, channel_text = target.rsplit(":", 1)
        if not any(item[0] == node and str(item[1]) == channel_text for item in store.plants()):
            await query.message.reply_text("Questa pianta non è più disponibile.", reply_markup=main_keyboard())
            return
        text = history_text(store, node, period)
        await query.message.reply_text(
            text or f"Nessun dato valido per {store.node_name(node)} nel periodo {period}.",
            reply_markup=navigation_keyboard(
                f"plant:{wizard_token(context, f'{node}:{channel_text}')}", "Dettaglio pianta"
            ),
        )
        return
    if query.data and query.data.startswith("plant:"):
        target = wizard_value(context, query.data.split(":", 1)[1])
        if not target or ":" not in target:
            await query.message.reply_text("Questa pianta non è più disponibile.", reply_markup=main_keyboard())
            return
        node, channel_text = target.rsplit(":", 1)
        matches = [plant for plant in store.plants() if plant[0] == node and str(plant[1]) == channel_text]
        if not matches:
            await query.message.reply_text("Questa pianta non è più disponibile.", reply_markup=main_keyboard())
            return
        _, channel, name, species, position, notes, threshold = matches[0]
        payload = store.latest_measurements(node) or {}
        moisture = next(
            (item.get("moisture_percent") for item in payload.get("soil", [])
             if isinstance(item, dict) and item.get("channel") == channel),
            None,
        )
        text = f"🌿 {name}\nNodo: {store.node_name(node)} [{node}]\nCanale: A{channel}\n"
        if species:
            text += f"Specie: {species}\n"
        if position:
            text += f"Posizione: {position}\n"
        if notes:
            text += f"Note: {notes}\n"
        text += f"Umidità terreno: {moisture:.1f}%" if isinstance(moisture, (int, float)) else "Umidità terreno: dato non disponibile"
        air = payload.get("air", {})
        if isinstance(air, dict) and air.get("valid"):
            text += f"\nAria: {air.get('temperature_c', '?')} °C · {air.get('humidity_percent', '?')}% umidità"
        light = payload.get("light", {})
        if isinstance(light, dict) and light.get("valid") and isinstance(light.get("lux"), (int, float)):
            text += f"\nLuce: {light['lux']:.1f} lux"
        if threshold is not None:
            text += f"\nSoglia: {threshold:.0f}%"
        last_watering = store.last_watering(node, channel)
        if last_watering:
            text += f"\nUltima annaffiatura registrata: {format_local_time(last_watering)}"
        history_token = wizard_token(context, f"{node}:{channel}")
        plant_token = wizard_token(context, f"{node}:{channel}")
        keyboard = [
            [
                InlineKeyboardButton("Storico 24h", callback_data=f"history:{history_token}:24h"),
                InlineKeyboardButton("Storico 7g", callback_data=f"history:{history_token}:7g"),
            ],
            [
                InlineKeyboardButton("Rinomina", callback_data=f"plant-action:rename:{plant_token}"),
                InlineKeyboardButton("Modifica", callback_data=f"plant-action:edit:{plant_token}"),
            ],
            [InlineKeyboardButton("💧 Segna come annaffiata", callback_data=f"plant-action:water:{plant_token}")],
            [
                InlineKeyboardButton("Sposta canale", callback_data=f"plant-action:move:{plant_token}"),
                InlineKeyboardButton("Elimina pianta", callback_data=f"plant-action:delete:{plant_token}"),
            ],
            [InlineKeyboardButton("⬅️ Le mie piante", callback_data="menu:plants")],
            [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
        ]
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    if query.data and query.data.startswith("plant-action:delete:"):
        await plant_delete_start(update, context)
        return
    if query.data == "plant-action:delete-confirm":
        await plant_delete_confirm(update, context)
        return
    if query.data and query.data.startswith("node-action:delete:"):
        node = wizard_value(context, query.data.rsplit(":", 1)[-1])
        if not node or not any(item[0] == node for item in store.known_nodes()):
            await query.message.reply_text("Questo nodo non è più disponibile.", reply_markup=main_keyboard())
            return
        context.user_data["node_action"] = {
            "action": "delete",
            "node": node,
            "clear_configuration": True,
            "clear_last_state": True,
            "clear_history": True,
        }
        await query.answer()
        await query.message.reply_text(
            f"Confermi l'eliminazione del nodo tecnico {node}?\nVerranno rimossi: configurazione, ultimo stato e storico.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑️ Conferma eliminazione", callback_data=f"node-action:delete-confirm:{wizard_token(context, node)}")],
                [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")],
                [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
            ]),
        )
        return
    if query.data and query.data.startswith("node-action:delete-confirm:"):
        node = wizard_value(context, query.data.rsplit(":", 1)[-1])
        action = context.user_data.get("node_action", {})
        if not node or action.get("node") != node:
            await query.answer("Nessun nodo da eliminare.", show_alert=True)
            return
        deleted = store.delete_node(
            node,
            clear_configuration=action.get("clear_configuration", True),
            clear_last_state=action.get("clear_last_state", True),
            clear_history=action.get("clear_history", True),
        )
        user_id = update.effective_user.id if update.effective_user else "unknown"
        LOGGER.warning(
            "Operazione distruttiva da user_id=%s: elimina nodo %s [config=%s stato=%s storico=%s]",
            user_id,
            node,
            deleted["configuration"],
            deleted["state"],
            deleted["history"],
        )
        await query.answer()
        await query.message.reply_text(
            f"✅ Nodo eliminato: {node}\nConfigurazione: {'sì' if deleted['configuration'] else 'no'}\nUltimo stato: {'sì' if deleted['state'] else 'no'}\nStorico: {'sì' if deleted['history'] else 'no'}",
            reply_markup=main_keyboard(),
        )
        context.user_data.pop("node_action", None)
        return
    await query.message.reply_text("Questa azione non è più disponibile.", reply_markup=main_keyboard())


def history_text(store: Store, node: str, period: str) -> str | None:
    since = datetime.now(timezone.utc) - timedelta(hours=24 if period == "24h" else 24 * 7)
    since_text = since.isoformat(timespec="seconds")
    history = store.history(node, since_text)
    summary = store.air_summary(node, since_text)
    light = store.light_summary(node, since_text)
    if not summary["count"] and not light["count"]:
        return None
    temperature_values = []
    humidity_values = []
    light_values = []
    for _, payload, _ in history:
        air = payload.get("air", {})
        if isinstance(air, dict) and air.get("valid"):
            if isinstance(air.get("temperature_c"), (int, float)):
                temperature_values.append(float(air["temperature_c"]))
            if isinstance(air.get("humidity_percent"), (int, float)):
                humidity_values.append(float(air["humidity_percent"]))
        current_light = payload.get("light", {})
        if (
            isinstance(current_light, dict)
            and current_light.get("valid")
            and isinstance(current_light.get("lux"), (int, float))
            and current_light["lux"] >= 0
        ):
            light_values.append(float(current_light["lux"]))

    lines = [f"📈 STORICO {period} · {store.node_name(node)}", f"Nodo tecnico: {node}", ""]
    if summary["count"]:
        lines.extend(["🌡️ ARIA", f"Temperatura: {summary['minimum']:.1f} / {summary['average']:.1f} / {summary['maximum']:.1f} °C", f"Andamento: {sparkline(temperature_values)}"])
        if summary["humidity_average"] is not None:
            lines.extend([f"Umidità media: {summary['humidity_average']:.1f}%", f"Andamento: {sparkline(humidity_values)}"])
        lines.append(f"Letture valide: {summary['count']}")
    else:
        lines.extend(["🌡️ ARIA", "Nessun dato valido"])
    if light["count"]:
        lines.extend(["", "💡 LUCE", f"Minima / media / massima: {light['minimum']:.1f} / {light['average']:.1f} / {light['maximum']:.1f} lux", f"Andamento: {sparkline(light_values)}", f"Letture valide: {light['count']}"])
    else:
        lines.extend(["", "💡 LUCE", "Nessun dato valido"])
    soil_lines = []
    for plant_node, channel, name, *_ in store.plants():
        if plant_node != node:
            continue
        soil = store.soil_summary(node, channel, since_text)
        if soil["count"]:
            soil_values = [
                float(item["moisture_percent"])
                for _, payload, _ in history
                for item in payload.get("soil", [])
                if isinstance(payload.get("soil", []), list)
                and isinstance(item, dict) and item.get("channel") == channel
                and isinstance(item.get("moisture_percent"), (int, float))
                and 0 <= item["moisture_percent"] <= 100
            ]
            soil_lines.append(
                f"🌱 {name} (A{channel})\n"
                f"   Minima / media / massima: {soil['minimum']:.1f} / {soil['average']:.1f} / {soil['maximum']:.1f}%\n"
                f"   Andamento: {sparkline(soil_values)}"
            )
    if soil_lines:
        lines.extend(["", "🌱 UMIDITÀ TERRENO", *soil_lines])
    return "\n".join(lines)


def sparkline(values: list[float], width: int = 18) -> str:
    """Render a compact, dependency-free trend line for Telegram messages."""
    if not values:
        return "n/d"
    if len(values) > width:
        step = (len(values) - 1) / (width - 1)
        values = [values[round(index * step)] for index in range(width)]
    minimum, maximum = min(values), max(values)
    levels = "▁▂▃▄▅▆▇█"
    if maximum == minimum:
        return levels[3] * len(values)
    return "".join(levels[round((value - minimum) / (maximum - minimum) * (len(levels) - 1))] for value in values)


def node_metric_text(store: Store, node: str, metric: str) -> str:
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    if metric == "temperature":
        summary = store.air_summary(node, since)
        if not summary["count"]:
            return f"{store.node_name(node)} [{node}]\nTemperatura: nessun dato valido nelle ultime 24h."
        return f"{store.node_name(node)} [{node}]\nTemperatura ultime 24h: media {summary['average']:.1f} °C, min {summary['minimum']:.1f} °C, max {summary['maximum']:.1f} °C"
    if metric == "humidity":
        summary = store.air_summary(node, since)
        if summary["humidity_average"] is None:
            return f"{store.node_name(node)} [{node}]\nUmidità aria: nessun dato valido nelle ultime 24h."
        return f"{store.node_name(node)} [{node}]\nUmidità aria ultime 24h: media {summary['humidity_average']:.1f}%"
    summary = store.light_summary(node, since)
    if not summary["count"]:
        return f"{store.node_name(node)} [{node}]\nLuce: nessun dato valido nelle ultime 24h."
    return f"{store.node_name(node)} [{node}]\nLuce ultime 24h: media {summary['average']:.1f} lux, min {summary['minimum']:.1f} lux, max {summary['maximum']:.1f} lux"


def node_status_text(store: Store, selected_node: str | None = None) -> str:
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    plants = store.plants()
    nodes = [item for item in store.known_nodes() if selected_node is None or item[0] == selected_node]
    if not nodes:
        return "Nodo non disponibile." if selected_node else "Nessun nodo conosciuto."

    lines = ["📊 STATO NODO · ultime 24h" if selected_node else "📊 STATO NODI · ultime 24h", ""]
    for node, name, _ in nodes:
        state = next(
            (payload.get("state") for current_node, kind, payload, _ in store.latest(node)
             if current_node == node and kind == "state"),
            "n/d",
        )
        lines.extend([
            f"🛰️ {name or node}",
            f"ID tecnico: {node}",
            f"Stato: {'🟢 online' if state == 'online' else '🔴 ' + str(state)}",
            "",
            "🌿 PIANTE",
        ])
        node_plants = [plant for plant in plants if plant[0] == node]
        if node_plants:
            for index, (_, channel, plant_name, *_rest) in enumerate(node_plants):
                soil = store.soil_summary(node, channel, since)
                branch = "└─" if index == len(node_plants) - 1 else "├─"
                moisture = f"{soil['average']:.1f}%" if soil["count"] else "n/d"
                lines.append(f"{branch} {plant_name} · A{channel} · 💧 {moisture} media")
        else:
            lines.append("└─ Nessuna pianta configurata")

        air = store.air_summary(node, since)
        light = store.light_summary(node, since)
        lines.extend(["", "🌡️ ARIA"])
        if air["count"]:
            lines.append(
                f"Temperatura: media {air['average']:.1f} °C, "
                f"min {air['minimum']:.1f} °C, max {air['maximum']:.1f} °C"
            )
            lines.append(
                f"Umidità aria: media {air['humidity_average']:.1f}%"
                if air["humidity_average"] is not None
                else "Umidità aria: n/d"
            )
        else:
            lines.append("Temperatura: n/d")
            lines.append("Umidità aria: n/d")
        lines.append("\n💡 LUCE")
        if light["count"]:
            lines.append(
                f"Luminosità: media {light['average']:.1f} lux, "
                f"min {light['minimum']:.1f} lux, max {light['maximum']:.1f} lux"
            )
        else:
            lines.append("Luminosità: n/d")
        lines.append("")
    return "\n".join(lines)


def plant_alerts_text(store: Store) -> str:
    alerts = store.plant_alerts()
    if not alerts:
        return "✅ Nessun avviso. Le letture disponibili sono sopra le soglie configurate."
    lines = ["⚠️ Avvisi piante"]
    for kind, name, node, channel, message in alerts:
        marker = "🔴" if kind == "alert" else "ℹ️"
        lines.append(f"{marker} {name} · {message} (A{channel}, {store.node_name(node)})")
    return "\n".join(lines)


def format_local_time(value: str) -> str:
    """Render a stored UTC timestamp in the hub's local timezone."""
    return datetime.fromisoformat(value).astimezone().strftime("%d/%m/%Y %H:%M")


def is_quiet_hours(settings: Settings, now: datetime | None = None) -> bool:
    """Return whether local time is inside the configured, possibly overnight, quiet window."""
    if settings.quiet_hours_start is None or settings.quiet_hours_end is None:
        return False
    current = (now or datetime.now(ZoneInfo(settings.timezone_name))).astimezone(ZoneInfo(settings.timezone_name)).time()
    start, end = settings.quiet_hours_start, settings.quiet_hours_end
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def daily_recap_text(store: Store, settings: Settings) -> str:
    now = datetime.now(ZoneInfo(settings.timezone_name))
    lines = [f"☀️ Recap giornaliero · {now.strftime('%d/%m/%Y')}", ""]
    alerts = plant_alerts_text(store)
    lines.extend([alerts, "", node_status_text(store)])
    text = "\n".join(lines)
    return text if len(text) <= 4096 else text[:4080] + "\n\n… recap abbreviato. Apri Stato nodi per i dettagli."


def daily_recap_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💧 Ho annaffiato tutte le piante", callback_data="recap:water-all")],
        [InlineKeyboardButton("🌱 Le mie piante", callback_data="menu:plants")],
    ])


async def daily_recap_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if is_quiet_hours(settings):
        LOGGER.info("Recap giornaliero non inviato: fascia silenziosa attiva")
        return
    store: Store = context.application.bot_data["store"]
    text = daily_recap_text(store, settings)
    recipients = set(settings.allowed_user_ids) | set(store.telegram_users())
    for user_id in recipients:
        try:
            await context.application.bot.send_message(chat_id=user_id, text=text, reply_markup=daily_recap_keyboard())
        except Exception:
            LOGGER.exception("Invio recap Telegram fallito per user_id=%s", user_id)


async def alert_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    current: dict[str, str] = {}
    expected: set[str] = set()
    messages: dict[str, str] = {}

    for node, channel, name, _, _, _, threshold in store.plants():
        missing_key = f"soil-missing:{node}:{channel}"
        expected.add(missing_key)
        messages[missing_key] = f"ℹ️ {name}: umidità del terreno non disponibile (A{channel}, {store.node_name(node)})."
        if threshold is not None:
            payload = store.latest_measurements(node) or {}
            soil = payload.get("soil", [])
            reading = next((item for item in soil if isinstance(item, dict) and item.get("channel") == channel), None) if isinstance(soil, list) else None
            moisture = reading.get("moisture_percent") if reading else None
            if isinstance(moisture, (int, float)) and 0 <= moisture <= 100 and moisture < threshold:
                if store.open_plant_warning(node, channel):
                    key = f"soil-low:{node}:{channel}"
                    expected.add(key)
                    messages[key] = (
                        f"🔴 {name}: umidità del terreno sotto soglia (A{channel}, {store.node_name(node)}). "
                        f"Lettura: {moisture:.1f}% (soglia {threshold:.0f}%). "
                        "L'avviso resterà aperto finché non registri l'annaffiatura."
                    )
                    current[key] = messages[key]

    for kind, name, node, channel, detail in store.plant_alerts():
        key = f"soil-missing:{node}:{channel}"
        if kind == "alert":
            continue
        current[key] = f"{messages[key]} Lettura: {detail}."

    for node, name, reason in store.offline_node_alerts(settings.node_offline_after_seconds):
        key = f"node-offline:{node}"
        expected.add(key)
        messages[key] = f"🔴 Nodo {name} offline: {reason}."
        current[key] = messages[key]

    recipients = set(settings.allowed_user_ids) | set(store.telegram_users())
    for key in expected:
        # Gli avvisi informativi vengono consegnati al termine della fascia silenziosa;
        # gli alert critici restano immediati.
        if key.startswith("soil-missing:") and is_quiet_hours(settings):
            continue
        changed, previous = store.update_alert_state(key, key in current)
        if not changed or (key not in current and previous is not True):
            continue
        if key in current:
            text = current[key]
        else:
            text = f"✅ Rientrato: {messages.get(key, 'la condizione di allarme non è più presente')}"
        for user_id in recipients:
            try:
                await context.application.bot.send_message(chat_id=user_id, text=text)
            except Exception:
                LOGGER.exception("Invio alert Telegram fallito per user_id=%s", user_id)


async def configure_command_menu(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("start", "apri il menu del bot"),
            BotCommand("help", "mostra cosa posso fare"),
            BotCommand("rinomina", "cambia nome a una pianta"),
            BotCommand("storico", "mostra l'andamento recente"),
            BotCommand("avvisi", "mostra alert e dati non disponibili"),
            BotCommand("annaffia", "registra un'annaffiatura"),
            BotCommand("recap", "mostra il recap giornaliero"),
            BotCommand("calibra", "imposta una calibrazione"),
            BotCommand("cal", "imposta una calibrazione"),
            BotCommand("node", "imposta il nome di un nodo"),
            BotCommand("plant", "configura una pianta"),
            BotCommand("whoami", "mostra il tuo ID Telegram"),
        ]
    )


async def rename_plant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await deny_unless_allowed(update, context):
        return
    message_text = update.effective_message.text if update.effective_message else ""
    command_body = message_text.split(" ", 1)[1] if " " in message_text else ""
    if "|" not in command_body:
        await update.effective_message.reply_text(
            "Per rinominare una pianta scrivi:\n"
            "/rinomina Nome attuale | Nome nuovo"
        )
        return
    current_name, new_name = (part.strip() for part in command_body.split("|", 1))
    if not current_name or not new_name:
        await update.effective_message.reply_text(
            "Servono sia il nome attuale sia quello nuovo. Esempio:\n"
            "/rinomina Basilico | Basilico cucina"
        )
        return
    store: Store = context.application.bot_data["store"]
    matches = store.find_plants(current_name)
    if not matches:
        await update.effective_message.reply_text(
            f"Non trovo la pianta {current_name}. Apri “Le mie piante” dal menu per vedere l'elenco completo."
        )
        return
    if len(matches) > 1:
        await update.effective_message.reply_text(
            "Ci sono più piante con questo nome. Prima assegna loro nomi diversi."
        )
        return
    if store.find_plants(new_name):
        await update.effective_message.reply_text(
            f"Esiste già una pianta chiamata {new_name}. Scegli un nome diverso."
        )
        return
    store.rename_plant(current_name, new_name)
    await update.effective_message.reply_text(f"✅ Pianta rinominata: {current_name} → {new_name}")


async def alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await deny_unless_allowed(update, context):
        return
    store: Store = context.application.bot_data["store"]
    await update.effective_message.reply_text(plant_alerts_text(store), reply_markup=main_keyboard())


async def recap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await deny_unless_allowed(update, context):
        return
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    await update.effective_message.reply_text(daily_recap_text(store, settings), reply_markup=daily_recap_keyboard())


async def water_plant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await deny_unless_allowed(update, context):
        return
    name = " ".join(context.args).strip()
    if not name:
        await update.effective_message.reply_text("Uso: /annaffia NOME_PIANTA")
        return
    store: Store = context.application.bot_data["store"]
    matches = store.find_plants(name)
    if len(matches) != 1:
        await update.effective_message.reply_text("Non trovo una sola pianta con questo nome. Apri “Le mie piante” per selezionarla.")
        return
    node, channel, plant_name, *_ = matches[0]
    user_id = update.effective_user.id if update.effective_user else None
    watered_at = store.record_watering(node, channel, user_id)
    await update.effective_message.reply_text(
        f"💧 Annaffiatura registrata per {plant_name} alle {format_local_time(watered_at)}. L'eventuale avviso è stato chiuso."
    )


async def bulk_watering_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    if not user_allowed(update, settings, store):
        await query.answer("Accesso non autorizzato.", show_alert=True)
        return ConversationHandler.END
    count = len(store.plants())
    if not count:
        await query.answer("Non ci sono piante configurate.", show_alert=True)
        return ConversationHandler.END
    context.user_data["bulk_watering"] = True
    await query.answer()
    await query.message.reply_text(
        f"Confermi di aver annaffiato tutte le {count} piante configurate?\n"
        "Saranno registrate tutte e verranno chiusi gli eventuali avvisi aperti.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💧 Conferma annaffiatura completa", callback_data="recap:water-all-confirm")],
            [InlineKeyboardButton("✖️ Annulla", callback_data="wizard:cancel")],
        ]),
    )
    return BULK_WATER_CONFIRM


async def bulk_watering_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    if not context.user_data.get("bulk_watering") or not user_allowed(update, settings, store):
        await query.answer("Questa conferma non è più disponibile.", show_alert=True)
        return ConversationHandler.END
    user_id = update.effective_user.id if update.effective_user else None
    count, watered_at = store.record_watering_for_all_plants(user_id)
    context.user_data.pop("bulk_watering", None)
    await query.answer()
    await query.message.reply_text(
        f"💧 Annaffiatura registrata per tutte le {count} piante alle {format_local_time(watered_at)}. "
        "Gli avvisi di umidità aperti sono stati chiusi.",
        reply_markup=main_keyboard(is_admin(update, settings)),
    )
    return ConversationHandler.END


def build_bulk_watering_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(bulk_watering_start, pattern=r"^recap:water-all$")],
        states={BULK_WATER_CONFIRM: [CallbackQueryHandler(bulk_watering_confirm, pattern=r"^recap:water-all-confirm$")]},
        fallbacks=[CallbackQueryHandler(cancel_wizard, pattern=r"^wizard:cancel$"), CommandHandler(["annulla", "cancel"], cancel_wizard)],
        conversation_timeout=900, per_user=True, per_chat=True,
    )


async def set_node_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await deny_unless_allowed(update, context):
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text("Uso: /node NODE NOME")
        return
    node = context.args[0]
    name = " ".join(context.args[1:]).strip()
    store: Store = context.application.bot_data["store"]
    try:
        store.set_node(node, name)
    except ValueError as error:
        await update.effective_message.reply_text(str(error))
        return
    await update.effective_message.reply_text(f"Nome salvato: {node} = {name}")


async def set_plant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await deny_unless_allowed(update, context):
        return
    if len(context.args) < 3 or context.args[1] not in {"0", "1", "2", "3"}:
        await update.effective_message.reply_text(
            "Uso: /plant NODE CANALE NOME [SPECIE] [POSIZIONE] [NOTE]"
        )
        return
    node, channel = context.args[:2]
    values = context.args[2:]
    name = values[0]
    species = values[1] if len(values) > 1 else ""
    position = values[2] if len(values) > 2 else ""
    notes = " ".join(values[3:])
    store: Store = context.application.bot_data["store"]
    try:
        store.set_plant(node, int(channel), name, species, position, notes)
    except ValueError as error:
        await update.effective_message.reply_text(str(error))
        return
    await update.effective_message.reply_text(f"Vaso salvato: {name} ({node}, A{channel})")


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await deny_unless_allowed(update, context):
        return
    if not context.args or (len(context.args) > 1 and context.args[1] not in {"24h", "7g"}):
        await update.effective_message.reply_text("Uso: /storico NOME_PIANTA [24h|7g]")
        return
    store: Store = context.application.bot_data["store"]
    target = " ".join(context.args[:1])
    matches = store.find_plants(target)
    node = matches[0][0] if len(matches) == 1 else context.args[0]
    period = context.args[1] if len(context.args) > 1 else "24h"
    text = history_text(store, node, period)
    if text is None:
        await update.effective_message.reply_text(f"Nessun dato valido per {store.node_name(node)} nel periodo {period}.")
        return
    await update.effective_message.reply_text(text)


async def set_calibration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await deny_unless_allowed(update, context):
        return
    if len(context.args) < 4:
        await update.effective_message.reply_text("Uso: /calibra NODE CANALE dry|wet|soglia VALORE")
        return
    node, channel, field, value = context.args[:4]
    field = {"soglia": "threshold"}.get(field, field)
    if channel not in {"0", "1", "2", "3"} or field not in {"dry", "wet", "threshold"}:
        await update.effective_message.reply_text("Canale 0..3 e campo dry, wet oppure threshold.")
        return
    try:
        numeric_value = float(value)
    except ValueError:
        await update.effective_message.reply_text("Non capisco il valore. Scrivi un numero, per esempio 35.")
        return
    if field == "threshold" and not 0 <= numeric_value <= 100:
        await update.effective_message.reply_text("La soglia deve essere compresa tra 0 e 100%.")
        return
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    if field == "threshold":
        try:
            store.set_plant_threshold(node, int(channel), numeric_value)
        except ValueError as error:
            await update.effective_message.reply_text(str(error))
            return
    client: mqtt.Client = context.application.bot_data["mqtt"]
    topic = f"{settings.topic_prefix}/{node}/config"
    client.publish(topic, json.dumps({"channel": int(channel), field: numeric_value}), qos=1)
    await update.effective_message.reply_text(
        f"✅ Configurazione inviata. Canale A{channel} del nodo {node}: {field} = {numeric_value:g}."
    )


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
    settings = store.notification_settings(settings)
    mqtt_client = build_mqtt_client(settings, store)
    mqtt_client.loop_start()
    application = Application.builder().token(settings.telegram_token).post_init(configure_command_menu).build()
    application.bot_data.update(settings=settings, store=store, mqtt=mqtt_client)
    if application.job_queue is None:
        raise RuntimeError("Installa python-telegram-bot con l'extra job-queue per gli alert automatici")
    application.job_queue.run_repeating(
        alert_job,
        interval=settings.alert_check_interval_seconds,
        first=5,
        name="plant-alerts",
    )
    application.add_handler(build_user_management_handler())
    application.add_handler(build_recap_settings_handler())
    application.add_handler(build_wizard_handler())
    application.add_handler(build_plant_action_handler())
    application.add_handler(build_bulk_watering_handler())
    application.add_handler(CommandHandler("whoami", whoami))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("avvisi", alerts))
    application.add_handler(CommandHandler("recap", recap))
    application.add_handler(CommandHandler("annaffia", water_plant))
    application.add_handler(CommandHandler("rinomina", rename_plant))
    application.add_handler(CommandHandler(["cal", "calibra"], set_calibration))
    application.add_handler(CommandHandler("node", set_node_name))
    application.add_handler(CommandHandler("plant", set_plant))
    application.add_handler(CommandHandler("storico", history))
    application.add_handler(CallbackQueryHandler(button_click))
    reschedule_daily_recap(application, settings)
    LOGGER.info("Hub avviato; utenti autorizzati: %d", len(settings.allowed_user_ids))
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
