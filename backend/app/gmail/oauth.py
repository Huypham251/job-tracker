from app.auth.oauth import oauth
from app.core.config import settings

# Registered on the SAME OAuth() registry as the login client ("google") —
# Authlib supports multiple named clients on one registry. Kept as a
# separate client (not a second scope on "google") so logging in can never
# implicitly grant Gmail access: this consent is only ever requested by the
# explicit "Connect Gmail" action in app/gmail/router.py.
oauth.register(
    name="google_gmail",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
        "code_challenge_method": "S256",
    },
    # access_type=offline requests a refresh_token; prompt=consent forces
    # Google to issue one on every connect (not just the very first time).
    authorize_params={"access_type": "offline", "prompt": "consent"},
)
