"""FastAPI application entry point for AutoRoster."""

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .api.routes_dropoff import router as dropoff_router
from .api.routes_export import router as export_router
from .api.routes_health import router as health_router
from .api.routes_history import router as history_router
from .api.routes_plan import router as plan_router
from .core.config import config
from .core.logging import logger


def create_app() -> FastAPI:
    app = FastAPI(
        title="AutoRoster Transit Engine",
        description="Employee transport planning and bus route optimization API.",
        version="0.1.0",
    )

    app.include_router(plan_router)
    app.include_router(export_router)
    app.include_router(health_router)
    app.include_router(dropoff_router)
    app.include_router(history_router)

    static_path = config.ROOT_DIR / "static"
    if static_path.exists():
        app.mount("/static", StaticFiles(directory=static_path), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        template_path = config.ROOT_DIR / "templates" / "index.html"
        if template_path.exists():
            return FileResponse(template_path)
        return PlainTextResponse("AutoRoster Transit Engine Active.")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        icon_path = config.ROOT_DIR / "static" / "favicon.ico"
        if icon_path.exists():
            return FileResponse(icon_path)
        return PlainTextResponse("", status_code=204)

    logger.info("AutoRoster FastAPI application initialized successfully.")
    return app


app = create_app()
