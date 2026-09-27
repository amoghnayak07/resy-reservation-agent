"""Global daily spend cap. Only a guard against a runaway meter -- Langfuse stays
the analytics source of truth (CLAUDE.md). Kept behind a Protocol, like
ConversationRepository, so chat tests can swap in an in-memory fake instead of
hitting Postgres."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import DailySpend
from app.errors import ApiError


def _today() -> date:
    return datetime.now(UTC).date()


class SpendGuard(Protocol):
    async def check_cap(self) -> None: ...

    async def record(self, cost_usd: Decimal) -> None: ...


class SqlSpendGuard:
    """Default implementation, backed by the app's Postgres database."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def check_cap(self) -> None:
        row = await self._db.get(DailySpend, _today())
        spend = Decimal(str(row.spend_usd)) if row is not None else Decimal("0")
        if spend >= settings.daily_spend_cap_usd:
            raise ApiError(
                429,
                "daily_budget_reached",
                "Daily demo budget reached, try again tomorrow.",
            )

    async def record(self, cost_usd: Decimal) -> None:
        stmt = (
            insert(DailySpend)
            .values(day=_today(), spend_usd=cost_usd, llm_calls=1)
            .on_conflict_do_update(
                index_elements=[DailySpend.day],
                set_={
                    "spend_usd": DailySpend.spend_usd + cost_usd,
                    "llm_calls": DailySpend.llm_calls + 1,
                },
            )
        )
        await self._db.execute(stmt)
        await self._db.commit()
