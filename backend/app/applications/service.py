from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications.exceptions import ApplicationNotFound
from app.applications.models import Application
from app.applications.schemas import ApplicationCreate, ApplicationUpdate


def list_applications(db: Session, user_id: UUID) -> list[Application]:
    statement = (
        select(Application)
        .where(Application.user_id == user_id)
        .order_by(Application.created_at.desc())
    )
    return list(db.scalars(statement))


def get_application(db: Session, user_id: UUID, application_id: UUID) -> Application:
    statement = select(Application).where(
        Application.id == application_id, Application.user_id == user_id
    )
    application = db.scalars(statement).one_or_none()
    if application is None:
        raise ApplicationNotFound(application_id)
    return application


def create_application(
    db: Session, user_id: UUID, data: ApplicationCreate
) -> Application:
    application = Application(user_id=user_id, **data.model_dump())
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def update_application(
    db: Session, user_id: UUID, application_id: UUID, data: ApplicationUpdate
) -> Application:
    application = get_application(db, user_id, application_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(application, field, value)
    db.commit()
    db.refresh(application)
    return application


def delete_application(db: Session, user_id: UUID, application_id: UUID) -> None:
    application = get_application(db, user_id, application_id)
    db.delete(application)
    db.commit()
