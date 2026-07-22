"""Security primitives: admin auth, signed tokens, rate limiting, headers.

Deliberately dependency-light. The rate limiter is per-process and in-memory, which
is correct for one or two web containers. If you scale past that, swap `_BUCKETS`
for Redis — the interface stays the same.
"""
import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

# --------------------------------------------------------------------- admin auth
_basic = HTTPBasic(auto_error=False)


def require_admin(creds: HTTPBasicCredentials = Depends(_basic)):
    """HTTP Basic over TLS. Good enough for a one-person admin surface.

    Refuses to run at all if the password is unset, so you can't ship with an
    accidentally-open admin panel.
    """
    if not settings.admin_password:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Admin disabled: set ADMIN_PASSWORD to enable.",
        )
    if creds is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    ok_user = hmac.compare_digest(creds.username, settings.admin_username)
    ok_pass = hmac.compare_digest(creds.password, settings.admin_password)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return creds.username


# ------------------------------------------------------------------ signed tokens
def _serializer() -> URLSafeTimedSerializer:
    if not settings.secret_key or settings.secret_key == "change-me":
        raise RuntimeError("SECRET_KEY is unset. Generate one: python -c "
                           "'import secrets;print(secrets.token_urlsafe(48))'")
    return URLSafeTimedSerializer(settings.secret_key, salt="claimwatch")


def make_token(payload: dict) -> str:
    return _serializer().dumps(payload)


def read_token(token: str, max_age: int = 60 * 60 * 48) -> dict | None:
    """Returns None on tamper or expiry. Used for email confirm links."""
    try:
        return _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


def random_token(n: int = 32) -> str:
    return secrets.token_urlsafe(n)


# ------------------------------------------------------------------ rate limiting
_BUCKETS: dict[str, deque] = defaultdict(deque)


def client_ip(request: Request) -> str:
    """Honour the proxy header, but only the first hop — the rest is user-controlled."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(key: str, limit: int, window: int):
    """FastAPI dependency factory. `limit` requests per `window` seconds per IP."""
    def dep(request: Request):
        ip = client_ip(request)
        bucket = _BUCKETS[f"{key}:{ip}"]
        now = time.time()
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            retry = int(window - (now - bucket[0])) + 1
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many requests. Slow down.",
                headers={"Retry-After": str(retry)},
            )
        bucket.append(now)
    return dep


# --------------------------------------------------------------- security headers
CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy": CSP,
}


async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    for k, v in HEADERS.items():
        response.headers.setdefault(k, v)
    if settings.https_only:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response
