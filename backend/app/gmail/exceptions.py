from uuid import UUID


class GmailNotConnected(Exception):
    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id
        super().__init__(f"No Gmail connection for user {user_id}")
