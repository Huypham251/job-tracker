from app.db.session import engine


def test_engine_has_pool_pre_ping_enabled() -> None:
    assert engine.pool._pre_ping is True
