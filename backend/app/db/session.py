from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# pool_pre_ping tests each pooled connection with a lightweight ping before
# handing it out, transparently reconnecting if it's gone stale — needed
# because Neon (and other serverless Postgres providers) close idle
# connections server-side, which SQLAlchemy's pool otherwise doesn't detect
# until a query on that connection fails with a raw
# "SSL connection has been closed unexpectedly" OperationalError.
#
# hide_parameters (Phase 10): SQLAlchemy exception text otherwise lists every
# bound value, and worker tracebacks go to public GitHub Actions logs — the
# worker's per-page pre-check query binds raw Gmail message IDs.
def build_engine(url: str) -> Engine:
    return create_engine(url, future=True, pool_pre_ping=True, hide_parameters=True)


engine = build_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
