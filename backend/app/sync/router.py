from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.sync import service
from app.sync.exceptions import SyncJobNotFound
from app.sync.schemas import SyncJobRead
from app.users.models import User

router = APIRouter(prefix="/gmail", tags=["sync"])


@router.post("/sync", response_model=SyncJobRead, status_code=status.HTTP_202_ACCEPTED)
def start_sync(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> SyncJobRead:
    return service.enqueue_sync(db, current_user.id)


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
