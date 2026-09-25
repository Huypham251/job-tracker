from app.db.session import engine


def test_engine_has_pool_pre_ping_enabled() -> None:
    assert engine.pool._pre_ping is True


def test_app_engine_hides_sql_parameters_from_exception_text() -> None:
    # Worker tracebacks go to public GitHub Actions logs; SQLAlchemy otherwise
    # appends every bound value (e.g. raw Gmail message IDs) to the error.
    from app.db.session import engine

    assert engine.hide_parameters is True
