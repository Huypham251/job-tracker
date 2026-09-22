import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def test_drain_module_can_configure_orm_mappers_standalone() -> None:
    """Same class of bug test_sync_worker_entrypoint.py guards against for
    `python -m app.sync.worker`, for this new entrypoint: a genuinely fresh
    process importing only app.sync.drain must not crash configuring
    SQLAlchemy's mappers (GmailConnection.owner / Application.owner resolve
    "User" as a string relationship, lazily, the first time any mapper
    configures — this only works if app.users.models was already imported).
    app.sync.drain imports app.sync.worker, which already imports
    app.users.models.User for exactly this reason — this test proves that
    protection extends to the new entrypoint too, not just the old one.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.sync.drain; from sqlalchemy.orm import configure_mappers; configure_mappers()",
        ],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
