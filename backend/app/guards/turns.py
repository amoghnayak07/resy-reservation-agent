"""Caps how many turns a single conversation can have (CLAUDE.md ->
MAX_TURNS_PER_CONVERSATION)."""

from app.config import settings
from app.db.models import Conversation
from app.errors import ApiError


def check_turn_limit(conversation: Conversation) -> None:
    if conversation.message_count >= settings.max_turns_per_conversation:
        raise ApiError(
            409,
            "conversation_full",
            "This conversation has reached its turn limit. Start a new conversation.",
        )
