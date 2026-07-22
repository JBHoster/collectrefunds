"""Seed demo data so you can see the UI before wiring live ingest."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import init_db, session_scope
from app.demo_data import DEMO
from app.ingest.base import upsert, sweep_deadlines
from app.models import Subscriber

init_db()
with session_scope() as s:
    c, u = upsert(s, "ftc", DEMO)
    sweep_deadlines(s)
    if not s.query(Subscriber).filter_by(phone="+15555550123").first():
        s.add(Subscriber(phone="+15555550123", verified=True, categories="",
                         min_payout=0.0, claim_required_only=False))
print(f"seeded: {c} new, {u} updated")
