"""Per-user state helpers for Telegram conversations."""

import uuid
from typing import Any


def wizard_token(context: Any, value: str) -> str:
    token = uuid.uuid4().hex[:12]
    context.user_data.setdefault("wizard_callbacks", {})[token] = value
    return token


def wizard_value(context: Any, token: str) -> str | None:
    return context.user_data.get("wizard_callbacks", {}).get(token)
