import time
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.auth.dependencies import get_current_user
from app.core.ratelimit import limit_per_user
from app.db.session import get_db
from app.sync import dispatch, service
from app.sync.exceptions import SyncAlreadyRunning, SyncJobNotFound
from app.sync.schemas import SyncJobRead
from app.users.models import User

router = APIRouter(prefix="/gmail", tags=["sync"])

# A Sync click on an already-queued job asks for the worker again (Phase 9);
# repeated clicks re-dispatch at most this often per job (Phase 10). One
# process, so an in-memory map is enough; a restart only allows one extra.
REKICK_COOLDOWN_SECONDS = 30.0
_last_rekick: dict[UUID, float] = {}


def _rekick_allowed(job_id: UUID, now: float) -> bool:
    last = _last_rekick.get(job_id)
    if last is not None and now - last < REKICK_COOLDOWN_SECONDS:
        return False
    _last_rekick[job_id] = now
    return True


@router.post(
    "/sync",
    response_model=SyncJobRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(limit_per_user("sync", 10))],
)
def start_sync(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # The worker is requested only after the job is committed and the
    # response is on its way (a background task), and request_worker never
    # raises — so a failed dispatch can't change this response; the job just
    # stays queued for the lane's cron fallback or the next Sync click.
    try:
        job = service.enqueue_sync(db, current_user.id)
    except SyncAlreadyRunning as exc:
        if not service.should_rekick(exc.job):
            raise
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=SyncJobRead.model_validate(exc.job).model_dump(mode="json"),
            background=(
                BackgroundTask(dispatch.request_worker, exc.job.job_type)
                if _rekick_allowed(exc.job.id, time.monotonic())
                else None
            ),
        )
    background_tasks.add_task(dispatch.request_worker, job.job_type)
    return job


@router.get("/sync/latest", response_model=SyncJobRead | None)
def get_latest_sync(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> SyncJobRead | None:
    return service.get_latest_job(db, current_user.id)


@router.get("/sync/{job_id}", response_model=SyncJobRead)
def get_sync(
    job_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SyncJobRead:
    job = service.get_job(db, current_user.id, job_id)
    if job is None:
        raise SyncJobNotFound(job_id)
    return job
