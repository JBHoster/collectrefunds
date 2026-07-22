# CollectRefunds

**A website that shows every open US federal refund program, lets people filter down to
the ones that might apply to them, and texts them when a new one opens.**

No accounts. No login. No email. Claims are always filed on the official government site —
this site never takes a claim and never charges a fee.

**To try it right now:** run `make start` and open http://localhost:8000. That's it —
no accounts or setup needed, and text messages print to your screen so it's free to test.
The full guide is further down.

The homepage leads with the total dollars unclaimed (counting up on load), a live countdown
on the soonest-closing refund, a ranked "best opportunities" rail, and a free/$4-Pro pricing
section. The Pro tier is priced under what a single refund pays back — the fairness rule that
keeps people happy about paying.

---

## What it does, in plain terms

**1. It watches the government.** Every three hours it reads the FTC's official refund
page, pulls out each program's deadline, payout, and claim link, and notices anything that
changed since last time.

**2. It shows people what's open.** The home page is a list of every active program with a
bar that drains as the deadline approaches. On the left is a filter panel — payout size,
time left, whether you have to file a claim or get paid automatically, whether you need a
receipt, and the category. Clicking a filter is instant, because the filtering happens in
the browser.

**3. It updates itself while you're looking at it.** If a new program appears while
someone has the page open, it slides in without a refresh, and the counters at the top
update. The dot in the corner shows the connection is live.

**4. It texts people about new ones.** The only subscription is SMS. Someone enters their
mobile number, picks their filters, gets a 6-digit code by text, and replies with it.
After that they get a short text whenever a new program matches *their* filters — capped
at 3 a day, never overnight. Reply STOP and it stops immediately.

---

## The five things worth understanding

**Why filters instead of accounts.** Asking people to make an account to check whether
they're owed $50 loses almost all of them. Filters give the same answer in one click and
there's nothing to secure, breach, or reset.

**Why filtering happens in the browser.** The whole dataset — a few hundred programs — is
sent with the page. Every filter click is instant with no server round-trip. The same
filters also exist as an API, so search engines and scripts get the same results.

**Why text instead of email.** Email alerts compete with hundreds of other emails and land
in spam. A text gets read. The tradeoff is that texts cost money per message and are much
more heavily regulated, which is why the code enforces confirmed opt-in, a daily cap, and
quiet hours rather than leaving those to policy.

**Why some programs get held back.** The scraper scores its own confidence when it reads a
page. Anything under 0.6 is hidden from the public list and never texted about until you
approve it at `/admin`. A wrong deadline is the one mistake that actually costs a user
money.

**Why the health check goes red when the scraper stops.** The dangerous failure isn't the
site going down. It's the site staying up while showing deadlines that expired weeks ago.
`/healthz` returns 503 if the last scrape failed or is more than 26 hours old.

---

## Setup guide

### Part 1 — Run it on your computer (one command, costs nothing)

You do **not** need a Twilio account, a domain, or a server for this part. Text messages
print to your screen instead of sending, so you can try the whole thing for free.

```bash
make start
```

That's the whole setup. It installs what it needs, builds the database, adds example
programs, and opens the site at **http://localhost:8000**. Run it again any time — it's
safe to repeat.

Try it: click some filters, then sign up for alerts with any US phone number. The
confirmation code **prints in the same window** where you ran `make start`, not to your
phone. Type it into the site to confirm.

The handful of other commands, if you want them:

| Command | What it does |
| --- | --- |
| `make start` | Set up and run the site (start here) |
| `make secrets` | Generate the two passwords you'll need to go live |
| `make ingest` | Pull real, current programs from the FTC |
| `make test` | Run the tests |
| `make go-live` | Check your settings and launch the live site |
| `make help` | List these commands |

### Part 2 — Get a phone number (do this early)

US carriers require registration before a business can send automated texts. **This takes
a few days to approve, so start it before you need it.**

1. Make a Twilio account and buy a phone number (about $1/month).
2. In the Twilio console, create a **Messaging Service** and add your number to it.
3. Complete **A2P 10DLC registration**. Twilio walks you through it. You'll need your
   business details and a link to your text-alert terms page — that's the `/sms` page
   this project already generates for you.
4. Once approved, copy four values into your `.env`:

```
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_MESSAGING_SERVICE_SID=MG...
CONTACT_EMAIL=you@yourdomain.com
```

5. In Twilio, set the number's **inbound webhook** to
   `https://yourdomain.com/sms/inbound`. This is what makes STOP, HELP, and START work.
   Without it you're legally exposed.

Texts cost roughly **$0.008 each** to send in the US. A thousand subscribers getting four
alerts a month is about $32/month.

### Part 3 — Put it online

**The simple way — one server, about $12/month.**

Get any Ubuntu box (DigitalOcean, Hetzner, and Linode all work), and point your domain's
DNS A record at its IP address. Then:

```bash
git clone <your repo> && cd claimwatch
cp .env.example .env
make secrets              # prints your two passwords — paste them into .env
nano .env                 # fill in the rest (see below)

make go-live              # checks everything, then launches with HTTPS
```

In `.env`, the lines to fill in are:

```
ENVIRONMENT=production
BASE_URL=https://yourdomain.com
SECRET_KEY=              ← paste from "make secrets"
ADMIN_PASSWORD=          ← paste from "make secrets"
CONTACT_EMAIL=you@yourdomain.com
USER_AGENT=CollectRefunds/2.0 (+https://yourdomain.com/about; you@yourdomain.com)
SITE_DOMAIN=yourdomain.com
TWILIO_ACCOUNT_SID=...   ← from Part 2
TWILIO_AUTH_TOKEN=...
TWILIO_MESSAGING_SERVICE_SID=...
```

`make go-live` refuses to launch if anything important is missing, and tells you exactly
what to fix. Once it runs, HTTPS certificates are issued automatically on the first visit —
there's no certificate step to do yourself. It starts four pieces for you: the database,
the website, the scraper, and the HTTPS layer.

**The managed way (Render, Railway, Fly).** Deploy the `Dockerfile` twice off the same
image and database — once as a web service running gunicorn, once as a worker running
`python -m app.worker` — set `RUN_SCHEDULER_IN_WEB=false`, and run `alembic upgrade head`
as a release command. Health check path is `/healthz`.

### Part 4 — Turn on $4 Pro payments (Stripe)

The "Get Pro" button says "coming soon" until you add Stripe keys. To make it real:

1. Make a **Stripe** account at stripe.com.
2. Create a **Product** called "Pro alerts" with a **recurring price** of $4/month. Copy its price ID (`price_...`).
3. In **Developers → API keys**, copy your secret key (`sk_...`).
4. In **Developers → Webhooks**, add an endpoint at `https://yourdomain.com/stripe/webhook`, subscribe it to `checkout.session.completed` and `customer.subscription.deleted`, and copy the signing secret (`whsec_...`).
5. Put all four into `.env` (or Render's environment settings):

```
STRIPE_SECRET_KEY=sk_...
STRIPE_PRICE_ID=price_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PUBLISHABLE_KEY=pk_...
```

The Pro button now runs real Stripe Checkout. Cards are handled entirely on Stripe's hosted page — your server never sees card numbers. Test with card `4242 4242 4242 4242` first.

**How it works:** someone signs up for free alerts (confirming their phone), clicks Get Pro, and pays. The signed webhook flips them to Pro — raising their alert cap and unlocking unlimited follows. Cancelling via Stripe's billing portal ends Pro automatically.

### Part 5 — Before you tell anyone about it

Run `make deploy-check` first; it catches the mechanical problems. Then, by hand:

- [ ] Visit the site over HTTPS and confirm the padlock
- [ ] **Text yourself the whole flow**: sign up, get the code, confirm, reply STOP, reply START
- [ ] Confirm STOP actually worked (check `/admin` — the subscriber count should drop)
- [ ] Run `make ingest-full` once and check the programs look right
- [ ] Visit `/healthz` and confirm it says ok
- [ ] Point an uptime monitor at `/healthz`
- [ ] Submit `https://yourdomain.com/sitemap.xml` in Google Search Console
- [ ] **Have a lawyer read `/terms`, `/privacy` and `/sms`**

That last one isn't boilerplate caution. Text-message rules in the US carry statutory
damages **per message**, so a mistake at scale gets expensive quickly. The code is built to
keep you on the right side of it — confirmed opt-in, STOP handling, consent records, a
daily cap — but a lawyer should confirm the wording matches how you actually operate.

---

## Running it day to day

Almost nothing to do. The scraper runs every three hours on its own.

**`/admin`** (username `admin`, the password you set) shows subscriber count, scrape
history, failed texts, and the review queue. The review queue is the only thing that ever
needs your attention: it holds programs the scraper wasn't confident about. You click
approve or reject after checking the source link. Usually empty.

**If the site stops updating**, `/healthz` goes red and tells you why. The most likely
cause is the FTC changing its page layout, which shows up as a scrape error in `/admin`.

---

## Where the money is (if you want it to be)

Consumer traffic here monetizes badly — don't plan on ads. The realistic paths are a data
feed sold to law firms, and securities-claim filing for institutional investors, which is
a genuinely large business hiding inside this same pipeline.

The growth engine is search. Every program gets its own page that works without
JavaScript, because people search "amazon settlement claim" and those pages need to be
findable.

---

## Adding more sources

Right now it tracks the FTC. Each new source is one file in `app/ingest/` with a `fetch()`
function; change detection, alerts, filters, SEO pages, and the sitemap all work
automatically once it's registered in `app/ingest/run.py`.

Worth adding, in order of value:

1. **Claims administrators** — Epiq, JND, Rust, Simpluris. Roughly 10x more programs,
   including private class actions that never touch a government page.
2. **CourtListener** — federal court dockets, so you catch settlements at approval.
3. **State attorney general** settlements and **CFPB** relief funds.
4. **SEC Fair Funds** — the securities side.

---

## The rules this is built around

1. **Never host a claim form.** Link to the official site, always. Collecting claims puts
   you in a regulated business.
2. **Never charge to file.** Filing is free. Charging for it is what the scams do.
3. **Facts, not copy.** Dates and dollar figures aren't copyrightable; agency prose is.
   The summaries are generated, not copied.
4. **Don't tell anyone they qualify.** Show the official criteria and let the administrator
   decide. "This may apply to you" is the strongest honest claim.

---

## Project layout

```
app/
  main.py          website, API, live updates, SMS signup
  models.py        database tables
  sms.py           Twilio + message wording + STOP handling
  notify.py        decides who gets texted about what
  ingest/ftc.py    reads the FTC website
  ingest/base.py   change detection, deadline sweeps
  content.py       about / privacy / terms / text-alert terms
templates/         the pages
web/               stylesheet + browser code
tests/             30 tests
scripts/preflight.py   the "am I safe to launch" check
```
