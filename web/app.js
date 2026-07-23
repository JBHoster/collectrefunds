/* CollectRefunds front end.
   Renders the homepage from server-bootstrapped data, runs the live countdown and
   count-up, filters client-side for instant response, streams live updates over SSE,
   and drives the SMS opt-in flow. */

const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const parse = id => { try { return JSON.parse(document.getElementById(id).textContent); } catch { return null; } };

let PROGRAMS = parse('bootstrap') || [];
let BEST = parse('best-data') || [];
const SOONEST = parse('soonest-data');
const UPCOMING = parse('upcoming-data') || [];

/* ---------------------------------------------------------------- formatting */
const fmtBig = n => n >= 1e9 ? '$' + (n/1e9).toFixed(2) + 'B'
  : n >= 1e6 ? '$' + (n/1e6).toFixed(1) + 'M'
  : '$' + Math.round(n).toLocaleString();
const deadlineTxt = d => d == null ? 'No deadline' : d <= 0 ? 'Closes today'
  : d === 1 ? '1 day left' : d + ' days left';
const meterPct = d => d == null ? 100 : Math.max(5, Math.min(100, d / 180 * 100));
const meterCls = d => d == null ? '' : d <= 7 ? 'crit' : d <= 30 ? 'soon' : '';
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));

/* ---------------------------------------------------------------- count-up */
function countUp(el, target, dur = 1500) {
  if (!el || !target) return;
  const start = performance.now();
  (function tick(now) {
    const p = Math.min(1, (now - start) / dur);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = fmtBig(target * eased);
    if (p < 1) requestAnimationFrame(tick);
    else el.textContent = fmtBig(target);
  })(start);
}

/* ---------------------------------------------------------------- countdown */
function startCountdown() {
  const box = $('#countdown');
  if (!box || !box.dataset.deadline) return;
  const target = new Date(box.dataset.deadline + 'T23:59:59');
  function render() {
    const diff = target - new Date();
    if (diff <= 0) { box.innerHTML = '<div class="cd-unit crit"><b>0</b><span>closed</span></div>'; return; }
    const d = Math.floor(diff / 864e5), h = Math.floor(diff % 864e5 / 36e5),
          m = Math.floor(diff % 36e5 / 6e4), s = Math.floor(diff % 6e4 / 1e3);
    const crit = d < 7 ? 'crit' : '';
    box.innerHTML =
      `<div class="cd-unit ${crit}"><b>${d}</b><span>days</span></div>
       <div class="cd-unit ${crit}"><b>${String(h).padStart(2,'0')}</b><span>hrs</span></div>
       <div class="cd-unit ${crit}"><b>${String(m).padStart(2,'0')}</b><span>min</span></div>
       <div class="cd-unit ${crit}"><b>${String(s).padStart(2,'0')}</b><span>sec</span></div>`;
  }
  render();
  setInterval(render, 1000);
}

/* ---------------------------------------------------------------- best cards */
function renderBest() {
  const grid = $('#bestgrid');
  if (!grid) return;
  const maxPay = Math.max(0, ...BEST.map(p => p.payout || 0));
  grid.innerHTML = BEST.map((p, i) => {
    const pay = p.payout ? `$${p.payout}<small> / person</small>` : `Auto<small> — paid to you</small>`;
    const isTop = p.payout === maxPay && maxPay > 0;
    const urg = (p.days_left != null && p.days_left <= 14);
    const dl = p.days_left == null
      ? '<span class="opp-deadline ok">Open now</span>'
      : `<span class="opp-deadline ${urg ? 'urgent' : 'ok'}">${deadlineTxt(p.days_left)}</span>`;
    return `<a class="opp ${isTop ? 'top' : ''}" href="/programs/${encodeURIComponent(p.slug)}">
      <div class="opp-head"><span class="opp-rank">No. 0${i+1}</span>${isTop ? '<span class="prize-tag">Largest payout</span>' : ''}</div>
      <h3>${esc(p.name)}</h3><div class="co">${esc(p.company || '')} · ${esc(p.category_label || '')}</div>
      <div class="opp-pay">${pay}</div>
      <p class="blurb">${esc((p.summary || '').slice(0, 110))}</p>
      <div class="opp-foot">${dl}<span class="opp-go">Claim →</span></div></a>`;
  }).join('');
}

/* ---------------------------------------------------------------- list + filters */
let filter = 'all';
const matches = p => filter === 'free' ? p.auto
  : filter === 'soon' ? (p.days_left != null && p.days_left <= 30)
  : true;   // 'all' and 'big' show everything; 'big' just re-sorts

function renderList() {
  const box = $('#list');
  if (!box) return;
  let rows = PROGRAMS.filter(matches);
  if (filter === 'big') {
    rows.sort((a, b) => (b.payout || 0) - (a.payout || 0));           // biggest first
  } else if (filter === 'soon') {
    rows.sort((a, b) => (a.days_left ?? 999) - (b.days_left ?? 999)); // soonest first
  } else {
    rows.sort((a, b) => (a.days_left ?? 999) - (b.days_left ?? 999));
  }
  box.innerHTML = rows.map(p => {
    const urg = p.days_left != null && p.days_left <= 30;
    // One plain-language tag max, so rows stay scannable for everyone.
    let tag = '';
    if (p.auto) tag = '<span class="tag free">Paid automatically</span>';
    else if (p.payout && p.payout >= 50) tag = '<span class="tag big">Big payout</span>';
    const badge = p.payout
      ? `<span class="money-badge"><b>$${p.payout}</b><em>each</em></span>`
      : `<span class="money-badge auto"><b>Auto</b><em>paid</em></span>`;
    return `<a class="row ${urg ? 'closing-row' : ''}" href="/programs/${encodeURIComponent(p.slug)}">
      ${badge}
      <div class="row-main">
        <div class="row-top"><span class="row-name">${esc(p.name)}</span>${tag}</div>
        <div class="row-blurb">${esc(p.summary || '')}</div>
        <div class="row-meter ${meterCls(p.days_left)}"><i style="width:${meterPct(p.days_left)}%"></i></div>
      </div>
      <div class="row-when"><span class="${urg ? 'urgent' : 'ok'}">${deadlineTxt(p.days_left)}</span></div>
    </a>`;
  }).join('') || '<p style="color:var(--ink-3);text-align:center;padding:36px">No refunds match that filter.</p>';
}

/* ---------------------------------------------------------------- live updates */
async function refreshData() {
  try {
    const rows = await fetch('/api/programs?limit=300').then(r => r.json());
    if (Array.isArray(rows)) { PROGRAMS = rows; renderList(); }
  } catch {}
}
function connectLive() {
  if (!window.EventSource) return;
  const es = new EventSource('/api/live');
  es.addEventListener('update', e => {
    let d = {}; try { d = JSON.parse(e.data); } catch { return; }
    if (d.version && d.version !== window.__VERSION__) {
      window.__VERSION__ = d.version;
      refreshData();
    }
  });
}

/* ---------------------------------------------------------------- SMS opt-in */
const form = $('#sms-form');
const statusEl = $('#sms-status');
let pendingPhone = '';
let followSlug = '';       // set when someone clicks "Notify me" on an upcoming program
let selectedPlan = 'free'; // 'free' or 'pro' — chosen via the toggle or tier buttons
function say(msg, kind = '') { if (statusEl) { statusEl.textContent = msg; statusEl.className = 'form-status ' + kind; } }

// ---- plan toggle (Free vs Pro) ----
function setPlan(plan) {
  selectedPlan = plan === 'pro' ? 'pro' : 'free';
  $$('.pt-opt').forEach(o => o.classList.toggle('on', o.dataset.plan === selectedPlan));
  const note = $('#pro-note');
  const submit = $('#sms-submit');
  if (selectedPlan === 'pro') {
    if (note) note.hidden = false;
    if (submit) submit.textContent = 'Continue to Pro →';
    const t = $('#signup-title'); if (t) t.textContent = 'Get instant Pro alerts.';
  } else {
    if (note) note.hidden = true;
    if (submit) submit.textContent = 'Text me new refunds';
    const t = $('#signup-title'); if (t) t.textContent = 'Get a text when a refund opens.';
  }
}
$('#plan-toggle')?.addEventListener('click', e => {
  const opt = e.target.closest('.pt-opt'); if (!opt) return;
  setPlan(opt.dataset.plan);
});

// tier buttons in the "How alerts work" section jump to signup with that plan chosen
$$('.tier-btn').forEach(b => b.addEventListener('click', () => {
  setPlan(b.dataset.plan);
  document.getElementById('alerts').scrollIntoView({ behavior: 'smooth' });
  setTimeout(() => $('#phone')?.focus(), 500);
}));

// ---- signup submit: confirm number first (free); Pro continues to checkout after ----
form?.addEventListener('submit', async e => {
  e.preventDefault();
  if (!$('#consent').checked) { say('Please tick the consent box so we can text you.', 'bad'); return; }
  const btn = $('#sms-submit'); btn.disabled = true; say('Sending code…');
  try {
    const res = await fetch('/api/subscribe', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        phone: $('#phone').value,
        min_payout: Number($('#f-min').value || 0),
        claim_required_only: $('#f-claim').checked,
        follow: followSlug,
        consent: true
      })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) say(data.detail || 'That didn\'t work. Check the number and try again.', 'bad');
    else if (data.status === 'updated') {
      // already verified — if they picked Pro, go straight to checkout
      if (selectedPlan === 'pro') { startProCheckout($('#phone').value); }
      else say(data.message, 'ok');
    } else {
      pendingPhone = $('#phone').value;
      $('#code-target').textContent = pendingPhone;
      $('#step-phone').hidden = true; $('#step-code').hidden = false;
      say(data.message, 'ok');
    }
  } catch { say('Network problem. Try again in a moment.', 'bad'); }
  finally { btn.disabled = false; }
});

$('#confirm-btn')?.addEventListener('click', async () => {
  say('Checking…');
  try {
    const res = await fetch('/api/confirm', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone: pendingPhone, code: $('#code').value })
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      $('#step-code').hidden = true;
      if (selectedPlan === 'pro') {
        say('Confirmed! Taking you to secure checkout…', 'ok');
        startProCheckout(pendingPhone);
      } else {
        say(data.message || 'You\'re all set — we\'ll text you.', 'ok');
      }
    } else say(data.detail || 'That code didn\'t match.', 'bad');
  } catch { say('Network problem. Try again.', 'bad'); }
});

$('#restart')?.addEventListener('click', () => {
  $('#step-code').hidden = true; $('#step-phone').hidden = false; say('');
});

// ---- Pro checkout (in-page, no browser prompt) ----
async function startProCheckout(phone) {
  try {
    const res = await fetch('/api/checkout', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone })
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.url) {
      window.location.href = data.url;            // Stripe hosted checkout
    } else if (res.status === 503) {
      say("Your free alerts are on! Pro isn't switched on quite yet — it's coming very soon.", 'ok');
    } else {
      say(data.detail || "Your alerts are set. We couldn't start Pro checkout just now.", 'bad');
    }
  } catch {
    say('Your alerts are set, but the checkout had a network issue. Try Pro again shortly.', 'bad');
  }
}

/* ---------------------------------------------------------------- upcoming funnel */
function renderUpcoming() {
  const grid = $('#upcominggrid');
  if (!grid || !UPCOMING.length) return;
  grid.innerHTML = UPCOMING.map(p => {
    const est = p.payout
      ? `$${p.payout}${p.payout_high && p.payout_high !== p.payout ? '–$' + p.payout_high : ''}`
      : (p.fund_h ? p.fund_h + ' fund' : 'Amount TBD');
    return `<div class="upc">
      <span class="upc-status">Awaiting approval</span>
      <h3>${esc(p.name)}</h3>
      <div class="co">${esc(p.company || '')} · ${esc(p.category_label || '')}</div>
      <div class="est">${est} <small>estimated</small></div>
      <div class="when">${esc(p.expected_open || 'Open date to be set')}</div>
      <p class="blurb">${esc((p.summary || '').slice(0, 130))}</p>
      <button class="upc-follow" data-slug="${esc(p.slug)}" data-name="${esc(p.name)}">
        Notify me when it opens
      </button>
    </div>`;
  }).join('');

  grid.querySelectorAll('.upc-follow').forEach(btn => {
    btn.addEventListener('click', () => {
      followSlug = btn.dataset.slug;
      // Reframe the signup so it's clearly about this specific settlement.
      const intro = document.getElementById('signup-intro');
      if (intro) intro.textContent =
        `Enter your number and we'll text you the moment "${btn.dataset.name}" opens for claims. No account needed.`;
      const eye = document.getElementById('signup-eyebrow');
      if (eye) eye.textContent = 'Get notified';
      document.getElementById('alerts').scrollIntoView({ behavior: 'smooth' });
      setTimeout(() => $('#phone')?.focus(), 500);
    });
  });
}

/* ---------------------------------------------------------------- quiz (step by step) */
// Plain, everyday yes/no questions. Each maps to a refund category in the data.
const QUIZ = [
  { q: "Do you shop on Amazon or have Prime?", cat: "subscriptions", icon: "cart" },
  { q: "Has anyone in your home played Fortnite or bought game add-ons?", cat: "tech_products", icon: "game" },
  { q: "Have you used a cash-advance or budgeting app? (like Brigit or Credit Karma)", cat: "fintech", icon: "phone" },
  { q: "Do you own a Ring doorbell or smart-home camera?", cat: "data_breach", icon: "home" },
];
const quizMatches = new Set();
let quizIndex = 0;

const ICONS = {
  cart: '<path d="M6 6h15l-1.5 9h-12z"/><circle cx="9" cy="20" r="1"/><circle cx="18" cy="20" r="1"/><path d="M6 6L5 3H2"/>',
  game: '<rect x="2" y="7" width="20" height="10" rx="4"/><path d="M7 12h3M8.5 10.5v3"/><circle cx="16" cy="11" r="1"/><circle cx="18" cy="13" r="1"/>',
  phone: '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/>',
  home: '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/><circle cx="12" cy="14" r="2"/>',
};

function renderQuizStep() {
  const body = $('#quiz-body');
  if (!body) return;
  if (quizIndex >= QUIZ.length) { finishQuiz(); return; }
  const step = QUIZ[quizIndex];
  const num = $('#quiz-step-num');
  if (num) num.textContent = `Question ${quizIndex + 1}`;
  const prog = $('#quiz-progress');
  if (prog) prog.innerHTML = `<span id="quiz-step-num">Question ${quizIndex + 1}</span> of ${QUIZ.length}`;
  body.innerHTML = `
    <div class="quiz-icon"><svg viewBox="0 0 24 24" fill="none" stroke="#E8C879" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[step.icon] || ''}</svg></div>
    <h3 class="quiz-question">${step.q}</h3>
    <div class="quiz-answers">
      <button class="quiz-ans yes" data-a="yes">Yes</button>
      <button class="quiz-ans no" data-a="no">No / not sure</button>
    </div>`;
  body.querySelectorAll('.quiz-ans').forEach(b => b.addEventListener('click', () => {
    if (b.dataset.a === 'yes') quizMatches.add(step.cat);
    quizIndex++;
    renderQuizStep();
  }));
}

function finishQuiz() {
  const tags = [...quizMatches];
  revealResults(tags.length ? tags : 'all');
}

$('#quiz-skip')?.addEventListener('click', () => revealResults('all'));

function revealResults(tags) {
  const all = tags === 'all' || !tags.length;
  const chosen = all ? PROGRAMS : PROGRAMS.filter(p => tags.includes(p.category));
  const pool = chosen.length ? chosen : PROGRAMS;   // never show empty

  const eyebrow = $('#best-eyebrow'), title = $('#best-title'), desc = $('#best-desc');
  if (!all && chosen.length) {
    if (eyebrow) eyebrow.textContent = 'Your matches';
    if (title) title.textContent = chosen.length === 1
      ? 'You may be owed money from this one'
      : `You may qualify for ${chosen.length} of these`;
    const top = Math.max(...chosen.map(p => p.payout || 0));
    if (desc) desc.textContent = top > 0
      ? `Based on your answers, here's where the money is — up to $${top} per person. Claiming is always free.`
      : "Based on your answers, here are the refunds worth checking. Claiming is always free.";
    BEST = [...chosen].sort((a, b) => (b.payout || 0) - (a.payout || 0)).slice(0, 3);
    renderBest();
  } else if (all) {
    if (eyebrow) eyebrow.textContent = 'Best opportunities';
    if (title) title.textContent = 'Where the money is right now';
  }
  document.getElementById('best').scrollIntoView({ behavior: 'smooth' });
}

renderQuizStep();

/* ---------------------------------------------------------------- boot */
const big = $('#bignum');
if (big && big.dataset.target) countUp(big, Number(big.dataset.target));
startCountdown();
renderBest();
renderUpcoming();
renderList();
connectLive();
