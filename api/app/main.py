from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from api.app.core.settings import get_settings
from api.app.features.health.router import router as health_router
from api.app.features.match_review.chunk_router import (
    STATIC_DIRECTORY as MATCH_REVIEW_STATIC_DIRECTORY,
)
from api.app.features.match_review.chunk_router import (
    api_router as match_review_chunk_api_router,
)
from api.app.features.match_review.chunk_router import (
    screen_router as match_review_screen_router,
)
from api.app.features.match_review.router import router as match_review_router
from api.app.features.normalization_review.router import (
    STATIC_DIRECTORY as NORMALIZATION_REVIEW_STATIC_DIRECTORY,
)
from api.app.features.normalization_review.router import (
    api_router as normalization_review_api_router,
)
from api.app.features.normalization_review.router import (
    screen_router as normalization_review_screen_router,
)
from api.app.features.resolve.router import router as resolve_router
from api.app.features.review_queue.router import router as review_queue_router
from api.app.features.rule_review.router import router as rule_review_router
from api.app.features.tecdoc_connections.router import router as tecdoc_connections_router
from api.app.features.tecdoc_review.router import router as tecdoc_review_router


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", include_in_schema=False)
    def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/match-review")

    app.include_router(health_router)
    app.include_router(resolve_router)
    app.include_router(match_review_router)
    app.include_router(normalization_review_api_router)
    app.include_router(normalization_review_screen_router)
    app.include_router(match_review_chunk_api_router)
    app.include_router(match_review_screen_router)
    app.include_router(review_queue_router)
    app.include_router(rule_review_router)
    app.include_router(tecdoc_review_router)
    app.include_router(tecdoc_connections_router)
    app.mount(
        "/match-review/assets",
        StaticFiles(directory=MATCH_REVIEW_STATIC_DIRECTORY),
        name="match-review-assets",
    )
    app.mount(
        "/normalization-review/assets",
        StaticFiles(directory=NORMALIZATION_REVIEW_STATIC_DIRECTORY),
        name="normalization-review-assets",
    )

    return app


app = create_app()
