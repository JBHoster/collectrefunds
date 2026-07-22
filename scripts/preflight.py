"""Pre-deployment checks.

Run `make deploy-check` before shipping. Every check here corresponds to a way this
specific app can go live broken or unsafe. Exits non-zero on any failure so it can gate
a deploy pipeline.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

FAIL, WARN = [], []


def fail(msg):
    FAIL.append(msg)


def warn(msg):
    WARN.append(msg)


prod = settings.environment == "production"

# --- secrets ---------------------------------------------------------------
if settings.secret_key in ("", "change-me"):
    fail("SECRET_KEY is unset. Run 'make secrets' and paste the two lines "
         "it prints into your .env file.")
elif len(settings.secret_key) < 32:
    fail("SECRET_KEY is too short — use at least 32 characters.")

if prod and not settings.admin_password:
    warn("ADMIN_PASSWORD is unset. /admin will return 503 and you won't be able "
         "to clear the review queue.")
elif settings.admin_password and len(settings.admin_password) < 12:
    fail("ADMIN_PASSWORD is under 12 characters. It's HTTP Basic on the open "
         "internet — make it long.")

# --- urls ------------------------------------------------------------------
if prod:
    if "localhost" in settings.base_url or settings.base_url.startswith("http://"):
        fail(f"BASE_URL is {settings.base_url!r}. In production it must be your real "
             "https:// domain — it's baked into confirmation links and the sitemap.")
    if settings.base_url.endswith("/"):
        fail("BASE_URL must not have a trailing slash; URLs will end up doubled.")
    if not settings.https_only:
        warn("HTTPS_ONLY is false: HSTS won't be sent.")

# --- database --------------------------------------------------------------
if prod and settings.database_url.startswith("sqlite"):
    warn("Using SQLite in production. Fine for one small instance, but it rules out "
         "multiple web containers and most managed backups. Prefer Postgres.")

# --- SMS -------------------------------------------------------------------
if prod and not settings.twilio_account_sid:
    fail("TWILIO_ACCOUNT_SID is unset in production: confirmation codes will print "
         "to the log instead of sending, so nobody can ever activate alerts.")
if settings.twilio_account_sid:
    if not settings.twilio_auth_token:
        fail("TWILIO_AUTH_TOKEN is unset. Inbound webhooks can't be verified, so "
             "anyone who finds /sms/inbound could forge a STOP for someone else.")
    if not (settings.twilio_from_number or settings.twilio_messaging_service_sid):
        fail("Set TWILIO_FROM_NUMBER or TWILIO_MESSAGING_SERVICE_SID.")
if "example.com" in settings.contact_email:
    fail("CONTACT_EMAIL still points at example.com. Carriers require a working "
         "support contact, and it's quoted in every HELP reply.")
if settings.max_sms_per_day > 5:
    warn(f"MAX_SMS_PER_DAY is {settings.max_sms_per_day}. Above ~5/day you'll drive "
         "opt-outs and spam complaints faster than signups.")

# --- scraper etiquette -----------------------------------------------------
if "example.com" in settings.user_agent or "localhost" in settings.user_agent:
    fail("USER_AGENT still has a placeholder. Government sites block anonymous "
         "scrapers — put a real contact URL and email in it.")

# --- scheduling ------------------------------------------------------------
if prod and settings.run_scheduler_in_web:
    warn("RUN_SCHEDULER_IN_WEB is true in production. Every web worker will run its "
         "own ingest, duplicating scrapes. Set it false and run `python -m app.worker`.")

if settings.ingest_interval_minutes < 30:
    warn(f"Ingest every {settings.ingest_interval_minutes} min is aggressive for a "
         "source that changes a few times a week. 180 is plenty.")

# --- legal copy ------------------------------------------------------------
try:
    from app.content import PAGES
    if "example.com" in PAGES["privacy"]["content"]:
        warn("Privacy policy still references example.com.")
except Exception as e:  # pragma: no cover
    fail(f"Could not load legal pages: {e}")

# --- report ----------------------------------------------------------------
for w in WARN:
    print(f"WARN  {w}\n")
for f in FAIL:
    print(f"FAIL  {f}\n")

if FAIL:
    print(f"{len(FAIL)} blocking issue(s). Not safe to deploy.")
    sys.exit(1)

print(f"Preflight passed{f' with {len(WARN)} warning(s)' if WARN else ''}.")
print("\nStill to do by hand before launch:")
print("  - Point DNS at the host and confirm TLS is live")
print("  - Register your A2P 10DLC campaign in Twilio. US carriers filter or block")
print("    unregistered application-to-person traffic. Allow several days.")
print("  - Point the number's inbound webhook at POST /sms/inbound")
print("  - Text yourself the whole flow: signup, code, alert, STOP, then START")
print("  - Submit the sitemap in Google Search Console")
print("  - Have a lawyer read /terms, /privacy and /sms")
