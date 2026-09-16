from uuid import UUID


class ReviewItemNotFound(Exception):
    def __init__(self, item_id: UUID) -> None:
        self.item_id = item_id
        super().__init__(f"Review item {item_id} not found")
