"""Telegram texts and inline keyboard renderers for the hub."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

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


def main_keyboard(include_user_admin: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("➕ Aggiungi pianta", callback_data="wizard:plant:start")],
        [InlineKeyboardButton("⚙️ Configura nodo", callback_data="wizard:node:start")],
        [InlineKeyboardButton("🛠️ Calibra sensore", callback_data="wizard:cal:start")],
        [InlineKeyboardButton("🌱 Le mie piante", callback_data="menu:plants")],
        [InlineKeyboardButton("📊 Stato nodi", callback_data="menu:status")],
        [InlineKeyboardButton("❓ Aiuto", callback_data="menu:help")],
    ]
    if include_user_admin:
        buttons.append([InlineKeyboardButton("👥 Gestione utenti", callback_data="users:list")])
    return InlineKeyboardMarkup(buttons)


def user_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Add User", callback_data="users:add")],
            [InlineKeyboardButton("Remove User", callback_data="users:remove")],
            [InlineKeyboardButton("Users List", callback_data="users:list")],
            [InlineKeyboardButton("⬅️ Menu", callback_data="menu:home")],
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Annulla", callback_data="wizard:cancel")],
        [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
    ])
