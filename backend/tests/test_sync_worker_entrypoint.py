import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def test_worker_module_can_configure_orm_mappers_standalone() -> None:
    """Reproduces a real startup crash: `python -m app.sync.worker` failed on
    its first database query with
    `sqlalchemy.exc.InvalidRequestError: ... expression 'User' failed to
    locate a name ('User')`.

    GmailConnection.owner and Application.owner both declare their
    relationship to User as the string "User" — SQLAlchemy resolves that
    lazily, the first time any mapper is configured, by looking up the name
    in its class registry. That only works if app.users.models has actually
    been imported by then. app/gmail/models.py and app/applications/models.py
    both import User only under `if TYPE_CHECKING:` (a no-op at runtime), so
    nothing in worker.py's own import graph ever imports it.

    This never surfaces via the FastAPI app (main.py imports auth_router,
    which imports app.users.models early) or via pytest (conftest.py
    explicitly imports every model module before any test runs) — only a
    genuinely fresh process that imports *just* app.sync.worker reproduces
    it, which is why a subprocess is used here instead of an in-process
    import.

    configure_mappers() forces the exact registry-resolution pass that
    crashed, without needing a real database connection — the failure in the
    traceback above happens before any SQL is ever sent.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.sync.worker; from sqlalchemy.orm import configure_mappers; configure_mappers()",
        ],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
