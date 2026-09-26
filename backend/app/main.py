from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.logging_config import configure_logging


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "env": settings.app_env,
            "version": settings.render_git_commit,
        }

    return app


app = create_app()
