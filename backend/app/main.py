from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app.db.session import get_db

from app.applications.exceptions import ApplicationNotFound
from app.applications.router import router as applications_router
from app.auth.router import router as auth_router
from app.core.config import settings
from app.gmail.exceptions import REAUTH_CODE, REAUTH_MESSAGE, GmailNotConnected, GmailReauthRequired
from app.gmail.google_api import GoogleApiError
from app.gmail.router import router as gmail_router
from app.pipeline.exceptions import ReviewItemNotFound
from app.pipeline.router import router as pipeline_router
from app.sync.exceptions import SyncAlreadyRunning, SyncJobNotFound
from app.sync.router import router as sync_router
from app.sync.schemas import SyncJobRead


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.run_worker_in_process:
        from app.sync.inprocess import start_worker_thread

        start_worker_thread()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Job Application Tracker", version="0.1.0", lifespan=lifespan)

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

    @app.exception_handler(GmailReauthRequired)
    async def handle_gmail_reauth_required(request: Request, exc: GmailReauthRequired) -> JSONResponse:
        # 403, not 409: the frontend reads a 409 body as the active SyncJob,
        # while any client shows a 403's detail as a plain error message.
        return JSONResponse(status_code=403, content={"detail": REAUTH_MESSAGE, "code": REAUTH_CODE})

    @app.exception_handler(GoogleApiError)
    async def handle_google_api_error(
        request: Request, exc: GoogleApiError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={"detail": "Gmail request failed. Try reconnecting your Gmail account."},
        )

    @app.exception_handler(ReviewItemNotFound)
    async def handle_review_item_not_found(
        request: Request, exc: ReviewItemNotFound
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(SyncAlreadyRunning)
    async def handle_sync_already_running(request: Request, exc: SyncAlreadyRunning) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=SyncJobRead.model_validate(exc.job).model_dump(mode="json"),
        )

    @app.exception_handler(SyncJobNotFound)
    async def handle_sync_job_not_found(request: Request, exc: SyncJobNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def health_ready(db: Session = Depends(get_db)) -> JSONResponse:
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return JSONResponse(status_code=200, content={"status": "ok"})

    _WORKER_STALE_SECONDS = 30  # 15x POLL_INTERVAL_SECONDS — generous margin

    @app.get("/health/worker")
    def health_worker() -> JSONResponse:
        from datetime import datetime, timezone

        from app.sync.worker import get_last_poll_at

        last_poll = get_last_poll_at()
        if last_poll is None:
            return JSONResponse(
                status_code=503, content={"status": "not_running", "last_poll_at": None}
            )
        staleness = (datetime.now(timezone.utc) - last_poll).total_seconds()
        body = {"status": "ok", "last_poll_at": last_poll.isoformat(), "seconds_since_poll": staleness}
        if staleness > _WORKER_STALE_SECONDS:
            body["status"] = "stale"
            return JSONResponse(status_code=503, content=body)
        return JSONResponse(status_code=200, content=body)

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(applications_router, prefix="/api/v1")
    app.include_router(gmail_router, prefix="/api/v1")
    app.include_router(pipeline_router, prefix="/api/v1")
    app.include_router(sync_router, prefix="/api/v1")

    return app


app = create_app()
