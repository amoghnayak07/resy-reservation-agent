"""Manual CLI to verify graph persistence across process restarts. Not deployed."""

import argparse
import asyncio

from langchain_core.runnables import RunnableConfig

from app.agent.graph import build_graph, open_checkpointer


async def main(conversation_id: str, timezone: str) -> None:
    async with open_checkpointer() as checkpointer:
        graph = build_graph(checkpointer)
        config: RunnableConfig = {
            "configurable": {"thread_id": conversation_id, "timezone": timezone}
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
    args = parser.parse_args()
    asyncio.run(main(args.conversation_id, args.timezone))
