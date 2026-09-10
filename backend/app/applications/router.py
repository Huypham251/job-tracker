from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.applications import service
from app.applications.schemas import (
    ApplicationCreate,
    ApplicationRead,
    ApplicationUpdate,
)
from app.db.session import get_db

router = APIRouter(prefix="/applications", tags=["applications"])


@router.get("", response_model=list[ApplicationRead])
def list_applications(db: Session = Depends(get_db)) -> list:
    return service.list_applications(db)


@router.post("", response_model=ApplicationRead, status_code=status.HTTP_201_CREATED)
def create_application(
    payload: ApplicationCreate, db: Session = Depends(get_db)
):
    return service.create_application(db, payload)


@router.get("/{application_id}", response_model=ApplicationRead)
def get_application(application_id: UUID, db: Session = Depends(get_db)):
    return service.get_application(db, application_id)


@router.patch("/{application_id}", response_model=ApplicationRead)
def update_application(
    application_id: UUID,
    payload: ApplicationUpdate,
    db: Session = Depends(get_db),
):
    return service.update_application(db, application_id, payload)


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_application(
    application_id: UUID, db: Session = Depends(get_db)
) -> Response:
    service.delete_application(db, application_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
