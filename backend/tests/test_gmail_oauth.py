def test_google_gmail_client_is_registered_with_readonly_scope() -> None:
    from app.gmail.oauth import oauth

    client = oauth.google_gmail
    assert client.client_kwargs["scope"] == "https://www.googleapis.com/auth/gmail.readonly"
    assert client.authorize_params == {"access_type": "offline", "prompt": "consent"}


def test_google_gmail_client_is_distinct_from_the_login_client() -> None:
    from app.auth.oauth import oauth as login_oauth
    from app.gmail.oauth import oauth as gmail_oauth

    assert login_oauth is gmail_oauth  # same registry
    assert login_oauth.google is not gmail_oauth.google_gmail  # different clients
