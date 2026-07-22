"""Shared ingest machinery.

Every source produces a list of plain dicts. `upsert` is the only thing that writes
Programs, so change-detection and event emission live in exactly one place. Adding a
new source (CFPB, a claims administrator, CourtListener) means writing a fetch function
that returns dicts in this shape -- nothing else changes.
"""
import hashlib
import re
from datetime import date, datetime

from ..models import Event, Program, ScrapeRun

# Fields that, when they change, are worth waking a user up for.
WATCHED = {
    "claim_deadline": "deadline_changed",
    "payout_low": "payout_changed",
    "payout_high": "payout_changed",
    "status": "status_changed",
    "claim_url": "claim_url_added",
}

HASH_FIELDS = [
    "name", "summary", "payout_low", "payout_high", "total_fund", "claim_url",
    "claim_deadline", "status", "eligibility", "administrator",
]


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:200] or "program"


def content_hash(d: dict) -> str:
    raw = "|".join(str(d.get(f, "")) for f in HASH_FIELDS)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _coerce(v):
    if isinstance(v, datetime):
        return v.date()
    return v


def upsert(session, source: str, records: list[dict]) -> tuple[int, int]:
    """Insert or update programs, emitting Events for anything that materially changed."""
    created = updated = 0

    for rec in records:
        rec = {k: _coerce(v) for k, v in rec.items()}
        rec.setdefault("slug", slugify(rec.get("name", "")))
        h = content_hash(rec)

        prog = (
            session.query(Program)
            .filter_by(source=source, source_key=rec["source_key"])
            .one_or_none()
        )

        if prog is None:
            prog = Program(source=source, content_hash=h, **rec)
            prog.needs_review = (rec.get("confidence", 1.0) or 1.0) < 0.6
            session.add(prog)
            session.flush()
            session.add(Event(
                program_id=prog.id,
                kind="new_program",
                detail=f"{prog.name} added from {source}",
            ))
            if prog.claim_deadline:
                session.add(Event(
                    program_id=prog.id,
                    kind="deadline_set",
                    detail=f"Claim deadline {prog.claim_deadline.isoformat()}",
                ))
            created += 1
            continue

        prog.last_seen_at = datetime.utcnow()

        if prog.content_hash == h:
            continue  # nothing moved

        for field, kind in WATCHED.items():
            old, new = getattr(prog, field, None), rec.get(field)
            if new is not None and old != new:
                session.add(Event(
                    program_id=prog.id,
                    kind=kind,
                    detail=f"{field}: {old or '—'} → {new}",
                ))

        for k, v in rec.items():
            if k in ("source_key",) or v is None:
                continue
            setattr(prog, k, v)
        prog.content_hash = h
        updated += 1

    return created, updated


def sweep_deadlines(session, warn_days=(30, 14, 7, 3, 1)) -> int:
    """Emit deadline_soon events and auto-close expired programs. Runs every cycle."""
    today = date.today()
    n = 0

    for prog in session.query(Program).filter(
        Program.claim_deadline.isnot(None), Program.status == "open"
    ):
        left = (prog.claim_deadline - today).days
        if left < 0:
            prog.status = "claims_closed"
            session.add(Event(program_id=prog.id, kind="status_changed",
                              detail="Claim window closed"))
            n += 1
            continue
        if left in warn_days:
            already = session.query(Event).filter(
                Event.program_id == prog.id,
                Event.kind == "deadline_soon",
                Event.detail.like(f"%{left} day%"),
            ).first()
            if not already:
                session.add(Event(program_id=prog.id, kind="deadline_soon",
                                  detail=f"{left} day(s) left to file a claim"))
                n += 1
    return n


def record_run(session, source, found, created, updated, error=None) -> ScrapeRun:
    run = ScrapeRun(
        source=source, finished_at=datetime.utcnow(), found=found,
        created=created, updated=updated, ok=error is None, error=error,
    )
    session.add(run)
    return run
