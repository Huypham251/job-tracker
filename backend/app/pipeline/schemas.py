from datetime import date

from pydantic import BaseModel


class ProcessResult(BaseModel):
    processed: int
    auto_applied: int
    queued_for_review: int
    ignored: int
