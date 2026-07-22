"""SMS delivery.

Talks to Twilio over plain HTTP so there's no SDK dependency. With TWILIO_ACCOUNT_SID
unset, messages print to stdout, so the whole opt-in and alert flow is testable with no
account and no spend.

Compliance is built in rather than bolted on, because SMS is the one channel where
getting it wrong is expensive:
  - confirmed opt-in (we text a code, they text it back) before anything else is sent
  - STOP / UNSTOP / HELP handled on every inbound message
  - every alert carries the "STOP to end" footer
  - a hard per-day cap and quiet hours, enforced in code not policy
"""
import hashlib
import hmac
import base64
import re
from datetime import date, datetime

import httpx

from .config import settings

TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


# ------------------------------------------------------------------ phone numbers
def normalize_phone(raw: str) -> str | None:
    """Return E.164 or None. Deliberately conservative — a bad number is a wasted
    send and, worse, a text to a stranger who never consented."""
    if not raw:
        return None
    digits = re.sub(r"[^\d+]", "", raw.strip())

    if digits.startswith("+"):
        rest = digits[1:]
        if rest.isdigit() and 8 <= len(rest) <= 15:
            return "+" + rest
        return None

    if len(digits) == 10 and digits[0] in "23456789":      # US/CA without country code
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None


def mask_phone(phone: str) -> str:
    """For logs and admin screens. Never print a full number."""
    return phone[:-4].replace(phone[2:-4], "*" * len(phone[2:-4])) + phone[-4:] \
        if phone and len(phone) > 6 else "***"


# ---------------------------------------------------------------------- sending
def send_sms(to: str, body: str) -> tuple[bool, str | None]:
    """Returns (ok, error). Never raises — a failed text must not kill a batch."""
    if not settings.twilio_account_sid:
        print(f"\n{'='*60}\nSMS -> {to}\n{'-'*60}\n{body}\n{'='*60}\n")
        return True, None

    payload = {"To": to, "Body": body}
    if settings.twilio_messaging_service_sid:
        payload["MessagingServiceSid"] = settings.twilio_messaging_service_sid
    else:
        payload["From"] = settings.twilio_from_number

    try:
        r = httpx.post(
            TWILIO_API.format(sid=settings.twilio_account_sid),
            data=payload,
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            timeout=20,
        )
        if r.status_code >= 400:
            return False, f"twilio {r.status_code}: {r.text[:200]}"
        return True, None
    except Exception as e:
        return False, f"{e.__class__.__name__}: {e}"


def validate_twilio_signature(url: str, params: dict, signature: str) -> bool:
    """Verify an inbound webhook really came from Twilio.

    Without this, anyone who finds the endpoint can forge a STOP for someone else's
    number, or spoof a confirmation.
    """
    if not settings.twilio_auth_token:
        return False
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(settings.twilio_auth_token.encode(),
                      data.encode("utf-8"), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature or "")


# -------------------------------------------------------------------- templates
BRAND = settings.site_name
FOOTER = "Reply STOP to end."


def confirm_message(code: str) -> str:
    return (f"{BRAND}: your code is {code}. Reply with this code to confirm alerts "
            f"about new federal refund programs. Msg&data rates may apply. "
            f"Msg frequency varies. Reply HELP for help, STOP to cancel.")


def welcome_message() -> str:
    return (f"{BRAND}: you're confirmed. We'll text you when a new federal refund "
            f"program matches your filters. We never ask for money or personal info — "
            f"claims are always free and filed on the official site. {FOOTER}")


def help_message() -> str:
    return (f"{BRAND} texts you when new US federal refund programs open. "
            f"We are not a government agency and never charge to file. "
            f"Help: {settings.contact_email}. Reply STOP to cancel. "
            f"Msg&data rates may apply.")


def stop_message() -> str:
    return f"{BRAND}: you're unsubscribed and won't get more texts. Reply START to rejoin."


def alert_message(program, kind: str) -> str:
    """Kept under 320 chars (2 SMS segments) — every segment costs money.

    The link and the STOP footer are reserved first and the *name* is truncated to
    fit around them. Truncating the whole string instead would drop the footer,
    which carriers require on every message, and the link, which is the entire point.

    Format is deliberately boring and scannable: what, how much, when it closes,
    where to go. This competes for attention with actual settlement scams, so it
    needs to read like a utility, not marketing.
    """
    LIMIT = 320
    if kind == "deadline_soon":
        lead = f"CLOSING SOON ({program.days_left}d left)"
    elif kind == "opened":
        lead = "NOW OPEN — you asked to be told"
    else:
        lead = "NEW REFUND"

    # Reserved tail — never truncated.
    link = f"{settings.base_url}/programs/{program.slug}"
    tail = f"{link} {FOOTER}"

    # Facts, in priority order.
    facts = []
    est = program.payout_high or program.payout_low
    if est:
        facts.append(f"~${est:,.0f} each.")
    if program.claim_deadline:
        facts.append(f"File by {program.claim_deadline:%b %d}.")
    if program.payout_note and "automatic" in program.payout_note.lower():
        facts.append("No claim needed.")
    fact_str = (" " + " ".join(facts)) if facts else ""

    prefix = f"{BRAND}: {lead} — "
    budget = LIMIT - len(prefix) - len(fact_str) - len(tail) - 2   # 2 = ". " + " "
    name = program.name
    if budget < 8:
        # Pathological case: drop facts before mangling the name beyond recognition.
        fact_str = ""
        budget = LIMIT - len(prefix) - len(tail) - 2
    if len(name) > budget:
        name = name[:max(1, budget - 1)].rstrip() + "…"

    return f"{prefix}{name}.{fact_str} {tail}"
