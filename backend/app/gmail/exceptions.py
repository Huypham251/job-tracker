from uuid import UUID

# Phase 10: returned to the browser (HTTP 403 body and SyncJob.error_code /
# error_message) when the user's Gmail grant is no longer usable.
REAUTH_CODE = "gmail_reauth_required"
REAUTH_MESSAGE = "Gmail access has expired or was revoked. Reconnect Gmail to keep syncing."


class GmailNotConnected(Exception):
    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id
        super().__init__(f"No Gmail connection for user {user_id}")


class GmailReauthRequired(Exception):
    """The connection exists but its grant is expired, revoked or unreadable —
    nothing but a reconnect can fix it, so refuse work instead of queueing a
    doomed sync."""

    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id
        super().__init__(f"Gmail reconnect required for user {user_id}")
