from datetime import date

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    cors_origins: str = "http://localhost:5173"
    env: str = "development"

    google_client_id: str
    google_client_secret: str
    secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 43200
    frontend_url: str = "http://localhost:5173"
    cookie_secure: bool = False
    gmail_token_encryption_key: str
    # Calibrated 2026-09-15 (Task 8) against evaluation/dataset.jsonl's actual
    # RuleBasedExtractor confidence scores. This field is read by
    # app/pipeline/service.py as `confidence >= threshold` to decide auto-apply
    # vs. review queue.
    #
    # The one wrong extraction in the dataset (a company-name miss) scores
    # 0.65. The cluster of fully-correct, high-signal extractions scores 0.825
    # (one example, weaker status-margin) and 0.9 (six examples, template
    # extraction on both fields with a clear status-score margin). 0.85 sits
    # in the gap strictly above the known-wrong 0.65 and, per this plan's
    # explicit preference for erring toward a higher bar (a wrong auto-created
    # row is worse than one extra manual review), also above the single 0.825
    # correct-but-weaker-margin example — so that example routes to review
    # too rather than riding in on the wrong example's coattails. Keeping this
    # value here (unchanged from its prior, un-evidenced default) is itself a
    # decision made from the calibration data, not an oversight.
    classification_confidence_threshold: float = 0.85
    gmail_sync_backfill_days: int = 180
    # Healthy process_job() commits at least once per message and once per
    # page (worst-case gap between commits is one token-refresh call plus
    # one list-page call, each bounded by google_api._TIMEOUT=10s) — this
    # threshold has a wide safety margin over that, so a "running" job whose
    # updated_at hasn't moved in this long is treated as orphaned by a crash
    # (see worker.py's reap_stale_jobs), not as still legitimately working.
    sync_stale_job_threshold_minutes: int = Field(default=15, gt=0)
    # Phase 10 — each lane drain works a job for at most this long, then
    # checkpoints it at a page boundary and requeues it without using an
    # attempt; the lane workflow re-dispatches itself to continue. The lane
    # workflows set these explicitly (next to their timeout-minutes).
    sync_initial_slice_seconds: int = Field(default=1500, gt=0)
    sync_incremental_slice_seconds: int = Field(default=1200, gt=0)
    # Phase 10 — at drain start, a "running" job in the drain's own lane that
    # hasn't committed for this long was abandoned by a killed run (a healthy
    # job commits after every message). See worker.sweep_orphans.
    sync_orphan_threshold_seconds: int = Field(default=120, gt=0)
    # Production only (set via the Render Web Service's environment) — starts
    # sync/worker.py's run_forever() on a background thread at FastAPI startup
    # instead of requiring a separate `python -m app.sync.worker` process.
    # Defaults false so local dev is unaffected — see app/sync/inprocess.py.
    run_worker_in_process: bool = False
    # Phase 9 — when both token and repository are set, POST /gmail/sync asks
    # GitHub to start the matching lane's worker workflow right away
    # (workflow_dispatch) instead of waiting for its cron fallback. Production
    # only (Render env); unset locally, where `python -m app.sync.worker` runs
    # instead. The token is a fine-grained PAT limited to this repo with only
    # "Actions: write" — see README's "Production deployment".
    sync_dispatch_token: str | None = None
    sync_dispatch_repository: str | None = None
    sync_dispatch_ref: str = "main"
    # Phase 10 — the dispatch token's expiry date (not a secret), so the
    # monitor (app/sync/monitor.py) warns before it lapses. Set only in the
    # sync-monitor workflow.
    sync_dispatch_token_expires_on: date | None = None

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()  # type: ignore[call-arg]
