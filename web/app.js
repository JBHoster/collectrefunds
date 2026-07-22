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
      <span class="opp-rank">No. 0${i+1}</span>${isTop ? '<span class="prize-tag">Largest payout</span>' : ''}
      <h3>${esc(p.name)}</h3><div class="co">${esc(p.company || '')} · ${esc(p.category_label || '')}</div>
      <div class="opp-pay">${pay}</div>
      <p class="blurb">${esc((p.summary || '').slice(0, 110))}</p>
      <div class="opp-foot">${dl}<span class="opp-go">Claim →</span></div></a>`;
  }).join('');
}

/* ---------------------------------------------------------------- list + filters */
let filter = 'all';
const matches = p => filter === 'free' ? p.auto
  : filter === 'noproof' ? !p.proof_required
  : filter === 'soon' ? (p.days_left != null && p.days_left <= 30)
  : filter === 'big' ? (p.payout && p.payout >= 50) : true;

function renderList() {
  const box = $('#list');
  if (!box) return;
  const rows = PROGRAMS.filter(matches).sort((a, b) => (a.days_left ?? 999) - (b.days_left ?? 999));
  box.innerHTML = rows.map(p => {
    const urg = p.days_left != null && p.days_left <= 30;
    const pay = p.payout ? `<b>$${p.payout}</b>` : `<b>Auto</b>`;
    const pills = [
      p.payout && p.payout >= 50 ? '<span class="tag big">$50+</span>' : '',
      p.auto ? '<span class="tag free">No claim</span>' : '',
      p.proof_required ? '<span class="tag proof">Receipt</span>' : ''
    ].join('');
    return `<a class="row ${urg ? 'closing-row' : ''}" href="/programs/${encodeURIComponent(p.slug)}">
      <div class="row-main">
        <div class="row-top"><span class="row-name">${esc(p.name)}</span>${pills}</div>
        <div class="row-blurb">${esc(p.summary || '')}</div>
        <div class="row-meter ${meterCls(p.days_left)}"><i style="width:${meterPct(p.days_left)}%"></i></div>
      </div>
      <div class="row-pay">${pay}<span class="${urg ? 'urgent' : 'ok'}">${deadlineTxt(p.days_left)}</span></div>
    </a>`;
  }).join('') || '<p style="color:var(--ink-3);text-align:center;padding:36px">No refunds match that filter.</p>';
}

$('#filterbar')?.addEventListener('click', e => {
  const c = e.target.closest('.chip');
  if (!c) return;
  $$('.chip').forEach(x => x.classList.remove('on'));
  c.classList.add('on');
  filter = c.dataset.f;
  renderList();
});

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
function say(msg, kind = '') { if (statusEl) { statusEl.textContent = msg; statusEl.className = 'form-status ' + kind; } }

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
    else if (data.status === 'updated') say(data.message, 'ok');
    else {
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
    if (res.ok) { $('#step-code').hidden = true; say(data.message, 'ok'); }
    else say(data.detail || 'That code didn\'t match.', 'bad');
  } catch { say('Network problem. Try again.', 'bad'); }
});

$('#restart')?.addEventListener('click', () => {
  $('#step-code').hidden = true; $('#step-phone').hidden = false; say('');
});

$('#get-pro')?.addEventListener('click', async () => {
  const btn = $('#get-pro');
  const phone = prompt(
    "Enter the mobile number you use for CollectRefunds alerts.\n\n" +
    "(New here? Set up free alerts first, then upgrade to Pro — it takes a few seconds.)"
  );
  if (!phone) return;
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = 'Opening checkout…';
  try {
    const res = await fetch('/api/checkout', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone })
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.url) {
      window.location.href = data.url;           // Stripe hosted checkout
    } else if (res.status === 503) {
      alert("Pro isn't switched on yet — it's coming very soon. For now, free text alerts work great.");
    } else {
      alert(data.detail || "Couldn't start checkout. Make sure you've confirmed your number for free alerts first.");
    }
  } catch {
    alert('Network problem. Please try again.');
  } finally {
    btn.disabled = false; btn.textContent = original;
  }
});

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

/* ---------------------------------------------------------------- boot */
const big = $('#bignum');
if (big && big.dataset.target) countUp(big, Number(big.dataset.target));
startCountdown();
renderBest();
renderUpcoming();
renderList();
connectLive();
