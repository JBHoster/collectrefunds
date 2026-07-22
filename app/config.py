"""Application settings. Everything is env-overridable so prod differs only by .env."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- core ---
    site_name: str = "CollectRefunds"
    base_url: str = "http://localhost:8000"   # used in emails + sitemap; no trailing slash
    secret_key: str = "change-me"             # signs email confirmation links
    environment: str = "development"          # development | production
    https_only: bool = False

    # SQLite by default; point at Postgres in prod:
    #   postgresql+psycopg2://user:pass@host/claimwatch
    database_url: str = "sqlite:///./claimwatch.db"

    # --- ingest ---
    # Only used when RUN_SCHEDULER_IN_WEB=true (dev). In production the worker
    # container owns ingest and the web process stays stateless.
    ingest_interval_minutes: int = 180
    run_scheduler_in_web: bool = True
    user_agent: str = "CollectRefunds/1.0 (+http://localhost:8000/about; you@example.com)"

    # Programs extracted below this confidence are held for human review.
    review_threshold: float = 0.6

    # --- admin ---
    admin_username: str = "admin"
    admin_password: str = ""       # unset = admin endpoints return 503

    # --- SMS (the only notification channel) ---
    # Blank account SID = messages print to stdout instead of sending, so the whole
    # opt-in flow is testable with no Twilio account and no spend.
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""            # or use a Messaging Service instead
    twilio_messaging_service_sid: str = ""

    # --- Stripe (Pro subscription payments) ---
    # Blank secret key = the Pro button shows "coming soon" instead of charging, so
    # the site runs fine with no Stripe account. Fill these in to take real payments.
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_price_id: str = ""               # the $4/mo recurring price, from Stripe
    stripe_webhook_secret: str = ""         # verifies webhook calls really came from Stripe
    pro_price_display: str = "$4/mo"        # shown on the site

    # Hard limits, enforced in code. SMS mistakes are expensive and irreversible.
    max_sms_per_day: int = 3                # per subscriber
    send_window_start_utc: int = 14         # ~9am US Eastern
    send_window_end_utc: int = 1            # ~8pm US Eastern (window wraps midnight)

    contact_email: str = "hello@example.com"

    # How often each live (SSE) connection checks for new data.
    live_poll_seconds: int = 10

    # --- optional ---
    anthropic_api_key: str = ""


settings = Settings()
