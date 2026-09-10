from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.applications.exceptions import ApplicationNotFound
from app.applications.router import router as applications_router
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(title="Job Application Tracker", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ApplicationNotFound)
    async def handle_application_not_found(
        request: Request, exc: ApplicationNotFound
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(applications_router, prefix="/api/v1")

    return app


app = create_app()
