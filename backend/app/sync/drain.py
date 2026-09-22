import logging

from app.sync.worker import drain_once

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = drain_once()
    logging.getLogger(__name__).info("Drained %d job(s)", count)
