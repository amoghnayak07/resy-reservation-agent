"""Manual CLI to chat with the agent locally in a Resy region (live, read-only Resy searches).
Not deployed. Never books: RESY_WRITES_ENABLED stays false locally. Without a conversation ID it
creates a new conversation row (owned by the "cli-local" session), which prepare_booking needs.

    uv run python -m scripts.chat_cli --region new-york-ny
"""

import argparse
import asyncio

from langchain_core.runnables import RunnableConfig

from app.agent.graph import build_graph, open_checkpointer
from app.agent.tools import make_tools
from app.db.engine import async_session_maker
from app.db.pending_bookings import SqlPendingBookingRepository
from app.db.repository import SqlConversationRepository
from app.regions import RegionDirectory, region_config
from app.resy.client import ResyClient

CLI_SESSION_ID = "cli-local"


async def main(conversation_id: str | None, region_slug: str) -> None:
    if conversation_id is None:
        async with async_session_maker() as db:
            conversation_id = str((await SqlConversationRepository(db).create(CLI_SESSION_ID)).id)
    async with ResyClient.from_settings() as resy, open_checkpointer() as checkpointer:
        regions = RegionDirectory(resy)
        city = await regions.get(region_slug)
        if city is None:
            raise SystemExit(f"Unknown region {region_slug!r} (use a Resy city slug).")
        bookings = SqlPendingBookingRepository(async_session_maker)
        graph = build_graph(checkpointer, tools=make_tools(resy, bookings, regions))
        config: RunnableConfig = {
            "configurable": {
                "thread_id": conversation_id,
                "session_id": CLI_SESSION_ID,
                **region_config(city),
            },
            "recursion_limit": 12,
        }
        print(f"Conversation {conversation_id} in {city.name}. Ctrl+C to exit.")
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
    parser.add_argument("--region", required=True, help="Resy city slug, e.g. new-york-ny")
    args = parser.parse_args()
    asyncio.run(main(args.conversation_id, args.region))
