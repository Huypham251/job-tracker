from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.auth.dependencies import COOKIE_NAME
from app.auth.jwt import create_access_token
from app.core.config import settings
from app.db.base import Base
from app.main import app
from app.users.models import User

# Models must be imported so their tables are registered on Base.metadata.
from app.applications import models  # noqa: F401

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"

_dev_url = make_url(settings.database_url)
TEST_DB_NAME = f"{_dev_url.database}_test"
TEST_DATABASE_URL = str(_dev_url.set(database=TEST_DB_NAME))
ADMIN_URL = str(_dev_url.set(database="postgres", drivername="postgresql"))


def _ensure_test_database() -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,)
        ).fetchone()
        if row is None:
            conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(scope="session")
def engine():
    _ensure_test_database()
    eng = create_engine(TEST_DATABASE_URL, future=True)
    # Guarantee a clean slate regardless of what a previous run left behind,
    # then run the REAL migrations (not Base.metadata.create_all) so
    # migration/model drift is caught by the test suite, not just by eye.
    Base.metadata.drop_all(eng)
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    command.upgrade(_alembic_config(TEST_DATABASE_URL), "head")
    yield eng
    eng.dispose()


@pytest.fixture
def db_session(engine):
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db_session) -> Iterator[TestClient]:
    from app.db.session import get_db

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _make_user(db_session: Session, *, google_sub: str, email: str, name: str) -> User:
    user = User(google_sub=google_sub, email=email, name=name)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _authenticated_client(db_session: Session, user: User) -> Iterator[TestClient]:
    from app.db.session import get_db

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    test_client.cookies.set(COOKIE_NAME, create_access_token(user.id))
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def user(db_session: Session) -> User:
    return _make_user(
        db_session, google_sub="google-sub-1", email="alice@example.com", name="Alice"
    )


@pytest.fixture
def auth_client(db_session: Session, user: User) -> Iterator[TestClient]:
    yield from _authenticated_client(db_session, user)


@pytest.fixture
def other_user(db_session: Session) -> User:
    return _make_user(
        db_session, google_sub="google-sub-2", email="bob@example.com", name="Bob"
    )


@pytest.fixture
def other_auth_client(db_session: Session, other_user: User) -> Iterator[TestClient]:
    yield from _authenticated_client(db_session, other_user)
