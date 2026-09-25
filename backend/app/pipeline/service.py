import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.applications.models import Application, ApplicationStatus
from app.core.config import settings
from app.core.privacy import message_ref
from app.classifier.extractor import ClassificationError, Extractor
from app.classifier.schemas import EmailExtraction
from app.pipeline import matching
from app.pipeline.exceptions import ReviewItemNotFound
from app.pipeline.models import ProcessedMessage
from app.pipeline.schemas import ReviewDecision

logger = logging.getLogger(__name__)

_MORE_SPECIFIC_STATUSES = {"applied", "oa", "interview", "rejected", "offer"}


def process_message(
    db: Session,
    user_id: UUID,
    extractor: Extractor,
    *,
    sync_job_id: UUID | None,
    message_id: str,
    summary: dict,
    body: str,
) -> str | None:
    try:
        extraction = extractor.classify_and_extract(
            subject=summary["subject"], sender=summary["from_"], date=summary["date"], body=body
        )
    except ClassificationError:
        logger.warning("Skipping message %s: extraction failed", message_ref(message_id))
        return None

    review_status, matched_application_id, proposed_action = _apply_decision(
        db, user_id, extraction
    )

    try:
        db.add(
            ProcessedMessage(
                user_id=user_id,
                sync_job_id=sync_job_id,
                gmail_message_id=message_id,
                subject=summary["subject"],
                sender=summary["from_"],
                message_date=summary["date"],
                snippet=summary["snippet"],
                is_job_related=extraction.is_job_related,
                confidence=extraction.confidence,
                extracted_company=extraction.company,
                extracted_position=extraction.position,
                extracted_status=extraction.status,
                extracted_status_date=extraction.status_date,
                matched_application_id=matched_application_id,
                proposed_action=proposed_action,
                review_status=review_status,
            )
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.warning("Skipping message %s: failed to persist", message_ref(message_id))
        return None

    return review_status


def _apply_decision(
    db: Session, user_id: UUID, extraction: EmailExtraction
) -> tuple[str, UUID | None, str | None]:
    if not extraction.is_job_related:
        return "ignored", None, None

    match = matching.find_candidate(db, user_id, extraction.company or "", extraction.position or "")

    if (
        match.application is not None
        and not match.ambiguous
        and extraction.status == "other"
        and match.application.status.value in _MORE_SPECIFIC_STATUSES
    ):
        return "ignored", match.application.id, None

    high_confidence = extraction.confidence >= settings.classification_confidence_threshold
    is_auto_managed = match.application is not None and match.application.source == "gmail"
    touches_existing_application = match.application is not None

    if (touches_existing_application and not is_auto_managed) or match.ambiguous or not high_confidence:
        return (
            "pending_review",
            match.application.id if match.application else None,
            match.action,
        )

    if match.action == "create":
        new_application = Application(
            user_id=user_id,
            company=extraction.company or "",
            position=extraction.position or "",
            status=ApplicationStatus(extraction.status or "applied"),
            applied_at=extraction.status_date,
            source="gmail",
        )
        db.add(new_application)
        db.flush()
        return "auto_applied", new_application.id, "create"

    application = match.application
    if extraction.status is not None:
        application.status = ApplicationStatus(extraction.status)
    if extraction.status_date is not None:
        application.applied_at = extraction.status_date
    return "auto_applied", application.id, "update"


def list_review_queue(db: Session, user_id: UUID) -> list[ProcessedMessage]:
    return list(
        db.scalars(
            select(ProcessedMessage)
            .where(
                ProcessedMessage.user_id == user_id,
                ProcessedMessage.review_status == "pending_review",
            )
            .order_by(ProcessedMessage.created_at.desc())
        )
    )


def _get_pending_item(db: Session, user_id: UUID, item_id: UUID) -> ProcessedMessage:
    item = db.scalars(
        select(ProcessedMessage).where(
            ProcessedMessage.id == item_id,
            ProcessedMessage.user_id == user_id,
            ProcessedMessage.review_status == "pending_review",
        )
    ).one_or_none()
    if item is None:
        raise ReviewItemNotFound(item_id)
    return item


def approve_review_item(
    db: Session, user_id: UUID, item_id: UUID, decision: ReviewDecision
) -> Application:
    item = _get_pending_item(db, user_id, item_id)

    company = decision.company or item.extracted_company or ""
    position = decision.position or item.extracted_position or ""
    status_value = decision.status or item.extracted_status
    status_date = decision.status_date or item.extracted_status_date

    if item.proposed_action == "update" and item.matched_application_id is not None:
        application = db.get(Application, item.matched_application_id)
        if application is None or application.user_id != user_id:
            raise ReviewItemNotFound(item_id)
        if company:
            application.company = company
        if position:
            application.position = position
        if status_value == "other" and application.status.value in _MORE_SPECIFIC_STATUSES:
            pass  # never regress a specific status to "other" — matches _apply_decision's guard
        elif status_value is not None:
            application.status = ApplicationStatus(status_value)
        if status_date is not None:
            application.applied_at = status_date
    else:
        application = Application(
            user_id=user_id,
            company=company,
            position=position,
            status=ApplicationStatus(status_value or "applied"),
            applied_at=status_date,
            source="gmail",
        )
        db.add(application)
        db.flush()

    item.review_status = "approved"
    item.matched_application_id = application.id
    db.commit()
    db.refresh(application)
    return application


def reject_review_item(db: Session, user_id: UUID, item_id: UUID) -> None:
    item = _get_pending_item(db, user_id, item_id)
    item.review_status = "rejected"
    db.commit()
