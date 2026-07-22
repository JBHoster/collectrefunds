"""CollectRefunds — public refund tracker.

No accounts, no login, no email. The site is a filterable public list; the only
subscription is SMS, and it requires a confirmed opt-in.
"""
import asyncio
import json
import logging
import random
from datetime import date, datetime, timedelta

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (
    HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator
from sqlalchemy import func, nulls_last, or_, select
from sqlalchemy.orm import Session

from .config import settings
from .content import PAGES, UPDATED
from .db import SessionLocal, get_db, init_db
from .models import Delivery, Event, Program, ScrapeRun, Subscriber
from .security import client_ip, rate_limit, require_admin, security_headers_middleware
from .sms import (
    confirm_message, help_message, mask_phone, normalize_phone, send_sms,
    stop_message, validate_twilio_signature, welcome_message,
)

log = logging.getLogger("claimwatch")

app = FastAPI(title="CollectRefunds", version="2.0.0", docs_url="/api/docs", redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=800)
app.middleware("http")(security_headers_middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.base_url] if settings.environment == "production" else ["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
def _startup():
    init_db()
    if settings.environment == "production":
        if settings.secret_key == "change-me":
            raise RuntimeError("Refusing to start: SECRET_KEY is unset in production.")
        if not settings.admin_password:
            log.warning("ADMIN_PASSWORD unset — admin returns 503.")

    # If the database is empty (fresh deploy), load demo programs so the site is
    # never blank while the first real scrape runs — or if the source is briefly
    # unreachable. Real FTC data layers on top; the demo rows share source_key
    # values the scraper would use, so they update in place rather than duplicate.
    try:
        with SessionLocal() as s:
            if s.query(Program).count() == 0:
                from .demo_data import DEMO
                from .ingest.base import upsert, sweep_deadlines
                upsert(s, "ftc", DEMO)
                sweep_deadlines(s)
                s.commit()
                log.info("seeded %d demo programs on first boot", len(DEMO))
    except Exception:
        log.exception("first-boot seed skipped")

    if settings.run_scheduler_in_web:
        from .scheduler import start_scheduler
        start_scheduler()


def ctx(request: Request, **extra):
    return {
        "site_name": settings.site_name,
        "base_url": settings.base_url,
        "contact_email": settings.contact_email,
        "path": request.url.path,
        **extra,
    }


# ------------------------------------------------------------------- formatting
def money_h(n):
    if n is None:
        return None
    if n >= 1e9:
        return f"${n/1e9:.1f}B"
    if n >= 1e6:
        return f"${n/1e6:.1f}M"
    return f"${n:,.0f}"


def meter(days):
    if days is None:
        return {"cls": "none", "pct": 100, "label": "No deadline posted"}
    pct = max(2, min(100, (days / 180) * 100))
    cls = "critical" if days <= 7 else "soon" if days <= 30 else ""
    return {"cls": cls, "pct": pct,
            "label": "Closes today" if days == 0 else f"{days} days left"}


def is_auto(p) -> bool:
    return bool(p.payout_note and "automatic" in p.payout_note.lower())


def as_dict(p: Program) -> dict:
    """One shape, used by the API, the templates and the client filter."""
    m = meter(p.days_left)
    est = p.payout_high or p.payout_low
    return {
        "slug": p.slug, "name": p.name, "company": p.company,
        "summary": p.summary, "category": p.category,
        "category_label": (p.category or "refund").replace("_", " ").title(),
        "payout": est, "payout_h": money_h(est),
        "total_fund": p.total_fund, "fund_h": money_h(p.total_fund),
        "payout_note": p.payout_note, "auto": is_auto(p),
        "proof_required": bool(p.proof_required),
        "claim_url": p.claim_url, "source_url": p.source_url,
        "administrator": p.administrator, "phone": p.phone,
        "eligibility": p.eligibility,
        "deadline": p.claim_deadline.isoformat() if p.claim_deadline else None,
        "deadline_date": p.claim_deadline.isoformat() if p.claim_deadline else None,
        "expected_open": p.expected_open,
        "days_left": p.days_left, "status": p.status,
        "meter_cls": m["cls"], "meter_pct": m["pct"], "meter_label": m["label"],
        "added": p.first_seen_at.isoformat() if p.first_seen_at else None,
    }


def opportunity_score(d: dict) -> float:
    """Rank a program for the 'best opportunities' rail: reward high payout, easy
    claims, and genuine urgency. Deliberately transparent — no hidden weighting."""
    s = float(d["payout"] or 10)
    if not d["proof_required"]:
        s *= 1.3
    if d["auto"]:
        s *= 1.15
    if d["days_left"] is not None and 0 <= d["days_left"] <= 30:
        s *= 1.6
    return s


def visible(db: Session):
    return db.query(Program).filter(Program.published.is_(True),
                                    Program.needs_review.is_(False))


def compute_stats(db: Session) -> dict:
    q = visible(db).filter(Program.status == "open")
    soon = q.filter(Program.claim_deadline.isnot(None),
                    Program.claim_deadline <= date.today() + timedelta(days=30),
                    Program.claim_deadline >= date.today()).count()
    last = db.query(ScrapeRun).order_by(ScrapeRun.id.desc()).first()
    total = (db.query(func.sum(Program.total_fund))
             .filter(Program.status == "open", Program.published.is_(True),
                     Program.needs_review.is_(False)).scalar() or 0)
    top_payout = (db.query(func.max(
                    func.coalesce(Program.payout_high, Program.payout_low)))
                  .filter(Program.status == "open", Program.published.is_(True),
                          Program.needs_review.is_(False)).scalar() or 0)
    return {
        "open_programs": q.count(),
        "closing_soon": soon,
        "total_fund": total,
        "total_fund_h": money_h(total) or "—",
        "max_payout": int(top_payout),
        "new_this_week": visible(db).filter(
            Program.first_seen_at >= datetime.utcnow() - timedelta(days=7)).count(),
        "last_ingest": last.finished_at.isoformat() if last and last.finished_at else None,
        "last_ingest_ok": last.ok if last else None,
    }


def data_version(db: Session) -> str:
    """Cheap fingerprint of the dataset. Drives live updates without websockets."""
    last_event = db.query(func.max(Event.id)).scalar() or 0
    count = visible(db).count()
    updated = db.query(func.max(Program.updated_at)).scalar()
    return f"{last_event}-{count}-{updated.isoformat() if updated else '0'}"


# ============================================================== PAGES ==========
@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    programs = (visible(db).filter(Program.status == "open")
                .order_by(nulls_last(Program.claim_deadline.asc())).limit(300).all())
    rows = [as_dict(p) for p in programs]
    cats = sorted({r["category"] for r in rows if r["category"]})

    # Best opportunities: transparent score over payout, ease, and urgency.
    best = sorted(rows, key=opportunity_score, reverse=True)[:3]

    # Soonest-closing open program with a real deadline drives the live countdown.
    closing = [r for r in rows if r["days_left"] is not None and r["days_left"] >= 0]
    soonest = min(closing, key=lambda r: r["days_left"]) if closing else None

    # Upcoming: announced but not yet accepting claims. This is the subscription
    # funnel — people follow a specific one and get texted the moment it opens.
    upcoming_q = (visible(db).filter(Program.status == "upcoming")
                  .order_by(nulls_last(Program.announced_on.desc())).limit(12).all())
    upcoming = [as_dict(p) for p in upcoming_q]

    return templates.TemplateResponse(request, "index.html", ctx(
        request, programs=rows, programs_json=json.dumps(rows),
        best=best, best_json=json.dumps(best),
        soonest=soonest, soonest_json=json.dumps(soonest),
        upcoming=upcoming, upcoming_json=json.dumps(upcoming),
        categories=cats, stats=compute_stats(db),
        stats_json=json.dumps(compute_stats(db)),
        version=data_version(db), max_sms=settings.max_sms_per_day))


@app.get("/programs/{slug}", response_class=HTMLResponse)
def program_page(slug: str, request: Request, db: Session = Depends(get_db)):
    p = visible(db).filter(Program.slug == slug).first()
    if not p:
        return templates.TemplateResponse(request, "404.html", ctx(request),
                                          status_code=404)
    est = p.payout_high or p.payout_low
    related = (visible(db)
               .filter(Program.id != p.id, Program.status == "open",
                       Program.category == p.category)
               .order_by(nulls_last(Program.claim_deadline.asc())).limit(5).all())
    return templates.TemplateResponse(request, "program.html", ctx(
        request, p=p, d=as_dict(p), meter=meter(p.days_left),
        related=[as_dict(r) for r in related], fund_h=money_h(p.total_fund),
        deadline_answer=(f"The claim deadline is {p.claim_deadline:%B %d, %Y}."
                         if p.claim_deadline else
                         "No claim deadline has been published yet."),
        payout_answer=(f"Around ${est:,.0f} per person, though the final amount depends "
                       "on how many valid claims are filed." if est else
                       "The per-person amount has not been published; it usually depends "
                       "on how many valid claims are filed."),
        eligibility_answer=(p.eligibility or
                            "Eligibility is set by the official program — check the "
                            "official claim site.")))


# ============================================================== LIVE ===========
@app.get("/api/version")
def api_version(db: Session = Depends(get_db)):
    """Polling fallback for clients that can't hold an SSE connection."""
    return {"version": data_version(db)}


@app.get("/api/live")
async def live(request: Request):
    """Server-sent events. Pushes only when the dataset fingerprint changes.

    Each connection polls the database on an interval rather than relying on an
    in-process pubsub, so this keeps working across multiple web containers.
    """
    async def stream():
        last = None
        idle = 0
        while True:
            if await request.is_disconnected():
                break
            db = SessionLocal()
            try:
                v = data_version(db)
                if v != last:
                    payload = {"version": v, "stats": compute_stats(db)}
                    yield f"event: update\ndata: {json.dumps(payload)}\n\n"
                    last, idle = v, 0
                else:
                    idle += 1
                    if idle % 6 == 0:        # keep-alive through proxies
                        yield ": ping\n\n"
            except Exception as e:
                log.warning("sse loop error: %s", e)
            finally:
                db.close()
            await asyncio.sleep(settings.live_poll_seconds)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


# ============================================================== DATA API =======
@app.get("/api/programs")
def list_programs(
    db: Session = Depends(get_db),
    q: str | None = None,
    category: str | None = Query(None, description="comma-separated"),
    status_: str = Query("open", alias="status"),
    min_payout: float | None = None,
    closing_within: int | None = None,
    claim_type: str | None = Query(None, description="auto | claim"),
    proof: str | None = Query(None, description="none | required"),
    sort: str = "deadline",
    limit: int = Query(300, le=500),
    _rl=Depends(rate_limit("api", 240, 60)),
):
    query = visible(db)
    if status_ != "all":
        query = query.filter(Program.status == status_)
    if category:
        cats = [c.strip() for c in category.split(",") if c.strip()]
        if cats:
            query = query.filter(Program.category.in_(cats))
    if q:
        like = f"%{q[:80]}%"
        query = query.filter(or_(Program.name.ilike(like), Program.company.ilike(like),
                                 Program.summary.ilike(like),
                                 Program.eligibility.ilike(like)))
    if min_payout:
        query = query.filter(
            func.coalesce(Program.payout_high, Program.payout_low) >= min_payout)
    if closing_within is not None:
        query = query.filter(
            Program.claim_deadline.isnot(None),
            Program.claim_deadline <= date.today() + timedelta(days=closing_within),
            Program.claim_deadline >= date.today())
    if claim_type == "auto":
        query = query.filter(Program.payout_note.ilike("%automatic%"))
    elif claim_type == "claim":
        query = query.filter(or_(Program.payout_note.is_(None),
                                 ~Program.payout_note.ilike("%automatic%")))
    if proof == "required":
        query = query.filter(Program.proof_required.is_(True))
    elif proof == "none":
        query = query.filter(or_(Program.proof_required.is_(False),
                                 Program.proof_required.is_(None)))

    if sort == "payout":
        query = query.order_by(nulls_last(
            func.coalesce(Program.payout_high, Program.payout_low).desc()))
    elif sort == "fund":
        query = query.order_by(nulls_last(Program.total_fund.desc()))
    elif sort == "new":
        query = query.order_by(Program.first_seen_at.desc())
    else:
        query = query.order_by(nulls_last(Program.claim_deadline.asc()))

    return [as_dict(p) for p in query.limit(limit).all()]


@app.get("/api/programs/{slug}")
def get_program(slug: str, db: Session = Depends(get_db)):
    p = visible(db).filter(Program.slug == slug).first()
    if not p:
        raise HTTPException(404, "Program not found")
    return as_dict(p)


@app.get("/api/stats")
def api_stats(db: Session = Depends(get_db)):
    return compute_stats(db)


@app.get("/api/categories")
def api_categories(db: Session = Depends(get_db)):
    rows = (visible(db).filter(Program.status == "open")
            .with_entities(Program.category, func.count(Program.id))
            .group_by(Program.category).all())
    return [{"category": c, "count": n} for c, n in rows if c]


@app.get("/api/events")
def api_events(db: Session = Depends(get_db), limit: int = Query(30, le=100)):
    rows = db.query(Event).order_by(Event.created_at.desc()).limit(limit * 3).all()
    out = []
    for e in rows:
        p = db.get(Program, e.program_id)
        if not p or not p.published or p.needs_review:
            continue
        out.append({"kind": e.kind, "detail": e.detail, "program": p.name,
                    "slug": p.slug, "at": e.created_at.isoformat()})
        if len(out) >= limit:
            break
    return out


# ============================================================== SMS OPT-IN =====
class SubscribeIn(BaseModel):
    phone: str
    categories: str = ""
    min_payout: float = 0.0
    claim_required_only: bool = False
    consent: bool = False
    follow: str = ""          # slug of a specific upcoming program to be told about

    @field_validator("categories")
    @classmethod
    def cap(cls, v: str) -> str:
        return (v or "")[:300].strip()

    @field_validator("follow")
    @classmethod
    def cap_follow(cls, v: str) -> str:
        return (v or "")[:120].strip()


def _merge_follow(existing: str, slug: str) -> str:
    """Add a program slug to a comma-separated follows list, no duplicates."""
    if not slug:
        return existing or ""
    items = [s for s in (existing or "").split(",") if s]
    if slug not in items:
        items.append(slug)
    return ",".join(items)[:2000]


@app.post("/api/subscribe")
def subscribe(body: SubscribeIn, request: Request, db: Session = Depends(get_db),
              _rl=Depends(rate_limit("subscribe", 4, 3600))):
    """Step 1 of confirmed opt-in: store the number unverified, text a code.

    Nothing is ever sent to this number beyond the single confirmation message
    until they text the code back. If they came in via an upcoming-program
    "notify me" button, `follow` records which one so they're texted the moment
    it opens — regardless of their other filters.
    """
    if not body.consent:
        raise HTTPException(400, "You must agree to receive text messages.")

    phone = normalize_phone(body.phone)
    if not phone:
        raise HTTPException(400, "That doesn't look like a valid mobile number.")

    # Confirm the followed program exists and is upcoming (avoid junk in the list).
    follow = ""
    if body.follow:
        prog = (db.query(Program)
                .filter(Program.slug == body.follow, Program.published.is_(True))
                .first())
        if prog:
            follow = prog.slug

    sub = db.query(Subscriber).filter_by(phone=phone).first()
    if sub and sub.verified and not sub.opted_out:
        # Already verified — update filters and add the follow without re-verifying.
        sub.categories = body.categories
        sub.min_payout = max(0.0, body.min_payout)
        sub.claim_required_only = body.claim_required_only
        sub.follows = _merge_follow(sub.follows, follow)
        db.commit()
        msg = ("You're all set — we'll text you the moment it opens."
               if follow else "Your alert filters have been updated.")
        return {"ok": True, "status": "updated", "message": msg}

    if not sub:
        sub = Subscriber(phone=phone)
        db.add(sub)

    # A previous STOP is cleared only by an explicit new opt-in like this one.
    sub.opted_out = False
    sub.opted_out_at = None
    sub.verified = False
    sub.categories = body.categories
    sub.min_payout = max(0.0, body.min_payout)
    sub.claim_required_only = body.claim_required_only
    sub.follows = _merge_follow(sub.follows, follow)
    sub.confirm_code = f"{random.randint(0, 999999):06d}"
    sub.confirm_sent_at = datetime.utcnow()
    sub.confirm_attempts = 0
    sub.consent_ip = client_ip(request)
    sub.consent_at = datetime.utcnow()
    db.commit()

    ok, err = send_sms(phone, confirm_message(sub.confirm_code))
    if not ok:
        log.warning("confirm send failed for %s: %s", mask_phone(phone), err)
        raise HTTPException(502, "We couldn't send the confirmation text. "
                                 "Check the number and try again.")

    return {"ok": True, "status": "pending",
            "message": "We texted you a 6-digit code. Reply to that text with the "
                       "code, or enter it below."}


class ConfirmIn(BaseModel):
    phone: str
    code: str


@app.post("/api/confirm")
def confirm(body: ConfirmIn, db: Session = Depends(get_db),
            _rl=Depends(rate_limit("confirm", 10, 900))):
    phone = normalize_phone(body.phone)
    sub = db.query(Subscriber).filter_by(phone=phone).first() if phone else None
    if not sub or not sub.confirm_code:
        raise HTTPException(404, "No pending confirmation for that number.")

    if sub.confirm_sent_at and \
            datetime.utcnow() - sub.confirm_sent_at > timedelta(minutes=30):
        raise HTTPException(400, "That code expired. Sign up again for a new one.")

    sub.confirm_attempts = (sub.confirm_attempts or 0) + 1
    if sub.confirm_attempts > 6:
        db.commit()
        raise HTTPException(429, "Too many attempts. Sign up again for a new code.")

    if body.code.strip() != sub.confirm_code:
        db.commit()
        raise HTTPException(400, "That code doesn't match.")

    sub.verified = True
    sub.confirm_code = None
    db.commit()
    send_sms(sub.phone, welcome_message())
    return {"ok": True, "message": "Confirmed. You'll get a text when a new program "
                                   "matches your filters."}


@app.post("/api/unsubscribe")
def unsubscribe(body: ConfirmIn, db: Session = Depends(get_db),
                _rl=Depends(rate_limit("unsub", 10, 900))):
    """Web unsubscribe. Texting STOP is the primary path; this is a convenience."""
    phone = normalize_phone(body.phone)
    sub = db.query(Subscriber).filter_by(phone=phone).first() if phone else None
    if sub:
        sub.opted_out = True
        sub.opted_out_at = datetime.utcnow()
        sub.verified = False
        db.commit()
    # Same answer either way — never confirm whether a number is on the list.
    return {"ok": True, "message": "That number will not receive further texts."}


@app.post("/sms/inbound")
async def sms_inbound(request: Request, db: Session = Depends(get_db)):
    """Twilio webhook. Handles STOP / START / HELP and code confirmation by reply.

    Carriers require STOP to work; Twilio also enforces it upstream, but we honour
    it in our own data so we never even attempt a send.
    """
    form = dict(await request.form())
    if settings.twilio_auth_token:
        sig = request.headers.get("X-Twilio-Signature", "")
        url = f"{settings.base_url}/sms/inbound"
        if not validate_twilio_signature(url, form, sig):
            raise HTTPException(403, "Invalid signature")

    phone = normalize_phone(form.get("From", ""))
    body = (form.get("Body") or "").strip()
    word = body.upper()

    def twiml(msg: str | None = None):
        inner = f"<Message>{msg}</Message>" if msg else ""
        return PlainTextResponse(
            f'<?xml version="1.0" encoding="UTF-8"?><Response>{inner}</Response>',
            media_type="application/xml")

    if not phone:
        return twiml()

    sub = db.query(Subscriber).filter_by(phone=phone).first()

    if word in {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}:
        if sub:
            sub.opted_out = True
            sub.opted_out_at = datetime.utcnow()
            sub.verified = False
            db.commit()
        return twiml(stop_message())

    if word in {"HELP", "INFO"}:
        return twiml(help_message())

    if word in {"START", "UNSTOP", "YES"}:
        if sub:
            sub.opted_out = False
            sub.opted_out_at = None
            sub.verified = True
            db.commit()
            return twiml(welcome_message())
        return twiml(f"{settings.site_name}: sign up at {settings.base_url}")

    # Six digits = a confirmation code reply.
    if sub and sub.confirm_code and body.replace(" ", "") == sub.confirm_code:
        sub.verified = True
        sub.confirm_code = None
        sub.opted_out = False
        db.commit()
        return twiml(welcome_message())

    return twiml(help_message())


# ============================================================== PAYMENTS =======
class CheckoutIn(BaseModel):
    phone: str


@app.post("/api/checkout")
def checkout(body: CheckoutIn, request: Request, db: Session = Depends(get_db),
             _rl=Depends(rate_limit("checkout", 8, 3600))):
    """Start a Stripe Checkout session to upgrade a verified subscriber to Pro.

    The person must already be a confirmed SMS subscriber — Pro is about *how* they
    get alerts, so we need their verified number first.
    """
    from .payments import create_checkout_session, is_enabled

    if not is_enabled():
        raise HTTPException(503, "Pro isn't available yet — check back soon.")

    phone = normalize_phone(body.phone)
    if not phone:
        raise HTTPException(400, "Enter the mobile number you signed up with.")

    sub = db.query(Subscriber).filter_by(phone=phone).first()
    if not sub or not sub.verified:
        raise HTTPException(400, "Confirm your number for free text alerts first, "
                                 "then upgrade to Pro.")
    if sub.is_pro:
        raise HTTPException(400, "You're already a Pro member.")

    base = settings.base_url.rstrip("/")
    ok, url, err = create_checkout_session(
        phone,
        success_url=f"{base}/pro/success",
        cancel_url=f"{base}/#pricing",
    )
    if not ok:
        log.warning("checkout failed for %s: %s", mask_phone(phone), err)
        raise HTTPException(502, "We couldn't start checkout. Please try again.")
    return {"ok": True, "url": url}


@app.post("/api/billing-portal")
def billing_portal(body: CheckoutIn, db: Session = Depends(get_db),
                   _rl=Depends(rate_limit("portal", 8, 3600))):
    """Give an existing Pro member a link to manage or cancel their subscription."""
    from .payments import create_billing_portal, is_enabled
    if not is_enabled():
        raise HTTPException(503, "Payments aren't enabled.")
    phone = normalize_phone(body.phone)
    sub = db.query(Subscriber).filter_by(phone=phone).first() if phone else None
    if not sub or not sub.stripe_customer_id:
        raise HTTPException(404, "No Pro subscription found for that number.")
    ok, url, err = create_billing_portal(
        sub.stripe_customer_id, return_url=settings.base_url.rstrip("/"))
    if not ok:
        raise HTTPException(502, "Couldn't open the billing portal.")
    return {"ok": True, "url": url}


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Stripe tells us when a payment completes or a subscription ends.

    The signature is verified before we trust anything — otherwise anyone could
    forge a 'payment succeeded' and get Pro for free.
    """
    from .payments import verify_webhook

    payload = await request.body()
    sig = request.headers.get("Stripe-Signature", "")
    event = verify_webhook(payload, sig)
    if event is None:
        raise HTTPException(400, "Invalid signature")

    etype = event.get("type", "")
    obj = event.get("data", {}).get("object", {})

    def find_sub():
        phone = (obj.get("metadata", {}).get("phone")
                 or obj.get("client_reference_id"))
        if phone:
            p = normalize_phone(phone)
            if p:
                return db.query(Subscriber).filter_by(phone=p).first()
        # fall back to matching the Stripe customer id
        cust = obj.get("customer")
        if cust:
            return db.query(Subscriber).filter_by(stripe_customer_id=cust).first()
        return None

    if etype == "checkout.session.completed":
        sub = find_sub()
        if sub:
            sub.is_pro = True
            sub.pro_since = datetime.utcnow()
            sub.pro_until = None
            sub.stripe_customer_id = obj.get("customer") or sub.stripe_customer_id
            sub.stripe_subscription_id = obj.get("subscription") or sub.stripe_subscription_id
            db.commit()
            log.info("Pro activated for %s", mask_phone(sub.phone))

    elif etype in ("customer.subscription.deleted", "customer.subscription.paused"):
        sub = find_sub()
        if sub:
            sub.is_pro = False
            sub.pro_until = datetime.utcnow()
            db.commit()
            log.info("Pro ended for %s", mask_phone(sub.phone))

    # Always 200 so Stripe stops retrying a handled event.
    return {"received": True}


@app.get("/pro/success", response_class=HTMLResponse)
def pro_success(request: Request):
    return templates.TemplateResponse(request, "message.html", ctx(
        request, eyebrow="Welcome to Pro",
        heading="You're all set.",
        body="Your Pro alerts are active. You'll now get an instant text the moment "
             "a refund you qualify for opens — and you can follow as many upcoming "
             "settlements as you like. Reply STOP to any text to pause anytime.",
        cta_href="/", cta_label="Back to refunds"))


# ============================================================== ADMIN ==========
@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db),
               _: str = Depends(require_admin)):
    subs = db.query(Subscriber).filter(Subscriber.verified.is_(True),
                                       Subscriber.opted_out.is_(False)).count()
    fails = (db.query(Delivery).filter(Delivery.ok.is_(False))
             .order_by(Delivery.id.desc()).limit(10).all())
    return templates.TemplateResponse(request, "admin.html", ctx(
        request,
        queue=db.query(Program).filter(Program.needs_review.is_(True)).all(),
        runs=db.query(ScrapeRun).order_by(ScrapeRun.id.desc()).limit(10).all(),
        threshold=settings.review_threshold, subscribers=subs,
        opted_out=db.query(Subscriber).filter(Subscriber.opted_out.is_(True)).count(),
        sent_today=db.query(Delivery).filter(
            Delivery.sent_at >= datetime.combine(date.today(), datetime.min.time())
        ).count(),
        failures=fails))


@app.post("/admin/review/{program_id}/approve")
def admin_approve(program_id: int, db: Session = Depends(get_db),
                  _: str = Depends(require_admin)):
    p = db.get(Program, program_id)
    if not p:
        raise HTTPException(404)
    p.needs_review, p.published = False, True
    db.commit()
    return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/review/{program_id}/reject")
def admin_reject(program_id: int, db: Session = Depends(get_db),
                 _: str = Depends(require_admin)):
    p = db.get(Program, program_id)
    if not p:
        raise HTTPException(404)
    p.published, p.needs_review = False, False
    db.commit()
    return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)


# ============================================================== MACHINE ========
@app.get("/robots.txt", response_class=Response, include_in_schema=False)
def robots():
    return Response(
        f"User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /api/\n"
        f"Disallow: /sms/\n\nSitemap: {settings.base_url}/sitemap.xml\n",
        media_type="text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request, db: Session = Depends(get_db)):
    programs = visible(db).order_by(Program.updated_at.desc()).limit(5000).all()
    return templates.TemplateResponse(request, "sitemap.xml",
                                      ctx(request, programs=programs),
                                      media_type="application/xml")


@app.get("/healthz", include_in_schema=False)
def healthz(db: Session = Depends(get_db)):
    """Liveness check — is the web app up and the database reachable?

    This is what the host (Render) polls to decide the deploy succeeded, so it must
    return 200 as soon as the app is serving, even before the first scrape has run.
    Data freshness is reported in the body (and enforced strictly by /healthz/data
    for an uptime monitor) rather than failing this check, otherwise a brand-new
    deploy can never go live.
    """
    try:
        db.execute(select(func.count(Program.id)))
    except Exception as e:
        return JSONResponse({"ok": False, "db": str(e)}, status_code=503)

    last = db.query(ScrapeRun).order_by(ScrapeRun.id.desc()).first()
    stale = True
    if last and last.finished_at:
        stale = (datetime.utcnow() - last.finished_at) > timedelta(hours=26)
    return JSONResponse({
        "ok": True,                       # the app is alive
        "data_fresh": bool(last and last.ok and not stale),
        "stale": stale,
        "programs": visible(db).filter(Program.status == "open").count(),
        "last_ingest": last.finished_at.isoformat() if last and last.finished_at else None,
        "version": app.version,
    }, status_code=200)


@app.get("/healthz/data", include_in_schema=False)
def healthz_data(db: Session = Depends(get_db)):
    """Strict freshness check for an external uptime monitor. Returns 503 when the
    scraper has gone quiet (>26h) — because serving weeks-old deadlines is worse than
    being down. Point your monitor here, not at /healthz."""
    last = db.query(ScrapeRun).order_by(ScrapeRun.id.desc()).first()
    stale = True
    if last and last.finished_at:
        stale = (datetime.utcnow() - last.finished_at) > timedelta(hours=26)
    healthy = bool(last and last.ok and not stale)
    return JSONResponse({
        "ok": healthy, "stale": stale,
        "last_ingest": last.finished_at.isoformat() if last and last.finished_at else None,
    }, status_code=200 if healthy else 503)


# --------------------------------------------------------- static pages + errors
@app.get("/{page}", response_class=HTMLResponse, include_in_schema=False)
def legal_page(page: str, request: Request):
    """Registered last so it can't shadow a real route."""
    if page not in PAGES:
        return templates.TemplateResponse(request, "404.html", ctx(request),
                                          status_code=404)
    return templates.TemplateResponse(request, "legal.html",
                                      ctx(request, updated=UPDATED, **PAGES[page]))


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return templates.TemplateResponse(request, "404.html", ctx(request), status_code=404)


app.mount("/static", StaticFiles(directory="web"), name="static")
