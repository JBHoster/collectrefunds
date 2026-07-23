"""Run with: python -m pytest tests/ -q"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ingest import ftc
from app.ingest.base import sweep_deadlines, upsert
from app.models import Base, Event, Program, Subscriber

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def make_session():
    e = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e)
    return sessionmaker(bind=e)()


# ============================================================ scraping
def test_listing_parser():
    rows = ftc.parse_listing(open(f"{FIX}/ftc_listing.html").read())
    assert len(rows) == 4
    assert rows[0]["source_key"] == "amazon-refunds"
    assert rows[0]["company"] == "Amazon"
    assert rows[1]["administrator"] == "JND Legal Administration"
    assert rows[1]["phone"] == "1-866-848-0871"


def test_detail_parser():
    stub = ftc.parse_listing(open(f"{FIX}/ftc_listing.html").read())[1]
    rec = ftc.parse_detail(open(f"{FIX}/ftc_detail.html").read(), stub)
    assert rec["claim_deadline"] == date(2026, 12, 15)
    assert rec["claim_url"] == "https://www.examplesettlement.com/claim"
    assert rec["total_fund"] == 1_500_000
    assert rec["payout_low"] == 37.5
    assert rec["proof_required"] is True
    assert rec["category"] == "subscriptions"
    assert "Federal Trade Commission is sending" not in rec["summary"]


# ============================================================ change detection
def test_upsert_is_idempotent_and_emits_change_events():
    s = make_session()
    rec = dict(source_key="x", name="Test Refunds", source_url="http://e/x",
               claim_deadline=date(2026, 6, 1), status="open")
    assert upsert(s, "ftc", [rec]) == (1, 0)
    assert upsert(s, "ftc", [rec]) == (0, 0)
    rec["claim_deadline"] = date(2026, 7, 1)
    assert upsert(s, "ftc", [rec]) == (0, 1)
    assert "deadline_changed" in [e.kind for e in s.query(Event).all()]
    assert s.query(Program).count() == 1


def test_deadline_sweep_closes_expired():
    s = make_session()
    upsert(s, "ftc", [dict(source_key="y", name="Old", source_url="http://e/y",
                           claim_deadline=date.today() - timedelta(days=1),
                           status="open")])
    sweep_deadlines(s)
    assert s.query(Program).one().status == "claims_closed"


# ============================================================ phone handling
@pytest.mark.parametrize("raw,expected", [
    ("5551234567", "+15551234567"),
    ("(555) 123-4567", "+15551234567"),
    ("555-123-4567", "+15551234567"),
    ("+15551234567", "+15551234567"),
    ("15551234567", "+15551234567"),
    ("+442071838750", "+442071838750"),
])
def test_phone_normalisation_accepts_real_numbers(raw, expected):
    from app.sms import normalize_phone
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "123", "0551234567", "+1", "55512345678901234"])
def test_phone_normalisation_rejects_junk(raw):
    """A bad number means texting a stranger who never consented. Reject, don't guess."""
    from app.sms import normalize_phone
    assert normalize_phone(raw) is None


# ============================================================ filters
def _prog(s, **kw):
    base = dict(source_key="p", name="Test", company="Test", source_url="http://e/p",
                status="open")
    base.update(kw)
    upsert(s, "ftc", [base])
    return s.query(Program).filter_by(source_key=base["source_key"]).one()


def test_subscriber_filters_gate_by_payout_category_and_claim_type():
    from app.notify import passes_filters
    s = make_session()
    p = _prog(s, payout_high=30.0, category="fintech", payout_note="Claim form required")

    assert passes_filters(Subscriber(min_payout=0, categories="", claim_required_only=False), p)
    assert not passes_filters(Subscriber(min_payout=50, categories="", claim_required_only=False), p)
    assert passes_filters(Subscriber(min_payout=25, categories="fintech", claim_required_only=False), p)
    assert not passes_filters(Subscriber(min_payout=0, categories="auto", claim_required_only=False), p)

    auto = _prog(s, source_key="p2", payout_note="Paid automatically — no claim needed")
    assert not passes_filters(Subscriber(min_payout=0, categories="", claim_required_only=True), auto)
    assert passes_filters(Subscriber(min_payout=0, categories="", claim_required_only=False), auto)


def test_unknown_payout_fails_a_payout_floor():
    """A program with no published amount must not slip past a '$50+' filter."""
    from app.notify import passes_filters
    s = make_session()
    p = _prog(s, payout_high=None, payout_low=None)
    assert not passes_filters(Subscriber(min_payout=50, categories="",
                                         claim_required_only=False), p)


# ============================================================ sending safety
def _sub(s, **kw):
    base = dict(phone="+15551234567", verified=True, opted_out=False,
                categories="", min_payout=0.0, claim_required_only=False,
                follows="", is_pro=False, sends_today=0, sends_day=date.today())
    base.update(kw)
    sub = Subscriber(**base)
    s.add(sub)
    s.commit()
    return sub


def test_dispatch_never_double_sends_after_replay():
    """The SAVEPOINT in _claim must contain a duplicate without discarding the batch.
    Regression: a plain rollback() wiped every Delivery and notified flag from the
    run, so the next pass re-texted everyone."""
    from app.models import Delivery
    from app.notify import dispatch
    s = make_session()
    _prog(s)
    _sub(s, is_pro=True)   # only Pro gets instant per-event texts

    first = dispatch(s, force=True)
    s.commit()
    assert first == 1
    assert s.query(Delivery).count() == 1

    for e in s.query(Event).all():
        e.notified = False
    s.commit()

    assert dispatch(s, force=True) == 0
    assert s.query(Delivery).count() == 1


def test_unconfirmed_numbers_are_never_texted():
    """Confirmed opt-in is worthless if dispatch ignores the flag."""
    from app.notify import dispatch
    s = make_session()
    _prog(s)
    _sub(s, verified=False)
    assert dispatch(s, force=True) == 0


def test_opted_out_numbers_are_never_texted():
    from app.notify import dispatch
    s = make_session()
    _prog(s)
    _sub(s, opted_out=True)
    assert dispatch(s, force=True) == 0


def test_daily_cap_is_enforced():
    from app.config import settings
    from app.notify import dispatch
    s = make_session()
    # Pro cap is 3x the base. Pre-load sends so only the last slot remains, and
    # verify dispatch stops at the ceiling rather than sending all events.
    pro_cap = settings.max_sms_per_day * 3
    for i in range(pro_cap + 2):
        _prog(s, source_key=f"cap{i}", name=f"Program {i}")
    _sub(s, is_pro=True)
    assert dispatch(s, force=True) == pro_cap


def test_quiet_hours_defer_rather_than_drop():
    """Messages held overnight must still be pending, not silently consumed."""
    from app.models import Delivery
    from app.notify import dispatch, in_quiet_hours
    from app.config import settings

    s = make_session()
    _prog(s)
    _sub(s, is_pro=True)   # only Pro gets instant per-event texts

    original = settings.send_window_start_utc, settings.send_window_end_utc
    try:
        # A window that cannot contain "now".
        h = datetime.utcnow().hour
        settings.send_window_start_utc = (h + 2) % 24
        settings.send_window_end_utc = (h + 3) % 24
        assert in_quiet_hours() is True
        assert dispatch(s) == 0
        assert s.query(Delivery).count() == 0
        assert s.query(Event).filter(Event.notified.is_(False)).count() > 0
    finally:
        settings.send_window_start_utc, settings.send_window_end_utc = original

    assert dispatch(s, force=True) == 1


def test_needs_review_programs_are_hidden_and_silent():
    from app.notify import dispatch
    s = make_session()
    upsert(s, "ftc", [dict(source_key="lc", name="Sketchy", company="Sketchy",
                           source_url="http://e/lc", status="open", confidence=0.2)])
    assert s.query(Program).one().needs_review is True
    _sub(s)
    assert dispatch(s, force=True) == 0


@pytest.mark.parametrize("name", ["Ring Refunds", "A" * 400, "X"])
def test_alert_message_always_keeps_link_and_stop_footer(name):
    """Regression: blunt truncation of the whole message dropped the STOP footer
    that carriers require, and the link the message exists to deliver."""
    from app.sms import alert_message
    s = make_session()
    p = _prog(s, name=name, payout_high=99999.0,
              claim_deadline=date.today() + timedelta(days=5))
    for kind in ("new_program", "deadline_soon"):
        msg = alert_message(p, kind)
        assert len(msg) <= 320, f"{len(msg)} chars"
        assert "STOP" in msg
        assert f"/programs/{p.slug}" in msg


def test_twilio_signature_validation_rejects_forgeries():
    """Without this, anyone can forge a STOP for someone else's number."""
    import base64
    import hashlib
    import hmac
    from app.config import settings
    from app.sms import validate_twilio_signature

    original = settings.twilio_auth_token
    settings.twilio_auth_token = "test-token"
    try:
        url = "https://example.com/sms/inbound"
        params = {"From": "+15551234567", "Body": "STOP"}
        data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
        good = base64.b64encode(hmac.new(b"test-token", data.encode(),
                                         hashlib.sha1).digest()).decode()
        assert validate_twilio_signature(url, params, good) is True
        assert validate_twilio_signature(url, params, "bogus") is False
        assert validate_twilio_signature(url, {"From": "+1999", "Body": "STOP"},
                                         good) is False
    finally:
        settings.twilio_auth_token = original


# ============================================================ failure loudness
def test_blocked_source_raises_instead_of_reporting_success():
    """Regression: a 403 from the source parsed as an empty page, recorded a
    successful run, and left /healthz green while the site served stale deadlines.
    A source failure must be loud."""
    import httpx
    from unittest.mock import patch

    class FakeResp:
        status_code = 403
        text = "Forbidden"

        def raise_for_status(self):
            raise httpx.HTTPStatusError("403", request=None, response=None)

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url): return FakeResp()

    with patch.object(ftc, "_client", lambda: FakeClient()):
        with pytest.raises(Exception):
            ftc.fetch()


def test_empty_listing_is_treated_as_failure():
    """An empty FTC list means the layout changed or we're blocked — never that
    every refund program in America closed at once."""
    from unittest.mock import patch

    class FakeResp:
        status_code = 200
        text = "<html><body><p>nothing here</p></body></html>"

        def raise_for_status(self): pass

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url): return FakeResp()

    with patch.object(ftc, "_client", lambda: FakeClient()):
        with pytest.raises(RuntimeError, match="Parsed 0 programs"):
            ftc.fetch()


# ============================================================ upcoming funnel
def test_follower_is_notified_when_upcoming_program_opens():
    """The subscription funnel: someone follows an upcoming settlement, and is
    texted the moment it flips to open — bypassing their normal filters."""
    from app.notify import dispatch
    s = make_session()
    # an upcoming program
    upsert(s, "ftc", [dict(source_key="up1", name="Upcoming Settlement", company="X",
                           source_url="http://e/up1", status="upcoming")])
    prog = s.query(Program).filter_by(source_key="up1").one()
    # a follower whose filters would NOT match (high floor), following this slug
    _sub(s, phone="+15550000001", min_payout=9999.0, follows=prog.slug)
    for e in s.query(Event).all():
        e.notified = True   # clear the new_program backlog

    # flip to open
    upsert(s, "ftc", [dict(source_key="up1", name="Upcoming Settlement", company="X",
                           source_url="http://e/up1", status="open",
                           payout_low=20.0)])
    assert dispatch(s, force=True) == 1        # texted despite the 9999 floor
    sub = s.query(Subscriber).filter_by(phone="+15550000001").one()
    assert sub.follows == ""                    # cleared, won't double-send


def test_non_followers_not_texted_on_open_unless_filters_match():
    """A status change to open must not blast everyone — only followers get the
    special treatment; others go through normal filters (and a bare open with no
    new_program event shouldn't reach a non-follower)."""
    from app.notify import dispatch
    s = make_session()
    upsert(s, "ftc", [dict(source_key="up2", name="Another Upcoming", company="Y",
                           source_url="http://e/up2", status="upcoming")])
    _sub(s, phone="+15550000002", min_payout=0.0)   # not following anything
    for e in s.query(Event).all():
        e.notified = True

    upsert(s, "ftc", [dict(source_key="up2", name="Another Upcoming", company="Y",
                           source_url="http://e/up2", status="open", payout_low=20.0)])
    # only a status_changed event exists; non-follower should get nothing from it
    assert dispatch(s, force=True) == 0


def test_follow_list_merges_without_duplicates():
    from app.main import _merge_follow
    assert _merge_follow("", "a") == "a"
    assert _merge_follow("a", "b") == "a,b"
    assert _merge_follow("a,b", "a") == "a,b"      # no dupes
    assert _merge_follow("a,b", "") == "a,b"        # empty slug is a no-op


# ============================================================ payments
def test_stripe_webhook_rejects_forged_signatures():
    """Without signature verification, anyone could forge a 'paid' event and get
    Pro for free. This is the single most important payment safety check."""
    import json, time, hmac, hashlib
    from app.config import settings
    from app.payments import verify_webhook

    original = settings.stripe_webhook_secret
    settings.stripe_webhook_secret = "whsec_test"
    try:
        payload = json.dumps({"type": "checkout.session.completed"}).encode()
        ts = str(int(time.time()))
        good = hmac.new(b"whsec_test", f"{ts}.".encode() + payload,
                        hashlib.sha256).hexdigest()

        assert verify_webhook(payload, f"t={ts},v1={good}") is not None
        assert verify_webhook(payload, f"t={ts},v1=deadbeef") is None
        assert verify_webhook(payload, "") is None
        assert verify_webhook(payload, "garbage") is None
        # stale timestamp (replay) rejected
        old_ts = str(int(time.time()) - 9999)
        old_sig = hmac.new(b"whsec_test", f"{old_ts}.".encode() + payload,
                           hashlib.sha256).hexdigest()
        assert verify_webhook(payload, f"t={old_ts},v1={old_sig}") is None
    finally:
        settings.stripe_webhook_secret = original


def test_payments_disabled_without_keys():
    from app.config import settings
    from app.payments import is_enabled
    original = settings.stripe_secret_key
    settings.stripe_secret_key = ""
    try:
        assert is_enabled() is False
    finally:
        settings.stripe_secret_key = original


def test_pro_members_get_a_higher_daily_cap():
    """Pro's functional benefit: a busy day never drops an alert they paid for."""
    from app.config import settings
    from app.notify import dispatch
    s = make_session()
    for i in range(settings.max_sms_per_day + 2):
        _prog(s, source_key=f"pc{i}", name=f"Program {i}")
    _sub(s, is_pro=True)
    # a Pro member should receive more than the free cap in one run
    assert dispatch(s, force=True) > settings.max_sms_per_day


# ============================================================ free vs pro model
def test_free_members_do_not_get_instant_texts():
    """Free members get a weekly digest, NOT instant per-event alerts. This is the
    core of the paid/free distinction, so it's worth pinning down."""
    from app.notify import dispatch
    s = make_session()
    _prog(s, source_key="fx1", name="Some Refund", payout_low=50.0)
    _sub(s, is_pro=False)               # free
    assert dispatch(s, force=True) == 0  # nothing instant for free


def test_pro_members_get_instant_texts():
    from app.notify import dispatch
    s = make_session()
    _prog(s, source_key="px1", name="Some Refund", payout_low=50.0)
    _sub(s, is_pro=True)
    assert dispatch(s, force=True) == 1


def test_free_follower_still_told_when_followed_program_opens():
    """Even a free member is texted instantly for a program they explicitly followed."""
    from app.notify import dispatch
    s = make_session()
    upsert(s, "ftc", [dict(source_key="fo1", name="Upcoming One", company="Z",
                           source_url="http://e/fo1", status="upcoming")])
    prog = s.query(Program).filter_by(source_key="fo1").one()
    _sub(s, is_pro=False, follows=prog.slug)
    for e in s.query(Event).all():
        e.notified = True
    upsert(s, "ftc", [dict(source_key="fo1", name="Upcoming One", company="Z",
                           source_url="http://e/fo1", status="open", payout_low=20.0)])
    assert dispatch(s, force=True) == 1   # followed → instant, even on free


def test_weekly_digest_targets_free_members_only():
    from app.notify import send_weekly_digest
    s = make_session()
    _prog(s, source_key="wd1", name="Weekly One", payout_low=40.0)
    _sub(s, phone="+15551110001", is_pro=False)
    _sub(s, phone="+15551110002", is_pro=True)
    sent = send_weekly_digest(s, force=True)
    assert sent == 1     # only the free member gets the digest


# ============================================================ claim-url safety
def test_claim_url_only_trusts_official_domains():
    """Scam copycat sites appear around every settlement. The claim button must
    NEVER point at an unverified administrator domain — only .gov (or a safe
    fallback), routing everything else through the official FTC page."""
    from app.main import safe_claim_url

    class P:
        def __init__(self, claim, source):
            self.claim_url, self.source_url = claim, source

    # a real .gov claim url is used directly
    url, direct = safe_claim_url(P("https://www.ftc.gov/fortnite", "https://www.ftc.gov/x"))
    assert url == "https://www.ftc.gov/fortnite" and direct is True

    # an untrusted domain is rejected and falls back to the .gov source
    url, direct = safe_claim_url(P("https://scam-refunds.com", "https://www.ftc.gov/x"))
    assert "scam" not in url and url == "https://www.ftc.gov/x" and direct is False

    # no claim url at all → the source page
    url, direct = safe_claim_url(P(None, "https://www.ftc.gov/enforcement/refunds/y"))
    assert url.endswith("/y") and direct is False

    # look-alike domain trick (ftc.gov.evil.com) must NOT be trusted
    url, direct = safe_claim_url(P("https://ftc.gov.evil.com/claim", "https://www.ftc.gov/z"))
    assert "evil" not in url and direct is False


# ============================================================ closed-claim guard
def test_expired_program_is_never_claimable():
    """A past claim deadline means closed — even if the stored status still says
    'open' (e.g. a scrape lagged or seed data is stale). This guard is what prevents
    ever showing a 'claim now' button for a window that has closed."""
    from datetime import date, timedelta
    from app.main import is_claimable

    class P:
        def __init__(self, status, deadline):
            self.status = status
            self.claim_deadline = deadline
        @property
        def days_left(self):
            if self.claim_deadline is None:
                return None
            return (self.claim_deadline - date.today()).days

    today = date.today()
    # open + future deadline → claimable
    assert is_claimable(P("open", today + timedelta(days=30))) is True
    # open + PAST deadline → NOT claimable (the Fortnite bug)
    assert is_claimable(P("open", today - timedelta(days=1))) is False
    # explicitly closed → not claimable
    assert is_claimable(P("claims_closed", today + timedelta(days=30))) is False
    # open + no deadline → claimable (automatic-payout programs)
    assert is_claimable(P("open", None)) is True
