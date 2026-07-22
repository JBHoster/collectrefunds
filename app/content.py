"""Static page content.

Kept in Python rather than a CMS because it changes rarely and version control is the
right audit trail for legal text. THE PRIVACY AND TERMS TEXT BELOW IS A STARTING POINT
DRAFTED FOR A US-BASED, EMAIL-ONLY SERVICE. Have a lawyer review it before launch —
especially if you add accounts, payments, or serve users in the EU or California at scale.
"""
from .config import settings

UPDATED = "July 2026"

S = settings.site_name
CONTACT = settings.contact_email

PAGES = {
    "about": dict(
        eyebrow="What this is",
        heading=f"{S} tracks refunds so you don't have to.",
        blurb=("An independent tracker for official US federal refund and settlement "
               "programs, with free deadline alerts."),
        content=f"""
<p>Government agencies and courts order companies to return money to people they harmed.
That money is real, and a large share of it is never claimed — usually because the people
owed it never hear about it before the deadline passes.</p>

<p>{S} watches the official sources continuously, records every program with its deadline
and payout, and emails you when one plausibly covers something you use.</p>

<h2>What we do not do</h2>
<ul>
  <li><strong>We never file your claim.</strong> Every claim link goes to the official
  program site. We do not collect claim information and we could not file on your behalf
  even if you asked.</li>
  <li><strong>We never charge you.</strong> Filing a legitimate claim is always free.
  Anyone who asks you to pay a fee to claim a settlement is running a scam.</li>
  <li><strong>We do not decide eligibility.</strong> We summarise the published criteria.
  The administrator decides who qualifies, and their site is authoritative.</li>
  <li><strong>We are not lawyers.</strong> Nothing here is legal advice.</li>
</ul>

<h2>Only claim what's yours</h2>
<p>Claim forms are signed under penalty of perjury, and administrators do audit them.
Submitting a claim for a settlement you don't qualify for is fraud, and it also drains
the fund for the people who were actually harmed. If the criteria don't describe you,
skip it.</p>

<h2>Corrections</h2>
<p>If a deadline or payout here is wrong, tell us at
<a href="mailto:{CONTACT}">{CONTACT}</a> and we'll fix it. When our data conflicts with
the official site, the official site is right.</p>
"""),

    "sources": dict(
        eyebrow="Where the data comes from",
        heading="Sources and method",
        blurb="How CollectRefunds collects, verifies and publishes refund program data.",
        content=f"""
<h2>Current sources</h2>
<ul>
  <li><strong>FTC refund programs</strong> — the Federal Trade Commission's list of
  active refund programs, plus each program's own detail page.</li>
</ul>

<h2>Method</h2>
<p>An automated job checks each source every few hours. For each program we record the
name, the responsible administrator, the total fund, the per-person payout where it's
published, the claim deadline, and the official claim URL.</p>

<p>The extractor scores its own confidence. Anything it isn't sure about is withheld from
the public list and reviewed by a person before it appears. When a deadline or payout
changes upstream, we record the change and alert affected subscribers.</p>

<h2>Facts, not copy</h2>
<p>We record factual data — dates, dollar figures, administrators, links — and write our
own descriptions. We don't reproduce agency or administrator page text.</p>

<h2>Accuracy</h2>
<p>We aim to be accurate and we show you when each program was last checked, but we can't
guarantee it. Deadlines get extended, funds get reallocated, and pages change without
notice. Always confirm on the official site before relying on anything here.</p>

<p>Errors: <a href="mailto:{CONTACT}">{CONTACT}</a>.</p>
"""),

    "privacy": dict(
        eyebrow="Privacy",
        heading="Privacy policy",
        blurb="What CollectRefunds stores, why, and how to delete it.",
        content=f"""
<h2>The short version</h2>
<p>Browsing this site is anonymous — no account, no login, no tracking pixels. The only
personal information we ever hold is a phone number, and only if you ask for text alerts.</p>

<h2>If you sign up for text alerts</h2>
<p>We store your <strong>mobile number</strong>, the <strong>filters</strong> you chose
(payout threshold, categories, whether you want automatic payouts included), and a
timestamp and IP address recording that you consented. The consent record exists because
telecom law requires us to be able to show you opted in.</p>

<p>That's the whole list. No name, no email, no address, no payment details. We never ask
for a Social Security number and never will.</p>

<h2>Why we hold it</h2>
<p>Only to send the alerts you asked for and to honour your opt-out. Your filters decide
which programs are worth texting you about.</p>

<h2>What we never do</h2>
<ul>
  <li>We do not sell, rent, or share your phone number. Not with marketers, not with law
  firms, not with claims administrators.</li>
  <li>We do not send marketing texts. The only messages are your confirmation code, a
  welcome message, and refund alerts matching your filters.</li>
  <li>We do not run third-party advertising or analytics trackers.</li>
</ul>

<p>Your number is shared with exactly one party: the telecom provider that delivers the
message, which cannot use it for anything else.</p>

<h2>Stopping</h2>
<p>Reply <strong>STOP</strong> to any text and it stops immediately. You can also
unsubscribe on this site. We keep a minimal record that the number opted out — that record
is what guarantees we never text it again, and deleting it would defeat that. Everything
else, including your filters, is cleared.</p>

<h2>Server logs</h2>
<p>Our servers keep standard request logs (IP address, timestamp, page requested) for
security and abuse prevention, retained for 30 days.</p>

<h2>Your rights</h2>
<p>Email <a href="mailto:{CONTACT}">{CONTACT}</a> to access, correct, or delete your data.
Depending on where you live you may have additional rights under laws such as the CCPA or
GDPR; we honour those requests regardless of where you are.</p>

<h2>Children</h2>
<p>This service isn't directed at anyone under 13 and we don't knowingly collect their data.</p>

<h2>Changes</h2>
<p>Material changes get posted here. Questions: <a href="mailto:{CONTACT}">{CONTACT}</a>.</p>
"""),

    "sms": dict(
        eyebrow="Text alerts",
        heading="Text alert terms",
        blurb="How CollectRefunds text alerts work, what they cost, and how to stop them.",
        content=f"""
<h2>What you get</h2>
<p>{S} sends a text message when a new US federal refund program opens that matches the
filters you chose. That's the entire program — no marketing, no promotions, no upsells.</p>

<h2>How often</h2>
<p>Message frequency varies with how many new refund programs are announced, typically a
few messages per month. We cap it at <strong>3 messages per day</strong> no matter what,
and we don't send overnight.</p>

<h2>Cost</h2>
<p>{S} is free. <strong>Message and data rates may apply</strong> from your carrier.</p>

<h2>How to start</h2>
<p>Enter your mobile number on our home page and tick the consent box. We'll text you a
6-digit code; reply with it (or type it on the site) to confirm. Nothing else is ever sent
to your number until you confirm.</p>

<h2>How to stop</h2>
<p>Reply <strong>STOP</strong> to any message. You'll get one confirmation that you've been
unsubscribed, and nothing after that. Reply <strong>START</strong> to rejoin, or
<strong>HELP</strong> for help.</p>

<h2>Carriers</h2>
<p>Carriers are not liable for delayed or undelivered messages. Delivery isn't guaranteed —
don't rely on a text as your only reminder of a claim deadline. Check the site.</p>

<h2>Help</h2>
<p>Reply HELP to any message, or email <a href="mailto:{CONTACT}">{CONTACT}</a>.</p>

<h2>Privacy</h2>
<p>We do not sell or share your phone number. See our
<a href="/privacy">privacy policy</a>.</p>
"""),

    "terms": dict(
        eyebrow="Terms",
        heading="Terms of use",
        blurb="The terms that apply to using CollectRefunds.",
        content=f"""
<h2>What this service is</h2>
<p>{S} is an informational tracker for publicly announced refund and settlement programs.
Using it means you accept these terms.</p>

<h2>Not legal advice, not a law firm</h2>
<p>{S} is not a law firm, attorney, claims administrator, or government agency, and is not
affiliated with, endorsed by, or sponsored by any of them. Nothing on this site is legal
advice or creates an attorney-client relationship. For advice about your situation, talk
to a lawyer.</p>

<h2>We don't determine eligibility or file claims</h2>
<p>We summarise published information. Whether you qualify is decided by the program
administrator under the terms of the relevant order or settlement. We never submit a claim
for you and never handle your claim information or payment.</p>

<h2>Honest claims only</h2>
<p>You agree to use this site only to find programs you genuinely qualify for. Claim forms
are typically signed under penalty of perjury. Submitting a false claim is a crime, and
you're solely responsible for anything you file.</p>

<h2>Accuracy and availability</h2>
<p>Information is provided "as is", without warranties of any kind. Deadlines, payouts and
eligibility change, sometimes without notice, and automated extraction can make mistakes.
Always verify on the official site. We don't guarantee the service will be uninterrupted
or error-free.</p>

<h2>Limitation of liability</h2>
<p>To the fullest extent permitted by law, {S} is not liable for any indirect, incidental,
consequential or punitive damages, or for any missed deadline, rejected claim, lost payment
or lost opportunity arising from your use of the service. Our total liability for any claim
relating to the service is limited to the greater of the amount you paid us (which is
normally nothing) or $50.</p>

<h2>Text alerts</h2>
<p>If you sign up for text alerts you consent to receive recurring automated messages at
the number you provide. Consent is not a condition of using this site or of any purchase.
Message frequency varies; message and data rates may apply. Reply STOP to cancel or HELP
for help. Full terms are on the <a href="/sms">text alert terms</a> page. Delivery is not
guaranteed — do not rely on a text as your only notice of a deadline.</p>

<h2>Acceptable use</h2>
<p>Don't scrape the site at volumes that degrade it, resell the data as your own product,
attempt to breach its security, or use it to send unsolicited messages. We may restrict
access for any of these.</p>

<h2>Links out</h2>
<p>We link to official government and administrator sites. We don't control them and aren't
responsible for their content or practices.</p>

<h2>Changes and termination</h2>
<p>We may modify these terms or discontinue the service at any time. Material changes will
be posted here. Continued use after a change means you accept it.</p>

<h2>Governing law</h2>
<p>These terms are governed by the laws of the United States and the state in which the
operator is domiciled, without regard to conflict-of-law rules.</p>

<p>Questions: <a href="mailto:{CONTACT}">{CONTACT}</a>.</p>
"""),
}
