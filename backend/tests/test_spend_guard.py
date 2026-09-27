import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import DailySpend
from app.errors import ApiError
from app.guards.spend import SqlSpendGuard

pytestmark = pytest.mark.integration


async def test_check_cap_blocks_at_and_over_cap_allows_under() -> None:
    engine = create_async_engine(settings.database_url)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    today = datetime.now(UTC).date()

    try:
        async with session_maker() as db:
            await db.execute(delete(DailySpend).where(DailySpend.day == today))
            db.add(DailySpend(day=today, spend_usd=Decimal("1.00"), llm_calls=1))
            await db.commit()

            guard = SqlSpendGuard(db)
            await guard.check_cap()  # under settings.daily_spend_cap_usd (2.00) -> no raise

        async with session_maker() as db:
            row = await db.get(DailySpend, today)
            assert row is not None
            row.spend_usd = float(settings.daily_spend_cap_usd)
            await db.commit()

            guard = SqlSpendGuard(db)
            with pytest.raises(ApiError) as exc_info:
                await guard.check_cap()
            assert exc_info.value.status_code == 429
            assert exc_info.value.code == "daily_budget_reached"
    finally:
        async with session_maker() as db:
            await db.execute(delete(DailySpend).where(DailySpend.day == today))
            await db.commit()
        await engine.dispose()


async def test_record_increments_are_atomic_under_concurrency() -> None:
    engine = create_async_engine(settings.database_url)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    today = datetime.now(UTC).date()

    try:
        async with session_maker() as db:
            await db.execute(delete(DailySpend).where(DailySpend.day == today))
            await db.commit()

        async def record_one(cost: Decimal) -> None:
            async with session_maker() as db:
                await SqlSpendGuard(db).record(cost)

        await asyncio.gather(record_one(Decimal("0.10")), record_one(Decimal("0.25")))

        async with session_maker() as db:
            row = await db.get(DailySpend, today)
            assert row is not None
            assert Decimal(str(row.spend_usd)) == Decimal("0.35")
            assert row.llm_calls == 2
    finally:
        async with session_maker() as db:
            await db.execute(delete(DailySpend).where(DailySpend.day == today))
            await db.commit()
        await engine.dispose()
