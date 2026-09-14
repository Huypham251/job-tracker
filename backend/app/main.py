from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app.applications.exceptions import ApplicationNotFound
from app.applications.router import router as applications_router
from app.auth.router import router as auth_router
from app.core.config import settings
from app.gmail.exceptions import GmailNotConnected
from app.gmail.google_api import GoogleApiError
from app.gmail.router import router as gmail_router


def create_app() -> FastAPI:
    app = FastAPI(title="Job Application Tracker", version="0.1.0")

    # Used only for the few seconds of the OAuth state/nonce handshake —
    # entirely separate from the app's own access_token cookie. Added
    # before CORSMiddleware so CORS ends up outermost (add_middleware
    # makes the last-added middleware outermost).
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        max_age=300,
        https_only=settings.cookie_secure,
        same_site="lax",
    )
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

    @app.exception_handler(GmailNotConnected)
    async def handle_gmail_not_connected(
        request: Request, exc: GmailNotConnected
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(GoogleApiError)
    async def handle_google_api_error(
        request: Request, exc: GoogleApiError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={"detail": "Gmail request failed. Try reconnecting your Gmail account."},
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(applications_router, prefix="/api/v1")
    app.include_router(gmail_router, prefix="/api/v1")

    return app


app = create_app()
