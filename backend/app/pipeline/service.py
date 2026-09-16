import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications.models import Application, ApplicationStatus
from app.core.config import settings
from app.gmail import google_api
from app.gmail import service as gmail_service
from app.gmail.exceptions import GmailNotConnected
from app.llm.client import Extractor, LLMExtractionError
from app.llm.schemas import EmailExtraction
from app.pipeline import matching
from app.pipeline.models import ProcessedMessage
from app.pipeline.schemas import ProcessResult

logger = logging.getLogger(__name__)

_MORE_SPECIFIC_STATUSES = {"applied", "oa", "interview", "rejected", "offer"}


def process_inbox(db: Session, user_id: UUID, extractor: Extractor) -> ProcessResult:
    connection = gmail_service.get_connection(db, user_id)
    if connection is None:
        raise GmailNotConnected(user_id)

    access_token = gmail_service.get_valid_access_token(db, connection)
    message_ids = google_api.list_message_ids(access_token, limit=settings.pipeline_batch_limit)

    already_processed = set(
        db.scalars(
            select(ProcessedMessage.gmail_message_id).where(
                ProcessedMessage.user_id == user_id,
                ProcessedMessage.gmail_message_id.in_(message_ids),
            )
        )
    )
    new_message_ids = [mid for mid in message_ids if mid not in already_processed]

    auto_applied = 0
    queued_for_review = 0
    ignored = 0

    for message_id in new_message_ids:
        try:
            summary = google_api.get_message_summary(access_token, message_id)
            body = google_api.get_message_body(access_token, message_id)
            extraction = extractor.classify_and_extract(
                subject=summary["subject"],
                sender=summary["from_"],
                date=summary["date"],
                body=body,
            )
        except (google_api.GoogleApiError, LLMExtractionError):
            logger.warning("Skipping message %s: fetch or extraction failed", message_id)
            continue

        review_status, matched_application_id, proposed_action = _apply_decision(
            db, user_id, extraction
        )

        db.add(
            ProcessedMessage(
                user_id=user_id,
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

        if review_status == "auto_applied":
            auto_applied += 1
        elif review_status == "pending_review":
            queued_for_review += 1
        else:
            ignored += 1

    return ProcessResult(
        processed=len(new_message_ids),
        auto_applied=auto_applied,
        queued_for_review=queued_for_review,
        ignored=ignored,
    )


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

    high_confidence = extraction.confidence >= settings.llm_confidence_threshold
    touches_manual = match.application is not None and match.application.source == "manual"

    if touches_manual or match.ambiguous or not high_confidence:
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
