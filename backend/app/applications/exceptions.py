from uuid import UUID


class ApplicationNotFound(Exception):
    def __init__(self, application_id: UUID) -> None:
        self.application_id = application_id
        super().__init__(f"Application {application_id} not found")
