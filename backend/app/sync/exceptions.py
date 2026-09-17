from uuid import UUID

from app.sync.models import SyncJob


class SyncAlreadyRunning(Exception):
    def __init__(self, job: SyncJob) -> None:
        self.job = job
        super().__init__(f"A sync job is already active for user {job.user_id}")


class SyncJobNotFound(Exception):
    def __init__(self, job_id: UUID) -> None:
        self.job_id = job_id
        super().__init__(f"Sync job {job_id} not found")
