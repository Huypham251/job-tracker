from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.applications.models import ApplicationStatus


def _strip(value: str | None) -> str | None:
    return value.strip() if isinstance(value, str) else value


class ApplicationCreate(BaseModel):
    company: str = Field(min_length=1, max_length=255)
    position: str = Field(min_length=1, max_length=255)
    status: ApplicationStatus = ApplicationStatus.applied
    applied_at: date | None = None

    @field_validator("company", "position", mode="before")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        return _strip(value)


class ApplicationUpdate(BaseModel):
    company: str | None = Field(default=None, min_length=1, max_length=255)
    position: str | None = Field(default=None, min_length=1, max_length=255)
    status: ApplicationStatus | None = None
    applied_at: date | None = None

    @field_validator("company", "position", mode="before")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        return _strip(value)

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> "ApplicationUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        return self


class ApplicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company: str
    position: str
    status: ApplicationStatus
    applied_at: date | None
    source: Literal["manual", "gmail"]
    created_at: datetime
    updated_at: datetime
