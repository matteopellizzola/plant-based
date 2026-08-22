"""Raspberry Pi hub for plant nodes and Telegram commands."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)

from core import Settings, Store, topic_parts

LOGGER = logging.getLogger("plant_hub")

HELP_TEXT = """🌿 Comandi disponibili

📋 Consultazione
/piante - elenco delle piante configurate
/pianta NOME - dettaglio e ultima lettura
/rinomina VECCHIO | NUOVO - cambia nome a una pianta
/stato - stato dei nodi collegati
/storico NOME [24h|7g] - andamento recente

⚙️ Configurazione
/calibra NODE CANALE dry|wet|soglia VALORE
/node NODE NOME - nome leggibile del nodo
/plant NODE CANALE NOME [SPECIE] [POSIZIONE] [NOTE]

Esempio:
/pianta Basilico
/storico plant-node-01 24h

Per recuperare il tuo ID Telegram: /whoami"""


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Aggiungi pianta", callback_data="wizard:plant:start")],
            [InlineKeyboardButton("⚙️ Configura nodo", callback_data="wizard:node:start")],
            [InlineKeyboardButton("🌱 Le mie piante", callback_data="menu:plants")],
            [InlineKeyboardButton("📊 Stato nodi", callback_data="menu:status")],
            [InlineKeyboardButton("❓ Aiuto", callback_data="menu:help")],
        ]
    )


NODE_NAME, PLANT_NODE, PLANT_CHANNEL, PLANT_NAME, PLANT_SPECIES, PLANT_POSITION, PLANT_NOTES, PLANT_CONFIRM = range(8)


def wizard_token(context: ContextTypes.DEFAULT_TYPE, value: str) -> str:
    token = uuid.uuid4().hex[:12]
    context.user_data.setdefault("wizard_callbacks", {})[token] = value
    return token


def wizard_value(context: ContextTypes.DEFAULT_TYPE, token: str) -> str | None:
    return context.user_data.get("wizard_callbacks", {}).get(token)


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Annulla", callback_data="wizard:cancel")]])


async def cancel_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("wizard", None)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Operazione annullata.", reply_markup=main_keyboard())
    elif update.effective_message:
        await update.effective_message.reply_text("Operazione annullata.", reply_markup=main_keyboard())
    return ConversationHandler.END


async def node_wizard_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not user_allowed(update, context.application.bot_data["settings"]):
        await query.answer("Accesso non autorizzato.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    store: Store = context.application.bot_data["store"]
    nodes = store.known_nodes()
    if not nodes:
        await query.edit_message_text("Nessun nodo conosciuto. Accendi un nodo e attendi il primo messaggio MQTT.")
        return ConversationHandler.END
    buttons = []
    for node, name, _ in nodes:
        label = f"{node} · {name or 'senza nome'} · {store.node_status(node)}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"wizard:node:select:{wizard_token(context, node)}")])
    buttons.append([InlineKeyboardButton("Annulla", callback_data="wizard:cancel")])
    await query.edit_message_text("Scegli il nodo da configurare:", reply_markup=InlineKeyboardMarkup(buttons))
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
    await query.edit_message_text(f"Nuovo nome per {node}:\nInvia il nome oppure /annulla.", reply_markup=cancel_keyboard())
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
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Conferma", callback_data="wizard:node:confirm"), InlineKeyboardButton("Annulla", callback_data="wizard:cancel")]]),
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
    await query.edit_message_text(f"Nome salvato: {wizard['node']} = {wizard['name']}", reply_markup=main_keyboard())
    context.user_data.pop("wizard", None)
    return ConversationHandler.END


async def plant_wizard_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not user_allowed(update, context.application.bot_data["settings"]):
        await query.answer("Accesso non autorizzato.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    store: Store = context.application.bot_data["store"]
    nodes = store.known_nodes()
    if not nodes:
        await query.edit_message_text("Nessun nodo conosciuto. Accendi un nodo e attendi il primo messaggio MQTT.")
        return ConversationHandler.END
    buttons = [[InlineKeyboardButton(f"{node} · {name or 'senza nome'} · {store.node_status(node)}", callback_data=f"wizard:plant:node:{wizard_token(context, node)}")] for node, name, _ in nodes]
    buttons.append([InlineKeyboardButton("Annulla", callback_data="wizard:cancel")])
    await query.edit_message_text("Scegli il nodo della pianta:", reply_markup=InlineKeyboardMarkup(buttons))
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
    buttons.append([InlineKeyboardButton("Annulla", callback_data="wizard:cancel")])
    await query.answer()
    await query.edit_message_text("Scegli un canale libero:", reply_markup=InlineKeyboardMarkup(buttons))
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
    await query.edit_message_text("Nome della pianta (oppure /annulla):", reply_markup=cancel_keyboard())
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
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Conferma", callback_data="wizard:plant:confirm"), InlineKeyboardButton("Correggi note", callback_data="wizard:plant:edit-notes")], [InlineKeyboardButton("Annulla", callback_data="wizard:cancel")]]),
        )
        return PLANT_CONFIRM
    context.user_data["wizard_state"] = state + 1
    keyboard = [[InlineKeyboardButton("Salta", callback_data="wizard:skip")], [InlineKeyboardButton("Annulla", callback_data="wizard:cancel")]] if state in {PLANT_SPECIES, PLANT_POSITION, PLANT_NOTES} else cancel_keyboard().inline_keyboard
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
        await query.edit_message_text("Inserisci le note opzionali oppure conferma direttamente.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Conferma", callback_data="wizard:plant:confirm")], [InlineKeyboardButton("Annulla", callback_data="wizard:cancel")]]))
        return PLANT_CONFIRM
    context.user_data["wizard_state"] = state + 1
    await query.edit_message_text(next_prompt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Salta", callback_data="wizard:skip")], [InlineKeyboardButton("Annulla", callback_data="wizard:cancel")]]))
    return state + 1


async def plant_wizard_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    wizard = context.user_data.get("wizard", {})
    store: Store = context.application.bot_data["store"]
    try:
        store.set_plant(wizard["node"], wizard["channel"], wizard["name"], wizard.get("species", ""), wizard.get("position", ""), wizard.get("notes", ""))
    except (KeyError, ValueError) as error:
        await query.answer(str(error), show_alert=True)
        return ConversationHandler.END
    await query.answer()
    await query.edit_message_text(f"Vaso salvato: {wizard['name']} ({wizard['node']}, A{wizard['channel']})", reply_markup=main_keyboard())
    context.user_data.pop("wizard", None)
    context.user_data.pop("wizard_state", None)
    return ConversationHandler.END


async def plant_wizard_edit_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    context.user_data["wizard_state"] = PLANT_NOTES
    await query.answer()
    await query.edit_message_text(
        "Nuove note (oppure premi Salta):",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Salta", callback_data="wizard:skip")],
            [InlineKeyboardButton("Annulla", callback_data="wizard:cancel")],
        ]),
    )
    return PLANT_NOTES


def build_wizard_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(node_wizard_start, pattern=r"^wizard:node:start$"),
            CallbackQueryHandler(plant_wizard_start, pattern=r"^wizard:plant:start$"),
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
        },
        fallbacks=[
            CallbackQueryHandler(cancel_wizard, pattern=r"^wizard:cancel$"),
            CommandHandler(["annulla", "cancel", "start", "help", "piante", "stato", "status", "storico", "cal", "calibra", "node", "plant"], cancel_wizard),
        ],
        conversation_timeout=900,
        per_user=True,
        per_chat=True,
    )


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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    settings: Settings = context.application.bot_data["settings"]
    if not user_allowed(update, settings):
        await update.effective_message.reply_text(
            "Ciao! Questo bot è protetto. Usa /whoami per conoscere il tuo ID Telegram "
            "e chiedi all'amministratore di autorizzarti."
        )
        return
    await update.effective_message.reply_text(
        "Ciao! Ti aiuto a controllare le tue piante.", reply_markup=main_keyboard()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await deny_unless_allowed(update, context):
        return
    await update.effective_message.reply_text(HELP_TEXT, reply_markup=main_keyboard())


async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    settings: Settings = context.application.bot_data["settings"]
    if query.from_user.id not in settings.allowed_user_ids:
        await query.answer("Accesso non autorizzato.", show_alert=True)
        return
    await query.answer()
    store: Store = context.application.bot_data["store"]
    if query.data == "menu:plants":
        configured_plants = store.plants()
        if not configured_plants:
            await query.edit_message_text("Non hai ancora configurato nessuna pianta.")
            return
        keyboard = [
            [InlineKeyboardButton(name, callback_data=f"plant:{node}:{channel}")]
            for node, channel, name, *_ in configured_plants
        ]
        keyboard.append([InlineKeyboardButton("⬅️ Menu", callback_data="menu:home")])
        await query.edit_message_text("Scegli una pianta:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    if query.data == "menu:status":
        rows = store.latest()
        text = "Nessuno stato ricevuto." if not rows else "\n".join(
            f"{node}: {payload.get('state', kind)} ({received_at})"
            for node, kind, payload, received_at in rows
            if kind == "state"
        )
        await query.edit_message_text(text or "Nessuno stato ricevuto.", reply_markup=main_keyboard())
        return
    if query.data == "menu:help":
        await query.edit_message_text(HELP_TEXT, reply_markup=main_keyboard())
        return
    if query.data == "menu:home":
        await query.edit_message_text("Menu principale", reply_markup=main_keyboard())
        return
    if query.data and query.data.startswith("plant:"):
        _, node, channel_text = query.data.split(":", 2)
        matches = [plant for plant in store.plants() if plant[0] == node and str(plant[1]) == channel_text]
        if not matches:
            await query.edit_message_text("Questa pianta non è più disponibile.", reply_markup=main_keyboard())
            return
        _, channel, name, species, position, notes, threshold = matches[0]
        payload = store.latest_measurements(node) or {}
        moisture = next(
            (item.get("moisture_percent") for item in payload.get("soil", [])
             if isinstance(item, dict) and item.get("channel") == channel),
            None,
        )
        text = f"🌿 {name}\nNodo: {store.node_name(node)}\nCanale: A{channel}\n"
        text += f"Umidità terreno: {moisture:.1f}%" if isinstance(moisture, (int, float)) else "Umidità terreno: dato non disponibile"
        keyboard = [[InlineKeyboardButton("⬅️ Le mie piante", callback_data="menu:plants")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def configure_command_menu(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("start", "apri il menu del bot"),
            BotCommand("help", "mostra cosa posso fare"),
            BotCommand("piante", "elenca le tue piante"),
            BotCommand("pianta", "mostra il dettaglio di una pianta"),
            BotCommand("rinomina", "cambia nome a una pianta"),
            BotCommand("stato", "controlla i nodi"),
            BotCommand("storico", "mostra l'andamento recente"),
            BotCommand("calibra", "imposta una calibrazione"),
            BotCommand("whoami", "mostra il tuo ID Telegram"),
        ]
    )


async def plants(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await deny_unless_allowed(update, context):
        return
    store: Store = context.application.bot_data["store"]
    configured_plants = store.plants()
    if not configured_plants:
        await update.effective_message.reply_text(
            "Non hai ancora configurato nessuna pianta.\n"
            "Per iniziare usa /plant NODE CANALE NOME."
        )
        return
    lines = ["🌱 Le tue piante"]
    current_node = None
    for node, channel, name, species, position, _, _ in configured_plants:
        if node != current_node:
            current_node = node
            lines.append(f"\n📍 {store.node_name(node)}")
        details = ", ".join(value for value in (species, position) if value)
        suffix = f" · {details}" if details else ""
        lines.append(f"  └ {name} · canale A{channel}{suffix}")
    await update.effective_message.reply_text("\n".join(lines))


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
            f"Non trovo la pianta {current_name}. Usa /piante per vedere l'alberatura completa."
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


async def plant_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await deny_unless_allowed(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("Scrivi il nome della pianta. Esempio: /pianta Basilico")
        return
    query = " ".join(context.args)
    store: Store = context.application.bot_data["store"]
    matches = store.find_plants(query)
    if not matches:
        await update.effective_message.reply_text(
            f"Non trovo una pianta chiamata {query}. Usa /piante per vedere i nomi disponibili."
        )
        return
    if len(matches) > 1:
        await update.effective_message.reply_text(
            "Ho trovato più piante con questo nome. Rinominale con /plant per distinguerle."
        )
        return
    node, channel, name, species, position, notes, threshold = matches[0]
    payload = store.latest_measurements(node) or {}
    soil_value = None
    for item in payload.get("soil", []):
        if isinstance(item, dict) and item.get("channel") == channel:
            soil_value = item.get("moisture_percent")
            break
    air = payload.get("air", {})
    lines = [f"🌿 {name}", f"Posizione: {position or 'non indicata'}"]
    if species:
        lines.append(f"Specie: {species}")
    if notes:
        lines.append(f"Note: {notes}")
    lines.append(f"Nodo: {store.node_name(node)}")
    lines.append(f"Umidità terreno: {soil_value:.1f}%" if isinstance(soil_value, (int, float)) else "Umidità terreno: dato non disponibile")
    if isinstance(air, dict) and air.get("valid"):
        lines.append(f"Aria: {air.get('temperature_c', '?')} °C · {air.get('humidity_percent', '?')}% umidità")
    light = payload.get("light", {})
    if isinstance(light, dict) and light.get("valid") and isinstance(light.get("lux"), (int, float)):
        lines.append(f"Luce: {light['lux']:.1f} lux")
    if threshold is not None:
        lines.append(f"Soglia configurata: {threshold:.0f}%")
    await update.effective_message.reply_text("\n".join(lines))


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
            lines.append(f"{store.node_name(node)} [{node}]: {kind}{detail} ({received_at})")
        text = "\n".join(lines)
    await update.effective_message.reply_text(text)


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
    since = datetime.now(timezone.utc) - timedelta(hours=24 if period == "24h" else 24 * 7)
    summary = store.air_summary(node, since.isoformat(timespec="seconds"))
    light = store.light_summary(node, since.isoformat(timespec="seconds"))
    if not summary["count"] and not light["count"]:
        await update.effective_message.reply_text(f"Nessun dato valido per {store.node_name(node)} nel periodo {period}.")
        return
    lines = [f"{store.node_name(node)} [{node}] - {period}"]
    if summary["count"]:
        lines.extend([
            f"Temperatura C: min {summary['minimum']:.1f}, max {summary['maximum']:.1f}, "
            f"media {summary['average']:.1f}, ultima {summary['latest']:.1f}",
            f"Umidita aria media: {summary['humidity_average']:.1f}%",
            f"Letture aria valide: {summary['count']}",
        ])
    else:
        lines.append("Aria: nessun dato valido")
    if light["count"]:
        lines.extend([
            f"Luce: min {light['minimum']:.1f}, max {light['maximum']:.1f}, "
            f"media {light['average']:.1f}, ultima {light['latest']:.1f} lux",
            f"Letture luce valide: {light['count']}",
        ])
    else:
        lines.append("Luce: nessun dato valido")
    await update.effective_message.reply_text("\n".join(lines))
    soil_lines = []
    for plant_node, channel, name, *_ in store.plants():
        if plant_node != node:
            continue
        soil = store.soil_summary(node, channel, since.isoformat(timespec="seconds"))
        if soil["count"]:
            soil_lines.append(
                f"{name} (A{channel}): min {soil['minimum']:.1f}%, max {soil['maximum']:.1f}%, "
                f"media {soil['average']:.1f}%, ultima {soil['latest']:.1f}%"
            )
    if soil_lines:
        await update.effective_message.reply_text("\n".join(["Umidita terreno:", *soil_lines]))


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
    mqtt_client = build_mqtt_client(settings, store)
    mqtt_client.loop_start()
    application = Application.builder().token(settings.telegram_token).post_init(configure_command_menu).build()
    application.bot_data.update(settings=settings, store=store, mqtt=mqtt_client)
    application.add_handler(build_wizard_handler())
    application.add_handler(CommandHandler("whoami", whoami))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler(["status", "stato"], status))
    application.add_handler(CommandHandler("piante", plants))
    application.add_handler(CommandHandler("pianta", plant_detail))
    application.add_handler(CommandHandler("rinomina", rename_plant))
    application.add_handler(CommandHandler(["cal", "calibra"], set_calibration))
    application.add_handler(CommandHandler("node", set_node_name))
    application.add_handler(CommandHandler("plant", set_plant))
    application.add_handler(CommandHandler("storico", history))
    application.add_handler(CallbackQueryHandler(button_click))
    LOGGER.info("Hub avviato; utenti autorizzati: %d", len(settings.allowed_user_ids))
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
