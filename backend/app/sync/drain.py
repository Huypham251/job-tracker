import argparse
import logging
import os

from app.sync.worker import LANES, DrainResult, drain_once, lane_slice_seconds

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> DrainResult:
    parser = argparse.ArgumentParser(description="Drain queued Gmail sync jobs, then exit.")
    parser.add_argument("--lane", choices=LANES)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    # This runs in GitHub Actions on a public repo, so its logs are public:
    # httpx logs every request URL at INFO, and Gmail URLs contain message IDs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    if args.lane is None:
        result = drain_once()
    else:
        result = drain_once(
            max_runtime_seconds=lane_slice_seconds(args.lane), job_type=args.lane, sweep=True
        )
    logger.info(
        "Drained %d job(s)%s%s",
        result.processed,
        f" from the {args.lane} lane" if args.lane else "",
        "; a job was paused at its slice deadline and continues in a new run" if result.requeued else "",
    )
    # The lane workflow reads this to decide whether to dispatch itself again
    # (Phase 10); outside GitHub Actions there's no GITHUB_OUTPUT to write.
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as fh:
            fh.write(f"requeued={'true' if result.requeued else 'false'}\n")
    return result


if __name__ == "__main__":
    main()
