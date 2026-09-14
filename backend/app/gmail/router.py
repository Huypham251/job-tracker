from authlib.integrations.base_client import OAuthError
from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.gmail import service
from app.gmail.oauth import oauth
from app.gmail.schemas import GmailMessageSummary, GmailStatus
from app.users.models import User

router = APIRouter(prefix="/gmail", tags=["gmail"])


@router.get("/connect")
async def gmail_connect(request: Request, current_user: User = Depends(get_current_user)):
    redirect_uri = str(request.url_for("gmail_callback"))
    return await oauth.google_gmail.authorize_redirect(request, redirect_uri)


@router.get("/callback", name="gmail_callback")
async def gmail_callback(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        token = await oauth.google_gmail.authorize_access_token(request)
    except OAuthError:
        # Consent declined, expired/replayed state, or a token-exchange
        # failure — send the user back to the frontend instead of a 500.
        return RedirectResponse(url=settings.frontend_url)

    service.connect(db, current_user.id, token)
    return RedirectResponse(url=settings.frontend_url)


@router.get("/status", response_model=GmailStatus)
def gmail_status(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> GmailStatus:
    connection = service.get_connection(db, current_user.id)
    if connection is None:
        return GmailStatus(connected=False, email=None, connected_at=None)
    return GmailStatus(
        connected=True, email=connection.google_email, connected_at=connection.created_at
    )


@router.post("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
def gmail_disconnect(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> None:
    service.disconnect(db, current_user.id)


@router.get("/messages", response_model=list[GmailMessageSummary])
def gmail_messages(
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    return service.list_recent_messages(db, current_user.id, limit)
