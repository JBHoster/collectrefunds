"""Ingest orchestrator.

    python -m app.ingest.run                 # full pass, all sources
    python -m app.ingest.run --limit 10      # cheap smoke test
    python -m app.ingest.run --fixture tests/fixtures/ftc_listing.html
    python -m app.ingest.run --no-notify

Run it on a cron/scheduler every few hours. That is the entire operational burden.
"""
import argparse
import traceback

from ..db import init_db, session_scope
from ..notify import dispatch
from . import ftc
from .base import record_run, sweep_deadlines, upsert

SOURCES = {"ftc": ftc}


def run(limit=None, fixture=None, notify=True, only=None):
    init_db()
    totals = {"found": 0, "created": 0, "updated": 0}

    for name, module in SOURCES.items():
        if only and name != only:
            continue
        with session_scope() as s:
            try:
                records = module.fetch(limit=limit, fixture=fixture)
                created, updated = upsert(s, module.SOURCE, records)
                record_run(s, name, len(records), created, updated)
                totals["found"] += len(records)
                totals["created"] += created
                totals["updated"] += updated
                print(f"[{name}] {len(records)} found, {created} new, {updated} updated")
            except Exception:
                record_run(s, name, 0, 0, 0, error=traceback.format_exc()[-2000:])
                print(f"[{name}] FAILED\n{traceback.format_exc()}")

    with session_scope() as s:
        n = sweep_deadlines(s)
        print(f"[deadlines] {n} events")

    if notify:
        with session_scope() as s:
            print(f"[notify] {dispatch(s)} texts sent")

    return totals


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int)
    p.add_argument("--fixture")
    p.add_argument("--only")
    p.add_argument("--no-notify", action="store_true")
    a = p.parse_args()
    run(limit=a.limit, fixture=a.fixture, notify=not a.no_notify, only=a.only)
