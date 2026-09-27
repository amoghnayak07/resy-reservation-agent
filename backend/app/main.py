from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.graph import build_graph, open_checkpointer
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.config import settings
from app.errors import register_exception_handlers
from app.logging_config import configure_logging, log_requests
from app.observability.langfuse import init_langfuse, shutdown_langfuse


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_langfuse()
    async with open_checkpointer() as checkpointer:
        app.state.graph = build_graph(checkpointer)
        yield
    shutdown_langfuse()


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.middleware("http")(log_requests)

    register_exception_handlers(app)

    app.include_router(conversations_router)
    app.include_router(chat_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "env": settings.app_env,
            "version": settings.render_git_commit,
        }

    return app


app = create_app()
