from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.scenario import ScenarioRepository
from app.api import http as http_api
from app.api import ws as ws_api
from app.core.config import AppConfig, Settings
from app.db.database import Database
from app.providers.registry import ProviderCache

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(
            level=getattr(logging, settings.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        config = AppConfig.load(settings.config_path)
        scenarios = ScenarioRepository(settings.scenarios_dir)
        scenarios.load()
        db = Database(settings.data_dir / "app.db")
        db.init()

        app.state.settings = settings
        app.state.config = config
        app.state.scenarios = scenarios
        app.state.db = db
        app.state.cache = ProviderCache()

        logger.info(
            "ready: profiles=%s scenarios=%s data=%s",
            config.profile_names(),
            {language: len(scenarios.list(language)) for language in ("en", "ja", "zh", "de")},
            settings.data_dir,
        )
        yield
        app.state.cache.clear()

    app = FastAPI(title="AI Speaking Tutor API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(http_api.router)
    app.include_router(ws_api.router)
    return app


app = create_app()
