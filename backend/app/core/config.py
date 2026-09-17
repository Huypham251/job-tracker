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

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()  # type: ignore[call-arg]
