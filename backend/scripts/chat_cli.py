"""Manual CLI to chat with the agent locally (persistence across restarts, and live Resy
searches when --lat/--lng are given). Not deployed. Never books: RESY_WRITES_ENABLED stays
false locally."""

import argparse
import asyncio

from langchain_core.runnables import RunnableConfig

from app.agent.graph import build_graph, open_checkpointer
from app.agent.tools import make_tools
from app.resy.client import ResyClient


async def main(conversation_id: str, timezone: str, lat: float | None, lng: float | None) -> None:
    location = (
        {"lat": round(lat, 3), "lng": round(lng, 3)}
        if lat is not None and lng is not None
        else None
    )
    async with ResyClient.from_settings() as resy, open_checkpointer() as checkpointer:
        graph = build_graph(checkpointer, tools=make_tools(resy))
        config: RunnableConfig = {
            "configurable": {
                "thread_id": conversation_id,
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
    parser.add_argument("conversation_id")
    parser.add_argument(
        "--timezone", required=True, help="IANA timezone name, e.g. America/New_York"
    )
    parser.add_argument("--lat", type=float, default=None)
    parser.add_argument("--lng", type=float, default=None)
    args = parser.parse_args()
    asyncio.run(main(args.conversation_id, args.timezone, args.lat, args.lng))
