"""Stripe payments for the Pro subscription.

Uses Stripe's REST API over plain HTTP so there's no SDK dependency, mirroring how
sms.py talks to Twilio. With STRIPE_SECRET_KEY unset, is_enabled() is False and the
site shows the Pro button as "coming soon" instead of trying to charge — so everything
runs with no Stripe account.

Card data never touches this server: we create a Stripe Checkout session and redirect
the person to Stripe's hosted page. Stripe tells us the result via a signed webhook.
"""
import hashlib
import hmac
import json
import time

import httpx

from .config import settings

STRIPE_API = "https://api.stripe.com/v1"


def is_enabled() -> bool:
    """True when Stripe is configured enough to take a payment."""
    return bool(settings.stripe_secret_key and settings.stripe_price_id)


def _post(path: str, data: dict) -> tuple[bool, dict]:
    """POST form-encoded to Stripe. Returns (ok, parsed_json)."""
    try:
        r = httpx.post(
            f"{STRIPE_API}/{path}",
            data=data,
            auth=(settings.stripe_secret_key, ""),
            timeout=20,
        )
        body = r.json()
        if r.status_code >= 400:
            return False, body
        return True, body
    except Exception as e:
        return False, {"error": {"message": f"{e.__class__.__name__}: {e}"}}


def create_checkout_session(phone: str, success_url: str, cancel_url: str) -> tuple[bool, str | None, str | None]:
    """Create a subscription Checkout session for this phone number.

    The phone travels in metadata + client_reference_id so the webhook can find the
    subscriber when payment completes. Returns (ok, checkout_url, error).
    """
    if not is_enabled():
        return False, None, "Payments are not enabled yet."

    # Stripe wants nested form fields as line_items[0][price] etc.
    data = {
        "mode": "subscription",
        "line_items[0][price]": settings.stripe_price_id,
        "line_items[0][quantity]": "1",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": phone,
        "metadata[phone]": phone,
        "subscription_data[metadata][phone]": phone,
        # Let Stripe collect the email for the receipt; we only store the phone.
        "allow_promotion_codes": "true",
    }
    ok, body = _post("checkout/sessions", data)
    if not ok:
        return False, None, body.get("error", {}).get("message", "Stripe error")
    return True, body.get("url"), None


def create_billing_portal(customer_id: str, return_url: str) -> tuple[bool, str | None, str | None]:
    """A link where a Pro member can manage or cancel their subscription."""
    if not is_enabled():
        return False, None, "Payments are not enabled."
    ok, body = _post("billing_portal/sessions",
                     {"customer": customer_id, "return_url": return_url})
    if not ok:
        return False, None, body.get("error", {}).get("message", "Stripe error")
    return True, body.get("url"), None


def verify_webhook(payload: bytes, sig_header: str) -> dict | None:
    """Verify a Stripe webhook signature and return the parsed event, or None.

    Stripe signs with a scheme like: t=timestamp,v1=hexdigest. We recompute the
    HMAC-SHA256 over "timestamp.payload" and compare. Without this, anyone could
    POST a fake "payment succeeded" and get Pro for free.
    """
    secret = settings.stripe_webhook_secret
    if not secret or not sig_header:
        return None

    parts = {}
    for piece in sig_header.split(","):
        if "=" in piece:
            k, v = piece.split("=", 1)
            parts.setdefault(k, v)

    timestamp = parts.get("t")
    signature = parts.get("v1")
    if not timestamp or not signature:
        return None

    # Reject events older than 5 minutes (replay protection).
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return None
    except ValueError:
        return None

    signed = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None

    try:
        return json.loads(payload)
    except Exception:
        return None
