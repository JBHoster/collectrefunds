"""Turning change-events into text messages.

The whole notification surface is now SMS. There is no email and no user account.
A subscriber is a phone number plus the same filter set the website exposes, and they
are only texted about programs that pass their own filters.

Three separate safety rails, because SMS mistakes are expensive and irreversible:
  1. `Delivery` has a unique constraint on (subscriber, event, channel) — re-running
     dispatch physically cannot send the same thing twice.
  2. A per-subscriber daily cap.
  3. Quiet hours — nothing goes out overnight.
"""
import logging
from datetime import date, datetime

from sqlalchemy.exc import IntegrityError

from .config import settings
from .models import Delivery, Event, Program, Subscriber
from .sms import alert_message, send_sms

log = logging.getLogger("claimwatch.notify")

# Only these events are worth a text. Payout revisions and URL additions are
# visible on the site but don't justify interrupting someone's day.
# Events worth a text. status_changed is included so followers of an upcoming
# program get told the moment it opens for claims.
NOTIFY_KINDS = {"new_program", "deadline_soon", "status_changed"}


def _is_now_open(program: Program, event: Event) -> bool:
    """True when this event represents an upcoming program becoming claimable."""
    return (event.kind == "status_changed" and program.status == "open")


def passes_filters(sub: Subscriber, program: Program) -> bool:
    """Same logic the website's filter rail uses, applied server-side."""
    cats = [c.strip() for c in (sub.categories or "").split(",") if c.strip()]
    if cats and program.category not in cats:
        return False

    est = program.payout_high or program.payout_low
    if sub.min_payout:
        if est is None or est < sub.min_payout:
            return False

    if sub.claim_required_only:
        note = (program.payout_note or "").lower()
        if "automatic" in note:
            return False

    return True


def in_quiet_hours(now: datetime | None = None) -> bool:
    """No texts overnight. Hours are UTC; defaults cover ~9am-9pm US Eastern."""
    now = now or datetime.utcnow()
    start, end = settings.send_window_start_utc, settings.send_window_end_utc
    h = now.hour
    if start <= end:
        return not (start <= h < end)
    return not (h >= start or h < end)   # window wraps midnight


def _under_daily_cap(sub: Subscriber) -> bool:
    today = date.today()
    if sub.sends_day != today:
        sub.sends_day, sub.sends_today = today, 0
    # Pro members get a higher ceiling so a busy day of new settlements never
    # silently drops an alert they paid to receive.
    cap = settings.max_sms_per_day * 3 if getattr(sub, "is_pro", False) else settings.max_sms_per_day
    return (sub.sends_today or 0) < cap


def _claim(session, subscriber_id: int, event_id: int) -> bool:
    """Reserve the send. False means it already went out.

    The insert runs in a SAVEPOINT: a plain rollback here would discard the whole
    batch, including every delivery already written and every notified flag, causing
    the next run to re-text everyone.
    """
    try:
        with session.begin_nested():
            session.add(Delivery(subscriber_id=subscriber_id, event_id=event_id))
        return True
    except IntegrityError:
        return False


def dispatch(session, limit: int = 500, force: bool = False) -> int:
    """Send pending alerts. Returns messages sent."""
    if not force and in_quiet_hours():
        log.info("quiet hours — deferring")
        return 0

    events = (session.query(Event)
              .filter(Event.notified.is_(False), Event.kind.in_(NOTIFY_KINDS))
              .order_by(Event.created_at).limit(limit).all())
    if not events:
        return 0

    subs = (session.query(Subscriber)
            .filter(Subscriber.verified.is_(True), Subscriber.opted_out.is_(False))
            .all())

    sent = 0
    for event in events:
        program = session.get(Program, event.program_id)

        # Never text about something we're not confident enough to publish.
        if not program or not program.published or program.needs_review:
            event.notified = True
            continue

        now_open = _is_now_open(program, event)

        # A plain status change that isn't "now open" (e.g. open -> closed) is not
        # worth a text — skip it, but still mark handled.
        if event.kind == "status_changed" and not now_open:
            event.notified = True
            continue

        for sub in subs:
            follows = [s for s in (sub.follows or "").split(",") if s]
            is_follower = program.slug in follows

            # Followers of a now-open program are always texted, regardless of
            # their filters — they explicitly asked about this one. Everyone else
            # goes through the normal filter gate.
            if now_open:
                if not is_follower:
                    continue
            else:
                if not passes_filters(sub, program):
                    continue

            if not _under_daily_cap(sub):
                continue
            if not _claim(session, sub.id, event.id):
                continue

            kind = "opened" if now_open else event.kind
            ok, err = send_sms(sub.phone, alert_message(program, kind))
            delivery = (session.query(Delivery)
                        .filter_by(subscriber_id=sub.id, event_id=event.id).first())
            if delivery:
                delivery.ok, delivery.error = ok, err
            if ok:
                sub.sends_today = (sub.sends_today or 0) + 1
                # Once told it opened, drop it from their follow list.
                if now_open and is_follower:
                    sub.follows = ",".join(s for s in follows if s != program.slug)
                sent += 1
            else:
                log.warning("sms failed: %s", err)

        event.notified = True

    return sent
