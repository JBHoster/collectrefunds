"""Data model.

Design notes:
- `Program` is the canonical record a user sees. One row per refund/settlement program.
- `source_key` is a stable natural key from the upstream source so re-scrapes update
  rather than duplicate. For FTC this is the detail-page URL slug.
- `content_hash` powers change detection: if the hash moves, something upstream changed
  and we emit an Event. That is what makes "live updates" work without polling diffs by hand.
- `Event` is an append-only log. Notifications are generated from events, never from
  scrape output directly, so a re-scrape can never double-notify.
"""
from datetime import datetime, date

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Program(Base):
    __tablename__ = "programs"

    id = Column(Integer, primary_key=True)

    # --- identity ---
    source = Column(String(32), nullable=False, index=True)      # "ftc", "cfpb", "admin:epiq"
    source_key = Column(String(255), nullable=False)             # stable per-source id
    __table_args__ = (UniqueConstraint("source", "source_key", name="uq_source_key"),)

    # --- what the user sees ---
    name = Column(String(300), nullable=False)
    slug = Column(String(300), nullable=False, index=True)
    company = Column(String(200))                # normalised defendant/brand
    summary = Column(Text)                       # our own words, never copied upstream
    category = Column(String(64), index=True)    # data_breach, subscriptions, auto, edu, ...

    # --- the money ---
    payout_low = Column(Float)                   # per-claimant estimate, USD
    payout_high = Column(Float)
    total_fund = Column(Float)                   # total pot, USD
    payout_note = Column(String(300))            # e.g. "amount depends on claim volume"

    # --- how to act ---
    claim_url = Column(String(600))              # OFFICIAL claim site. Never our own form.
    source_url = Column(String(600), nullable=False)
    administrator = Column(String(160))          # Epiq, JND, Rust, Simpluris, ...
    phone = Column(String(80))
    proof_required = Column(Boolean, default=False)
    eligibility = Column(Text)                   # criteria, paraphrased

    # --- timing ---
    announced_on = Column(Date, index=True)
    claim_deadline = Column(Date, index=True)
    # For upcoming (not-yet-open) programs: a human phrase like "Expected spring 2026".
    # We rarely get an exact open date, so this is free text shown on the card.
    expected_open = Column(String(120))
    status = Column(String(32), default="open", index=True)
    # open | claims_closed | paying | closed | unknown

    # --- pipeline bookkeeping ---
    content_hash = Column(String(64), index=True)
    confidence = Column(Float, default=1.0)      # <0.6 routes to review queue
    needs_review = Column(Boolean, default=False, index=True)
    published = Column(Boolean, default=True, index=True)
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    events = relationship("Event", back_populates="program", cascade="all, delete-orphan")

    @property
    def days_left(self):
        if not self.claim_deadline:
            return None
        return (self.claim_deadline - date.today()).days


class Event(Base):
    """Append-only change log. Drives notifications and the activity feed."""
    __tablename__ = "events"

    id = Column(Integer, primary_key=True)
    program_id = Column(Integer, ForeignKey("programs.id"), index=True)
    kind = Column(String(40), nullable=False, index=True)
    # new_program | deadline_set | deadline_changed | payout_changed
    # | status_changed | claim_url_added | deadline_soon
    detail = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    notified = Column(Boolean, default=False, index=True)

    program = relationship("Program", back_populates="events")


class Subscriber(Base):
    """An SMS subscriber. No login, no email, no password — a phone number and filters.

    `verified` only becomes True after the person replies with the code we texted them.
    Under the TCPA that confirmed opt-in is not optional, and neither is honouring STOP.
    """
    __tablename__ = "subscribers"

    id = Column(Integer, primary_key=True)
    phone = Column(String(20), unique=True, nullable=False, index=True)  # E.164
    verified = Column(Boolean, default=False, index=True)
    confirm_code = Column(String(8))
    confirm_sent_at = Column(DateTime)
    confirm_attempts = Column(Integer, default=0)

    # Filters — the same ones the website exposes. A subscriber is only texted
    # about programs that pass their own filter set.
    categories = Column(Text, default="")      # comma-separated; blank = all
    min_payout = Column(Float, default=0.0)
    claim_required_only = Column(Boolean, default=False)  # skip automatic payouts

    # Specific upcoming programs this person asked to be told about the moment
    # they open for claims. Comma-separated slugs. This is the subscription funnel:
    # "notify me when THIS opens" is a far stronger reason to opt in than a generic
    # alert, and these people are always texted regardless of their filters.
    follows = Column(Text, default="")

    # Opt-out state. Rows are kept (not deleted) after STOP so we can prove we
    # stopped, and so a re-subscribe doesn't silently resurrect an old opt-out.
    opted_out = Column(Boolean, default=False, index=True)
    opted_out_at = Column(DateTime)

    consent_ip = Column(String(64))            # evidence of consent
    consent_at = Column(DateTime)
    sends_today = Column(Integer, default=0)   # frequency cap
    sends_day = Column(Date)

    # Pro subscription (paid). Free subscribers still get alerts, just throttled;
    # Pro gets instant delivery and unlimited follows. Set by the Stripe webhook.
    is_pro = Column(Boolean, default=False, index=True)
    stripe_customer_id = Column(String(64))
    stripe_subscription_id = Column(String(64))
    pro_since = Column(DateTime)
    pro_until = Column(DateTime)               # set when a subscription is cancelled

    created_at = Column(DateTime, default=datetime.utcnow)


class Delivery(Base):
    """One row per (subscriber, event) text. The unique constraint is what makes
    re-running dispatch safe: a duplicate insert fails instead of texting twice."""
    __tablename__ = "deliveries"

    id = Column(Integer, primary_key=True)
    subscriber_id = Column(Integer, ForeignKey("subscribers.id"), index=True)
    event_id = Column(Integer, ForeignKey("events.id"), index=True)
    channel = Column(String(16), default="sms")
    sent_at = Column(DateTime, default=datetime.utcnow)
    ok = Column(Boolean, default=True)
    error = Column(String(300))
    __table_args__ = (UniqueConstraint("subscriber_id", "event_id", "channel",
                                       name="uq_delivery"),)


class ScrapeRun(Base):
    """Observability: if ingest silently breaks, you want to see it on the dashboard."""
    __tablename__ = "scrape_runs"

    id = Column(Integer, primary_key=True)
    source = Column(String(32), index=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime)
    found = Column(Integer, default=0)
    created = Column(Integer, default=0)
    updated = Column(Integer, default=0)
    ok = Column(Boolean, default=True)
    error = Column(Text)
