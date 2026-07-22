"""Seed realistic demo data so you can see the UI before wiring live ingest."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date, timedelta
from app.db import init_db, session_scope
from app.ingest.base import upsert, sweep_deadlines
from app.models import Subscriber

T = date.today()
DEMO = [
  dict(source_key="amazon-refunds", name="Amazon Refunds", company="Amazon",
       category="subscriptions", summary="The FTC is issuing refunds to Amazon Prime members enrolled or kept in the subscription through misleading flows. A claim form is required for some groups.",
       payout_low=51.0, payout_high=51.0, total_fund=1.5e9, payout_note="Claim form required for some groups",
       claim_url="https://www.amazonprimesettlement.com", source_url="https://www.ftc.gov/enforcement/refunds/amazon-refunds",
       administrator="Epiq Systems", announced_on=T-timedelta(days=170), claim_deadline=T+timedelta(days=21),
       eligibility="Prime members enrolled between June 2019 and June 2025 who used the service minimally.",
       status="open", confidence=0.9),
  dict(source_key="credit-karma-settlement", name="Credit Karma Settlement", company="Credit Karma",
       category="fintech", summary="Refunds for people told they were pre-approved for credit offers and then denied.",
       payout_low=7.5, payout_high=7.5, total_fund=2.5e6, payout_note="Paid automatically — no claim needed",
       claim_url=None, source_url="https://www.ftc.gov/enforcement/refunds/credit-karma-settlement",
       administrator="JND Legal Administration", phone="1-866-848-0871",
       announced_on=T-timedelta(days=90), claim_deadline=None, status="open", confidence=0.8),
  dict(source_key="brigit-refunds", name="Brigit Refunds", company="Brigit",
       category="fintech", summary="Refunds for cash-advance app users charged recurring fees they could not cancel.",
       payout_low=22.0, payout_high=60.0, total_fund=1.8e7, payout_note="Claim form required",
       proof_required=False, claim_url="https://www.brigitrefunds.com",
       source_url="https://www.ftc.gov/enforcement/refunds/brigit-refunds",
       administrator="Rust Consulting, Inc.", phone="1-833-637-5800",
       announced_on=T-timedelta(days=60), claim_deadline=T+timedelta(days=95), status="open", confidence=0.95),
  dict(source_key="ring-refunds", name="Ring Refunds", company="Ring",
       category="data_breach", summary="Refunds over unauthorised employee and contractor access to customer video footage.",
       total_fund=5.6e6, payout_note="Paid automatically — no claim needed",
       claim_url=None, source_url="https://www.ftc.gov/enforcement/refunds/ring-refunds",
       administrator="Rust Consulting, Inc.", phone="1-833-637-4884",
       announced_on=T-timedelta(days=340), claim_deadline=T+timedelta(days=5), status="open", confidence=0.85),
  dict(source_key="fortnite-refunds", name="Fortnite Refunds", company="Fortnite",
       category="tech_products", summary="Refunds for players charged for unwanted in-game purchases through misleading button design.",
       payout_low=114.0, payout_high=114.0, total_fund=2.45e8, payout_note="Claim form required",
       proof_required=True, claim_url="https://www.fortniterefund.com",
       source_url="https://www.ftc.gov/enforcement/refunds/fortnite-refunds",
       administrator="Rust Consulting, Inc.", phone="1-833-915-0880",
       announced_on=T-timedelta(days=380), claim_deadline=T+timedelta(days=140), status="open", confidence=0.92),

  # --- UPCOMING (announced, claims not yet open) — the subscription funnel ---
  dict(source_key="apple-siri-privacy", name="Apple Siri Privacy Settlement", company="Apple",
       category="data_breach", summary="Apple has reached a proposed settlement over Siri recordings captured without consent. Claims are not open yet — the court must grant final approval first.",
       payout_low=20.0, payout_high=100.0, total_fund=9.5e7, payout_note="Estimated — final amount set at approval",
       source_url="https://www.ftc.gov/enforcement/refunds",
       administrator="To be appointed", announced_on=T-timedelta(days=20),
       claim_deadline=None, expected_open="Expected to open spring 2026",
       eligibility="US owners of a Siri-enabled Apple device between 2014 and 2024.",
       status="upcoming", confidence=0.9),
  dict(source_key="meta-pixel-health", name="Meta Health Data Settlement", company="Meta",
       category="data_breach", summary="A proposed settlement over health information collected from hospital websites via Meta's tracking pixel. Awaiting preliminary approval before claims open.",
       payout_low=30.0, payout_high=None, total_fund=2.0e8, payout_note="Estimated — not yet finalised",
       source_url="https://www.ftc.gov/enforcement/refunds",
       administrator="To be appointed", announced_on=T-timedelta(days=12),
       claim_deadline=None, expected_open="Expected mid-2026",
       eligibility="People who used a hospital patient portal that ran Meta tracking.",
       status="upcoming", confidence=0.85),
  dict(source_key="ticketmaster-fees", name="Ticketmaster Junk Fees Refunds", company="Ticketmaster",
       category="tech_products", summary="A proposed FTC action over hidden and misrepresented ticket fees. If approved, buyers could receive partial refunds on past purchases.",
       payout_low=15.0, payout_high=75.0, total_fund=4.0e7, payout_note="Estimated — pending approval",
       source_url="https://www.ftc.gov/enforcement/refunds",
       administrator="To be appointed", announced_on=T-timedelta(days=6),
       claim_deadline=None, expected_open="Expected late 2026",
       eligibility="Anyone who paid service fees on Ticketmaster between 2019 and 2025.",
       status="upcoming", confidence=0.8),
]

init_db()
with session_scope() as s:
    c, u = upsert(s, "ftc", DEMO)
    sweep_deadlines(s)
    if not s.query(Subscriber).filter_by(phone="+15555550123").first():
        s.add(Subscriber(phone="+15555550123", verified=True, categories="",
                         min_payout=0.0, claim_required_only=False))
print(f"seeded: {c} new, {u} updated")
