"""Manual CLI to chat with the agent locally (persistence across restarts, and live Resy
searches when --lat/--lng are given). Not deployed. Never books: RESY_WRITES_ENABLED stays
false locally. Without a conversation ID it creates a new conversation row (owned by the
"cli-local" session), which prepare_booking needs."""

import argparse
import asyncio

from langchain_core.runnables import RunnableConfig

from app.agent.graph import build_graph, open_checkpointer
from app.agent.tools import make_tools
from app.db.engine import async_session_maker
from app.db.pending_bookings import SqlPendingBookingRepository
from app.db.repository import SqlConversationRepository
from app.resy.client import ResyClient

CLI_SESSION_ID = "cli-local"


async def main(
    conversation_id: str | None, timezone: str, lat: float | None, lng: float | None
) -> None:
    location = (
        {"lat": round(lat, 3), "lng": round(lng, 3)}
        if lat is not None and lng is not None
        else None
    )
    if conversation_id is None:
        async with async_session_maker() as db:
            conversation_id = str((await SqlConversationRepository(db).create(CLI_SESSION_ID)).id)
    async with ResyClient.from_settings() as resy, open_checkpointer() as checkpointer:
        bookings = SqlPendingBookingRepository(async_session_maker)
        graph = build_graph(checkpointer, tools=make_tools(resy, bookings))
        config: RunnableConfig = {
            "configurable": {
                "thread_id": conversation_id,
                "session_id": CLI_SESSION_ID,
                "timezone": timezone,
                "location_available": location is not None,
                "location": location,
            },
            "recursion_limit": 12,
        }
        print(f"Conversation {conversation_id}. Ctrl+C to exit.")
        while True:
            try:
                text = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
            result = await graph.ainvoke({"messages": [("user", text)]}, config=config)
            reply = result["messages"][-1]
            print(f"agent> {reply.content}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("conversation_id", nargs="?", default=None)
    parser.add_argument(
        "--timezone", required=True, help="IANA timezone name, e.g. America/New_York"
    )
    parser.add_argument("--lat", type=float, default=None)
    parser.add_argument("--lng", type=float, default=None)
    args = parser.parse_args()
    asyncio.run(main(args.conversation_id, args.timezone, args.lat, args.lng))
