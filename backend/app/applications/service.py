from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications.exceptions import ApplicationNotFound
from app.applications.models import Application
from app.applications.schemas import ApplicationCreate, ApplicationUpdate


def list_applications(db: Session) -> list[Application]:
    statement = select(Application).order_by(Application.created_at.desc())
    return list(db.scalars(statement))


def get_application(db: Session, application_id: UUID) -> Application:
    application = db.get(Application, application_id)
    if application is None:
        raise ApplicationNotFound(application_id)
    return application


def create_application(db: Session, data: ApplicationCreate) -> Application:
    application = Application(**data.model_dump())
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def update_application(
    db: Session, application_id: UUID, data: ApplicationUpdate
) -> Application:
    application = get_application(db, application_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(application, field, value)
    db.commit()
    db.refresh(application)
    return application


def delete_application(db: Session, application_id: UUID) -> None:
    application = get_application(db, application_id)
    db.delete(application)
    db.commit()
