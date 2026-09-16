from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class EmailExtraction(BaseModel):
    is_job_related: bool
    confidence: float = Field(ge=0.0, le=1.0)
    company: str | None = None
    position: str | None = None
    status: Literal["applied", "oa", "interview", "rejected", "offer", "other"] | None = None
    status_date: date | None = None
    reasoning: str | None = None
