from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.applications.schemas import ApplicationRead
from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.pipeline import service
from app.pipeline.schemas import ReviewDecision, ReviewItem
from app.users.models import User

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.get("/review", response_model=list[ReviewItem])
def pipeline_review_list(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> list:
    return service.list_review_queue(db, current_user.id)


@router.post("/review/{item_id}/approve", response_model=ApplicationRead)
def pipeline_review_approve(
    item_id: UUID,
    decision: ReviewDecision = ReviewDecision(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.approve_review_item(db, current_user.id, item_id, decision)


@router.post("/review/{item_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
def pipeline_review_reject(
    item_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    service.reject_review_item(db, current_user.id, item_id)
