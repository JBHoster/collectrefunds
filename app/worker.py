"""Standalone worker process.

    python -m app.worker

Owns ingest and digest scheduling in production. Runs one ingest pass immediately on
boot so a fresh deployment has data rather than an empty homepage.
"""
import logging
import signal
import sys

from .config import settings
from .db import init_db
from .scheduler import build_scheduler, ingest_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("claimwatch.worker")


def main():
    init_db()
    log.info("worker starting; ingest every %s min", settings.ingest_interval_minutes)

    log.info("running initial ingest pass")
    ingest_job()

    sched = build_scheduler(blocking=True)

    def shutdown(signum, _frame):
        log.info("signal %s received, shutting down", signum)
        sched.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    sched.start()


if __name__ == "__main__":
    main()
