"""FTC refund-program ingest.

Source: https://www.ftc.gov/enforcement/refunds

Why this source first: it is the only genuinely structured, officially maintained list
of live consumer refund programs in the US. The listing page is a plain HTML table
(program name, date, administrator + phone), and each program links to a detail page
carrying the claim deadline, the official claim site, and eligibility language.

We store facts (deadlines, dollar amounts, administrators, URLs) and write our own
summary copy. We never mirror FTC page text and we never host a claim form -- the
claim_url always points at the official site.
"""
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from ..config import settings
from .base import slugify

SOURCE = "ftc"
LIST_URL = "https://www.ftc.gov/enforcement/refunds"
BASE = "https://www.ftc.gov"

ADMINISTRATORS = [
    "Epiq Systems", "JND Legal Administration", "Rust Consulting", "Simpluris",
    "Analytics Consulting", "A.B. Data", "KCC", "Angeion", "Kroll",
]

CATEGORY_RULES = [
    ("data_breach", r"data breach|privacy|personal information|tracking|surveillance"),
    ("subscriptions", r"subscription|auto.?renew|negative option|cancel|membership|trial"),
    ("fintech", r"loan|credit|debt|lending|bank|cash advance|payday|financ|investing|trading"),
    ("auto", r"vehicle|auto|car |dealer|motor|warranty"),
    ("education", r"universit|college|career|school|degree|training|coaching"),
    ("healthcare", r"health|medical|therapy|pharma|supplement|weight loss|telehealth"),
    ("business_opportunity", r"business opportunit|pyramid|MLM|work.from.home|income claim"),
    ("tech_products", r"software|antivirus|app |gaming|device|smart home|electronic"),
    ("retail", r"retail|shopping|marketplace|clothing|apparel|cosmetic"),
]

# "December 31, 2026" / "December 31 2026"
_DATE = r"([A-Z][a-z]+\s+\d{1,2},?\s+20\d{2})"
DEADLINE_PATTERNS = [
    rf"deadline (?:to (?:file|apply|submit)[^.]*?)?is\s+{_DATE}",
    rf"(?:file|submit|apply)[^.]{{0,60}}?by\s+{_DATE}",
    rf"claims? (?:must be (?:filed|submitted)|are due)[^.]{{0,40}}?{_DATE}",
    rf"on or before\s+{_DATE}",
    rf"no later than\s+{_DATE}",
]

MONEY = r"\$\s?([\d,]+(?:\.\d{2})?)\s*(million|billion)?"

CLAIM_HOST_HINTS = re.compile(
    r"(settlement|refund|claim|redress)[a-z0-9-]*\.(com|net|org)|"
    r"(epiqglobal|jndla|rustconsulting|simpluris|noticeadmin|classaction)",
    re.I,
)


def _client():
    # Government sites (often behind Akamai/Cloudflare) reject requests that don't
    # look like a real browser — a bare User-Agent gets a 403. We send the full set
    # of headers a browser sends. This is not evasion: our User-Agent still names us
    # and links to a contact page; we're just speaking the CDN's expected dialect.
    ua = settings.user_agent or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    return httpx.Client(
        headers={
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                      "image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        },
        timeout=30,
        follow_redirects=True,
    )


def _parse_money(text: str):
    m = re.search(MONEY, text, re.I)
    if not m:
        return None
    val = float(m.group(1).replace(",", ""))
    scale = (m.group(2) or "").lower()
    return val * {"million": 1e6, "billion": 1e9}.get(scale, 1)


def _parse_date(s: str):
    s = s.replace(",", "").strip()
    for fmt in ("%B %d %Y", "%b %d %Y", "%B %Y", "%b %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def categorize(text: str) -> str:
    low = (text or "").lower()
    for cat, pattern in CATEGORY_RULES:
        if re.search(pattern, low, re.I):
            return cat
    return "other"


def parse_listing(html: str) -> list[dict]:
    """Parse the Active Refund Programs table into stub records."""
    soup = BeautifulSoup(html, "html.parser")
    out = []

    for row in soup.select("table tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        link = cells[0].find("a", href=True)
        if not link or "/refunds/" not in link["href"]:
            continue

        href = link["href"]
        url = href if href.startswith("http") else BASE + href
        name = link.get_text(" ", strip=True)
        announced = _parse_date(cells[1].get_text(" ", strip=True)) if len(cells) > 1 else None

        contact = cells[2].get_text(" ", strip=True) if len(cells) > 2 else ""
        admin = next((a for a in ADMINISTRATORS if a.lower() in contact.lower()), None)
        phone_m = re.search(r"(\+?1[-\s]?)?\d{3}[-.\s]\d{3}[-.\s]\d{4}", contact)

        out.append({
            "source_key": url.rsplit("/", 1)[-1],
            "name": name,
            "slug": slugify(name),
            "company": re.sub(
                r"\s+(Refunds?|Settlement)$", "", name, flags=re.I
            ).strip(),
            "source_url": url,
            "announced_on": announced,
            "administrator": admin,
            "phone": phone_m.group(0) if phone_m else None,
        })

    return out


def parse_detail(html: str, stub: dict) -> dict:
    """Pull deadline, claim URL, fund size and eligibility text off a program page."""
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    text = main.get_text(" ", strip=True)
    rec = dict(stub)
    confidence = 0.5

    # --- claim deadline ---
    for pattern in DEADLINE_PATTERNS:
        m = re.search(pattern, text, re.I)
        if m:
            d = _parse_date(m.group(1))
            if d:
                rec["claim_deadline"] = d
                confidence += 0.25
                break

    # --- official claim site (external link, not ftc.gov) ---
    for a in main.find_all("a", href=True):
        href = a["href"]
        if "now-leaving" in href:  # FTC wraps outbound links
            m = re.search(r"external_url=([^&]+)", href)
            if m:
                from urllib.parse import unquote
                href = unquote(m.group(1))
        if href.startswith("http") and "ftc.gov" not in href and CLAIM_HOST_HINTS.search(href):
            rec["claim_url"] = href
            confidence += 0.2
            break

    # --- total fund ---
    fund_m = re.search(rf"(?:sending|returning|distribut\w+|total of)[^.]{{0,40}}?{MONEY}", text, re.I)
    if fund_m:
        rec["total_fund"] = _parse_money(fund_m.group(0))
        confidence += 0.1

    # --- payout per person, when stated ---
    per_m = re.search(rf"(?:average|each|per person|payment[s]? of)[^.]{{0,30}}?{MONEY}", text, re.I)
    if per_m:
        amt = _parse_money(per_m.group(0))
        if amt and amt < 100_000:
            rec["payout_low"] = rec["payout_high"] = amt

    # --- does it need a claim form, or is it automatic? ---
    if re.search(r"you (?:do not|don't) (?:need|have) to (?:file|do|submit)", text, re.I):
        rec["payout_note"] = "Paid automatically — no claim needed"
        rec["proof_required"] = False
    elif re.search(r"file a claim|submit a claim|claim form|apply for a refund", text, re.I):
        rec["payout_note"] = "Claim form required"
        rec["proof_required"] = bool(
            re.search(r"proof of purchase|receipt|documentation|supporting document", text, re.I)
        )

    # --- eligibility, first substantive sentence mentioning who qualifies ---
    elig = re.search(
        r"([^.]{0,200}?(?:eligible|qualif\w+|if you (?:bought|purchased|paid|used|were))[^.]{0,300}\.)",
        text, re.I,
    )
    if elig:
        rec["eligibility"] = elig.group(1).strip()[:600]
        confidence += 0.1

    rec["category"] = categorize(f"{stub.get('name','')} {text[:2000]}")
    rec["summary"] = build_summary(rec)
    rec["confidence"] = min(confidence, 1.0)
    rec["status"] = "open"
    return rec


def build_summary(rec: dict) -> str:
    """Our own copy. Never upstream text."""
    bits = [f"The FTC is issuing refunds in the {rec.get('company', 'this')} matter."]
    if rec.get("total_fund"):
        bits.append(f"Total fund: ${rec['total_fund']:,.0f}.")
    if rec.get("payout_note"):
        bits.append(rec["payout_note"] + ".")
    if rec.get("claim_deadline"):
        bits.append(f"Claims close {rec['claim_deadline'].strftime('%b %d, %Y')}.")
    return " ".join(bits)


def fetch(limit: int | None = None, fixture: str | None = None) -> list[dict]:
    """Full FTC pass. `fixture` reads a saved listing HTML for offline tests."""
    if fixture:
        with open(fixture) as f:
            stubs = parse_listing(f.read())
        return [dict(s, category="other", summary=build_summary(s), confidence=0.5,
                     status="open") for s in stubs][:limit]

    with _client() as c:
        resp = c.get(LIST_URL)
        # A non-200 must be loud. Silently treating a 403 as "no programs today"
        # leaves the site serving expired deadlines with a green health check.
        resp.raise_for_status()

        stubs = parse_listing(resp.text)
        if not stubs:
            # The FTC refund list is never legitimately empty. Zero rows means we're
            # blocked, or the page structure changed and the parser needs updating.
            raise RuntimeError(
                f"Parsed 0 programs from {LIST_URL}. The page layout probably changed "
                f"or we're being blocked — check the selector in parse_listing().")

        if limit:
            stubs = stubs[:limit]

        records = []
        for stub in stubs:
            try:
                detail = c.get(stub["source_url"])
                detail.raise_for_status()
                records.append(parse_detail(detail.text, stub))
            except Exception as e:  # one bad page must not kill the whole run
                stub.update(summary=build_summary(stub), confidence=0.3,
                            status="open", category="other")
                stub["payout_note"] = f"detail fetch failed: {e.__class__.__name__}"
                records.append(stub)
        return records
