from datetime import datetime

from pydantic import BaseModel


class GmailStatus(BaseModel):
    connected: bool
    email: str | None
    connected_at: datetime | None


class GmailMessageSummary(BaseModel):
    id: str
    subject: str
    from_: str
    date: str
    snippet: str
