import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    from app import alert_job, button_click, start
    from core import Settings, Store
    from conversation_state import wizard_token
except ModuleNotFoundError as error:
    button_click = None
    start = None
    IMPORT_ERROR = error
else:
    IMPORT_ERROR = None


@unittest.skipIf(IMPORT_ERROR is not None, f"bot dependencies unavailable: {IMPORT_ERROR}")
class HandlerTests(unittest.IsolatedAsyncioTestCase):
    def make_context(self, store):
        settings = Settings(
            mqtt_host="127.0.0.1",
            mqtt_port=1883,
            mqtt_username="",
            mqtt_password="",
            topic_prefix="plants",
            telegram_token="token",
            allowed_user_ids=frozenset({42}),
            database_path=Path(":memory:"),
        )
        return SimpleNamespace(
            application=SimpleNamespace(bot_data={"settings": settings, "store": store}),
            user_data={},
        )

    def make_update(self, user_id=42, callback_data=None):
        user = SimpleNamespace(id=user_id)
        message = SimpleNamespace(reply_text=AsyncMock())
        if callback_data is None:
            return SimpleNamespace(effective_user=user, effective_message=message)
        query = SimpleNamespace(
            data=callback_data,
            from_user=user,
            message=message,
            answer=AsyncMock(),
        )
        return SimpleNamespace(callback_query=query, effective_user=user, effective_message=message)

    async def test_start_sends_menu_with_calibration_button(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            update = self.make_update()
            context = self.make_context(store)

            await start(update, context)

            update.effective_message.reply_text.assert_awaited_once()
            markup = update.effective_message.reply_text.await_args.kwargs["reply_markup"]
            callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
            self.assertIn("wizard:cal:start", callbacks)

    async def test_menu_callback_replies_without_editing_previous_message(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            update = self.make_update(callback_data="menu:home")
            context = self.make_context(store)

            await button_click(update, context)

            update.callback_query.answer.assert_awaited_once()
            update.callback_query.message.reply_text.assert_awaited_once()
            self.assertFalse(hasattr(update.callback_query, "edit_message_text"))

    async def test_unauthorized_callback_does_not_send_bot_message(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            update = self.make_update(user_id=99, callback_data="menu:home")
            context = self.make_context(store)

            await button_click(update, context)

            update.callback_query.answer.assert_awaited_once_with(
                "Accesso non autorizzato.", show_alert=True
            )
            update.callback_query.message.reply_text.assert_not_awaited()

    async def test_status_callback_renders_node_sections_and_navigation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            store.save("node", "state", {"state": "online"})
            update = self.make_update(callback_data="menu:status")
            context = self.make_context(store)

            await button_click(update, context)

            message = update.callback_query.message.reply_text.await_args
            self.assertIn("STATO NODI", message.args[0])
            callbacks = [
                button.callback_data
                for row in message.kwargs["reply_markup"].inline_keyboard
                for button in row
            ]
            self.assertIn("menu:home", callbacks)

    async def test_plant_history_callback_includes_back_navigation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            store.save("node", "state", {"state": "online"})
            store.set_plant("node", 0, "Basilico")
            store.save("node", "measurements", {"air": {"valid": True, "temperature_c": 20, "humidity_percent": 45}})
            context = self.make_context(store)
            token = wizard_token(context, "node:0")
            update = self.make_update(callback_data=f"history:{token}:24h")

            await button_click(update, context)

            message = update.callback_query.message.reply_text.await_args
            callbacks = [
                button.callback_data
                for row in message.kwargs["reply_markup"].inline_keyboard
                for button in row
            ]
            self.assertIn("menu:home", callbacks)

    async def test_alert_job_notifies_on_transition_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "hub.sqlite3")
            store.save("node", "state", {"state": "online"})
            store.set_plant("node", 0, "Basilico", threshold_percent=35)
            store.save("node", "measurements", {"soil": [{"channel": 0, "moisture_percent": 20}]})
            context = self.make_context(store)
            context.application.bot = SimpleNamespace(send_message=AsyncMock())

            await alert_job(context)
            await alert_job(context)
            self.assertEqual(context.application.bot.send_message.await_count, 1)
            self.assertIn("sotto soglia", context.application.bot.send_message.await_args.kwargs["text"])

            store.save("node", "measurements", {"soil": [{"channel": 0, "moisture_percent": 40}]})
            await alert_job(context)
            self.assertEqual(context.application.bot.send_message.await_count, 2)
            self.assertIn("Rientrato", context.application.bot.send_message.await_args.kwargs["text"])


if __name__ == "__main__":
    unittest.main()
