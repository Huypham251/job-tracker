import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
_TIMEOUT = 5.0


def workflow_file(job_type: str) -> str:
    return f"sync-{job_type}.yml"


def request_worker(job_type: str) -> bool:
    """Best-effort: ask GitHub Actions to start the worker lane for job_type.
    Never raises and never affects the caller — the job is already committed
    as "queued", so a failed dispatch only means it waits for the lane's cron
    fallback or the user's next Sync click. Logs the lane and the HTTP status
    or exception type, never the token, the response body or the exception
    text."""
    token = settings.sync_dispatch_token
    repository = settings.sync_dispatch_repository
    if not token or not repository:
        return False
    url = f"{GITHUB_API}/repos/{repository}/actions/workflows/{workflow_file(job_type)}/dispatches"
    try:
        response = httpx.post(
            url,
            json={"ref": settings.sync_dispatch_ref},
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=_TIMEOUT,
        )
    except Exception as exc:  # deliberately broad: a dispatch must never propagate
        logger.warning("Sync worker dispatch for the %s lane failed: %s", job_type, type(exc).__name__)
        return False
    # 200 with run details today; 204 with no body in older API behavior.
    if response.status_code in (200, 204):
        logger.info("Dispatched the %s sync worker", job_type)
        return True
    logger.warning("Sync worker dispatch for the %s lane was rejected: HTTP %s", job_type, response.status_code)
    return False
