import argparse
import logging

from app.sync.worker import LANE_DRAIN_BUDGET_SECONDS, drain_once

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drain queued Gmail sync jobs, then exit.")
    parser.add_argument("--lane", choices=sorted(LANE_DRAIN_BUDGET_SECONDS))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    # This runs in GitHub Actions on a public repo, so its logs are public:
    # httpx logs every request URL at INFO, and Gmail URLs contain message IDs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    if args.lane is None:
        count = drain_once()
    else:
        count = drain_once(max_runtime_seconds=LANE_DRAIN_BUDGET_SECONDS[args.lane], job_type=args.lane)
    logger.info("Drained %d job(s)%s", count, f" from the {args.lane} lane" if args.lane else "")
    return count


if __name__ == "__main__":
    main()
