from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import settings, ensure_runtime_folders
from app.database import init_db
from app.core.redis import init_redis, close_redis
def create_app() -> FastAPI:
    ensure_runtime_folders()

    app = FastAPI(title=settings.app_title, version=settings.app_version)

    app.mount("/static", StaticFiles(directory=settings.static_folder), name="static")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8000",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def on_startup():
        init_db()   
        await init_redis()

    @app.on_event("shutdown")
    async def on_shutdown():
        await close_redis()

    app.include_router(api_router)
    return app


app = create_app()
