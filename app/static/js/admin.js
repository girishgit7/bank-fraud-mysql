(function () {
  'use strict';
  const { api, esc, money, fmtDateTime, timeAgo, toast } = window.LW;
  const $ = (id) => document.getElementById(id);
  const state = { flags: [], status: 'open', query: '', stats: null, openId: null, opener: null };

  const level = (score) => (score >= 70 ? 'high' : score >= 50 ? 'med' : 'low');
  const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : '');
  const LABELS = { atm: 'ATM', upi: 'UPI', web: 'Web browser', online: 'Online', branch: 'Branch', card: 'Card' };
  const label = (v) => LABELS[v] || cap(v || '');
  const gauge = (score) => '<span class="gauge"><i style="width:' + Math.min(100, score) + '%"></i></span>';

  // ------------------------------------------------------------ hero + chart
  function renderHero() {
    const t = state.stats.totals;
    $('open-count').textContent = t.open_flags;
    const items = [
      ['Confirmed fraud', t.confirmed_flags],
      ['Dismissed', t.dismissed_flags],
      ['Transactions, last 24 hours', t.txns_24h],
      ['Volume, last 24 hours', money(t.volume_24h)],
    ];
    $('hero-stats').innerHTML = items
      .map((p) => '<div><dt>' + esc(p[0]) + '</dt><dd>' + esc(p[1]) + '</dd></div>')
      .join('');
  }

  function renderChart() {
    const daily = state.stats.daily;
    const box = $('chart');
    if (!daily.some((d) => d.volume > 0)) {
      box.innerHTML = '<p class="empty">No transactions in the last 14 days.</p>';
      return;
    }
    const W = 640, H = 214, padX = 6, top = 20, bottom = 38;
    const max = Math.max.apply(null, daily.map((d) => d.volume).concat([1]));
    const slot = (W - padX * 2) / daily.length;
    const bw = slot * 0.58;
    const plotH = H - top - bottom;
    let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet" aria-hidden="true">';
    svg += '<line class="axis" x1="' + padX + '" y1="' + (H - bottom) + '" x2="' + (W - padX) + '" y2="' + (H - bottom) + '"/>';
    daily.forEach(function (d, i) {
      const h = d.volume > 0 ? Math.max(3, (d.volume / max) * plotH) : 0;
      const x = padX + i * slot + (slot - bw) / 2;
      const y = H - bottom - h;
      const dt = new Date(d.day + 'T00:00:00Z');
      const dayNum = dt.getUTCDate();
      const month = dt.toLocaleDateString('en-IN', { month: 'short', timeZone: 'UTC' });
      const tip = dayNum + ' ' + month + ': ' + d.txns + (d.txns === 1 ? ' transaction, ' : ' transactions, ') +
        money(d.volume) + (d.flagged ? ', ' + d.flagged + ' flagged' : '');
      svg += '<g><title>' + esc(tip) + '</title>';
      if (h > 0) {
        svg += '<rect class="bar" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="2"/>';
      } else {
        svg += '<rect class="bar bar-empty" x="' + x.toFixed(1) + '" y="' + (H - bottom - 2) + '" width="' + bw.toFixed(1) + '" height="2"/>';
      }
      if (d.flagged > 0) {
        svg += '<circle class="flag-dot" cx="' + (x + bw / 2).toFixed(1) + '" cy="' + (y - 9).toFixed(1) + '" r="4.5"/>';
      }
      svg += '<text class="tick" x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - bottom + 15) + '" text-anchor="middle">' + dayNum + '</text>';
      if (i === 0 || dayNum === 1) {
        svg += '<text class="tick tick-month" x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - bottom + 29) + '" text-anchor="middle">' + esc(month) + '</text>';
      }
      svg += '</g>';
    });
    box.innerHTML = svg + '</svg>';
  }

  // ------------------------------------------------------------ review queue
  function filtered() {
    const q = state.query.trim().toLowerCase();
    if (!q) return state.flags;
    return state.flags.filter((f) =>
      [f.customer_name, f.rule_name, f.location_city, f.account_number, f.status].some(
        (v) => v && String(v).toLowerCase().indexOf(q) !== -1
      )
    );
  }

  function renderQueue() {
    const rows = filtered();
    const box = $('queue-body');
    if (!rows.length) {
      box.innerHTML = '<p class="empty">' +
        (state.query ? 'No flags match your search.'
          : state.status === 'open' ? 'Nothing is waiting for review. New flags appear here as soon as a transaction trips a rule.'
          : 'No flags in this view.') + '</p>';
      return;
    }
    box.innerHTML =
      '<table class="queue"><thead><tr><th>Risk</th><th>Customer</th><th>Rule</th><th class="num">Amount</th>' +
      '<th>Where</th><th>When</th><th>Status</th></tr></thead><tbody>' +
      rows.map(function (f) {
        const lv = level(f.risk_score);
        return (
          '<tr class="flag-row edge-' + lv + '" tabindex="0" role="button" data-id="' + f.flag_id + '"' +
          ' aria-label="Open flag ' + f.flag_id + ' for ' + esc(f.customer_name || 'unknown sender') + '">' +
          '<td><div class="risk risk-' + lv + '"><b>' + f.risk_score + '</b>' + gauge(f.risk_score) + '</div></td>' +
          '<td><div class="strong">' + esc(f.customer_name || 'Unknown sender') + '</div>' +
          '<div class="muted small">' + esc(f.account_number || 'No source account') + '</div></td>' +
          '<td>' + esc(f.rule_name) + '</td>' +
          '<td class="num">' + esc(money(f.amount)) + '</td>' +
          '<td>' + esc(f.location_city || '\u2014') + '</td>' +
          '<td class="nowrap">' + esc(fmtDateTime(f.txn_at)) + '</td>' +
          '<td><span class="chip chip-' + f.status + '">' + esc(cap(f.status)) + '</span></td></tr>'
        );
      }).join('') +
      '</tbody></table>';
  }

  // ------------------------------------------------------------ insights
  function renderInsights() {
    const rules = state.stats.byRule;
    $('rules-body').innerHTML = rules.length
      ? '<table class="compact"><thead><tr><th>Rule</th><th class="num">Flags</th><th class="num">Confirmed</th><th class="num">Open</th></tr></thead><tbody>' +
        rules.map((r) => '<tr><td>' + esc(r.rule_name) + '</td><td class="num">' + r.total + '</td><td class="num">' + r.confirmed + '</td><td class="num">' + r.open + '</td></tr>').join('') +
        '</tbody></table>'
      : '<p class="empty">No rules configured.</p>';

    const cities = state.stats.byCity;
    $('cities-body').innerHTML = cities.length
      ? '<table class="compact"><thead><tr><th>City</th><th class="num">Alerts</th><th class="num">Average risk</th></tr></thead><tbody>' +
        cities.map((c) => '<tr><td>' + esc(c.city) + '</td><td class="num">' + c.alerts + '</td><td class="num">' + c.avg_risk + '</td></tr>').join('') +
        '</tbody></table>'
      : '<p class="empty">No location data yet.</p>';
  }

  function eventText(ev) {
    const who = ev.role === 'admin' ? 'Analyst' : 'Customer';
    const d = ev.details || {};
    switch (ev.event_type) {
      case 'LOGIN': return who + ' signed in';
      case 'TRANSFER': return 'Transfer #' + ev.entity_id + (d.amount ? ' of ' + money(d.amount) : '') + (d.flagged ? ', flagged' : '');
      case 'FLAG_REVIEWED': return 'Flag #' + ev.entity_id + ' ' + (d.to || 'reviewed');
      case 'ACCOUNT_FROZEN': return 'Account #' + ev.entity_id + ' frozen';
      case 'ACCOUNT_UNFROZEN': return 'Account #' + ev.entity_id + ' unfrozen';
      case 'SIGNUP': return 'New customer signed up';
      case 'DEPOSIT': return 'Funds added' + (d.amount ? ': ' + money(d.amount) : '');
      case 'ACCOUNT_OPENED': return 'Account #' + ev.entity_id + ' opened';
      default: return ev.event_type;
    }
  }

  function renderActivity(events) {
    $('audit-body').innerHTML = events.length
      ? events.map((ev) => '<li><span>' + esc(eventText(ev)) + '</span><time>' + esc(timeAgo(ev.at)) + '</time></li>').join('')
      : '<li class="muted">No activity recorded yet.</li>';
  }

  // ------------------------------------------------------------ drawer
  function kv(rows) {
    return '<dl class="kv">' + rows.map((r) => '<dt>' + esc(r[0]) + '</dt><dd>' + r[1] + '</dd>').join('') + '</dl>';
  }

  function renderDrawer(f) {
    const lv = level(f.risk_score);
    const ctx = f.context;
    const geo = ctx && ctx.geo ? [ctx.geo.city, ctx.geo.country].filter(Boolean).join(', ') : '';
    const dev = ctx && ctx.device ? [label(ctx.device.type), ctx.device.id].filter(Boolean).join(', ') : '';

    const txnRows = [
      ['Transaction', '#' + f.txn_id],
      ['Type', esc(cap(f.txn_type)) + ' by ' + esc(label(f.channel))],
      ['Amount', esc(money(f.amount))],
      ['Where', esc(f.location_city || '\u2014')],
      ['When', esc(fmtDateTime(f.txn_at))],
    ];
    if (f.description) txnRows.push(['Note', esc(f.description)]);

    const acct = f.account_id
      ? kv([
          ['Customer', esc(f.customer_name || '\u2014')],
          ['Home city', esc(f.home_city || '\u2014')],
          ['Account', esc(f.account_number) + ' <span class="chip chip-' + (f.account_status === 'active' ? 'dismissed' : 'confirmed') + '">' + esc(cap(f.account_status)) + '</span>'],
        ]) +
        '<button class="btn btn-quiet" type="button" id="toggle-account" data-account="' + f.account_id + '" data-to="' + (f.account_status === 'frozen' ? 'active' : 'frozen') + '">' +
        (f.account_status === 'frozen' ? 'Unfreeze account' : 'Freeze account') + '</button>'
      : '<p class="muted small">This transaction has no source account (it is a deposit).</p>';

    const context = ctx
      ? kv([
          ['Device', esc(dev || '\u2014')],
          ['IP address', esc((ctx.device && ctx.device.ip) || '\u2014')],
          ['Place', esc(geo || '\u2014')],
        ])
      : '<p class="muted small">No extra context was stored for this alert.</p>';

    const notes = f.analyst_notes && f.analyst_notes.length
      ? '<ol class="notes">' + f.analyst_notes.map((n) => '<li>' + esc(n.note) + '<time>' + esc(fmtDateTime(n.at)) + '</time></li>').join('') + '</ol>'
      : '<p class="muted small">No notes yet.</p>';

    let review;
    if (f.status === 'open') {
      review =
        '<label for="review-note">Add a note <span class="optional">optional</span></label>' +
        '<textarea id="review-note" rows="3" maxlength="500"></textarea>' +
        (f.account_status === 'active'
          ? '<label class="check"><input type="checkbox" id="review-freeze"> Freeze the sender\u2019s account if I confirm fraud</label>'
          : '') +
        '<p class="form-error" id="review-error" role="alert" hidden></p>' +
        '<div class="actions"><button class="btn btn-danger" type="button" data-review="confirmed">Confirm fraud</button>' +
        '<button class="btn" type="button" data-review="dismissed">Dismiss flag</button></div>';
    } else {
      review = '<p class="muted">' + (f.status === 'confirmed' ? 'Confirmed as fraud' : 'Dismissed') +
        (f.reviewed_at ? ' on ' + esc(fmtDateTime(f.reviewed_at)) : '') + '.</p>';
    }

    $('drawer-content').innerHTML =
      '<div class="drawer-head"><div><p class="drawer-eyebrow">Flag #' + f.flag_id + '</p>' +
      '<h2 id="drawer-title">' + esc(f.rule_name) + '</h2></div>' +
      '<button class="icon-btn" id="drawer-close" type="button" aria-label="Close details">\u00D7</button></div>' +
      '<div class="drawer-score risk-' + lv + '"><div class="score-num">' + f.risk_score + '</div>' +
      '<div class="score-meta"><span>Risk score</span>' + gauge(f.risk_score) + '</div>' +
      '<span class="chip chip-' + f.status + '">' + esc(cap(f.status)) + '</span></div>' +
      '<section><h3>Transaction</h3>' + kv(txnRows) + '</section>' +
      '<section><h3>Customer and account</h3>' + acct + '</section>' +
      '<section><h3>Alert context</h3>' + context + '</section>' +
      '<section><h3>Analyst notes</h3>' + notes + '</section>' +
      '<section class="review"><h3>Decision</h3>' + review + '</section>';
  }

  function openDrawer(id, opener) {
    const flag = state.flags.find((f) => f.flag_id === id);
    if (!flag) return;
    state.openId = id;
    if (opener) state.opener = opener;
    renderDrawer(flag);
    $('drawer').classList.add('open');
    $('drawer').setAttribute('aria-hidden', 'false');
    $('scrim').classList.add('open');
    document.body.classList.add('no-scroll');
    $('drawer-close').focus();
  }

  function closeDrawer() {
    state.openId = null;
    $('drawer').classList.remove('open');
    $('drawer').setAttribute('aria-hidden', 'true');
    $('scrim').classList.remove('open');
    document.body.classList.remove('no-scroll');
    if (state.opener && document.contains(state.opener)) state.opener.focus();
  }

  async function review(flag, status) {
    const noteEl = $('review-note');
    const freezeEl = $('review-freeze');
    const errorBox = $('review-error');
    const buttons = document.querySelectorAll('[data-review]');
    buttons.forEach((b) => (b.disabled = true));
    errorBox.hidden = true;
    try {
      const result = await api('/api/admin/flags/' + flag.flag_id, {
        method: 'PATCH',
        body: { status: status, note: noteEl ? noteEl.value : '', freezeAccount: !!(freezeEl && freezeEl.checked) },
      });
      toast(status === 'confirmed'
        ? (result.frozenAccountId ? 'Fraud confirmed and the account was frozen.' : 'Fraud confirmed.')
        : 'Flag dismissed.');
      closeDrawer();
      await loadAll();
    } catch (err) {
      errorBox.textContent = err.message;
      errorBox.hidden = false;
      buttons.forEach((b) => (b.disabled = false));
    }
  }

  async function toggleAccount(accountId, to) {
    try {
      await api('/api/admin/accounts/' + accountId, { method: 'PATCH', body: { status: to } });
      toast(to === 'frozen' ? 'Account frozen.' : 'Account unfrozen.');
      const id = state.openId;
      await loadAll();
      if (id && state.flags.some((f) => f.flag_id === id)) openDrawer(id);
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  // keep keyboard focus inside the open drawer
  document.addEventListener('keydown', function (e) {
    if (!$('drawer').classList.contains('open')) return;
    if (e.key === 'Escape') {
      closeDrawer();
      return;
    }
    if (e.key !== 'Tab') return;
    const items = $('drawer').querySelectorAll('button:not([disabled]), input, textarea, [href]');
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  });

  $('scrim').addEventListener('click', closeDrawer);

  $('drawer').addEventListener('click', function (e) {
    if (e.target.closest('#drawer-close')) return closeDrawer();
    const reviewBtn = e.target.closest('[data-review]');
    if (reviewBtn) {
      const flag = state.flags.find((f) => f.flag_id === state.openId);
      if (flag) review(flag, reviewBtn.getAttribute('data-review'));
      return;
    }
    const toggle = e.target.closest('#toggle-account');
    if (toggle) toggleAccount(Number(toggle.getAttribute('data-account')), toggle.getAttribute('data-to'));
  });

  $('queue-body').addEventListener('click', function (e) {
    const row = e.target.closest('tr[data-id]');
    if (row) openDrawer(Number(row.getAttribute('data-id')), row);
  });
  $('queue-body').addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const row = e.target.closest('tr[data-id]');
    if (row) {
      e.preventDefault();
      openDrawer(Number(row.getAttribute('data-id')), row);
    }
  });

  $('flag-tabs').addEventListener('click', async function (e) {
    const tab = e.target.closest('.tab');
    if (!tab) return;
    state.status = tab.getAttribute('data-status');
    document.querySelectorAll('#flag-tabs .tab').forEach(function (t) {
      const on = t === tab;
      t.classList.toggle('is-active', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    try {
      await loadFlags();
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  $('search').addEventListener('input', function (e) {
    state.query = e.target.value;
    renderQueue();
  });

  // ------------------------------------------------------------ data
  async function loadStats() {
    state.stats = await api('/api/admin/stats');
    renderHero();
    renderChart();
    renderInsights();
  }
  async function loadFlags() {
    state.flags = (await api('/api/admin/flags?status=' + encodeURIComponent(state.status))).flags;
    renderQueue();
  }
  async function loadActivity() {
    renderActivity((await api('/api/admin/activity')).events);
  }
  function loadAll() {
    return Promise.all([loadStats(), loadFlags(), loadActivity()]);
  }

  loadAll().catch(function (err) {
    $('queue-body').innerHTML = '<p class="empty">' + esc(err.message) + '</p>';
    toast(err.message, 'error');
  });
})();
