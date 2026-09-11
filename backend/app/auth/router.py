from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import COOKIE_NAME, get_current_user
from app.auth.jwt import create_access_token
from app.auth.oauth import oauth
from app.core.config import settings
from app.db.session import get_db
from app.users.models import User
from app.users.schemas import UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/google/login")
async def google_login(request: Request):
    redirect_uri = str(request.url_for("google_callback"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback", name="google_callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    token = await oauth.google.authorize_access_token(request)
    claims = token["userinfo"]

    user = db.scalars(
        select(User).where(User.google_sub == claims["sub"])
    ).one_or_none()
    if user is None:
        user = User(
            google_sub=claims["sub"],
            email=claims["email"],
            name=claims.get("name") or claims["email"],
            picture_url=claims.get("picture"),
        )
        db.add(user)
    else:
        user.email = claims["email"]
        user.name = claims.get("name") or claims["email"]
        user.picture_url = claims.get("picture")
    db.commit()
    db.refresh(user)

    access_token = create_access_token(user.id)
    response = RedirectResponse(url=settings.frontend_url)
    response.set_cookie(
        key=COOKIE_NAME,
        value=access_token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.access_token_expire_minutes * 60,
    )
    return response


@router.get("/me", response_model=UserRead)
def get_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout() -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(key=COOKIE_NAME, samesite="lax")
    return response
