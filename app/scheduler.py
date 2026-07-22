"""Background scheduler.

In development the web process runs ingest on a timer for convenience. In production
set RUN_SCHEDULER_IN_WEB=false and run `python -m app.worker` in a separate container,
so the web process stays stateless and you can scale it without duplicating scrapes.
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .config import settings

log = logging.getLogger("claimwatch.scheduler")
_scheduler: BackgroundScheduler | None = None


def ingest_job():
    from .ingest.run import run
    try:
        run()
    except Exception:
        log.exception("ingest failed")


def notify_job():
    """Separate from ingest so a scraper failure can't block pending alerts, and
    so deferred messages (quiet hours) get another chance every 30 minutes."""
    from .db import session_scope
    from .notify import dispatch
    try:
        with session_scope() as s:
            n = dispatch(s)
        if n:
            log.info("sent %s alert texts", n)
    except Exception:
        log.exception("notify failed")


def build_scheduler(blocking: bool = False):
    from apscheduler.schedulers.blocking import BlockingScheduler
    sched = BlockingScheduler() if blocking else BackgroundScheduler(daemon=True)
    sched.add_job(ingest_job, "interval",
                  minutes=settings.ingest_interval_minutes,
                  id="ingest", max_instances=1, coalesce=True,
                  next_run_time=None)
    # Retry pending alerts every 30 min. dispatch() is a no-op during quiet
    # hours, so anything found overnight goes out when the window opens.
    sched.add_job(notify_job, "interval", minutes=30,
                  id="notify", max_instances=1, coalesce=True)
    return sched


def start_scheduler():
    global _scheduler
    if _scheduler:
        return _scheduler
    _scheduler = build_scheduler()
    _scheduler.start()
    log.info("scheduler started (ingest every %s min)", settings.ingest_interval_minutes)
    return _scheduler
