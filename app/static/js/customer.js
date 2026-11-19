(function () {
  'use strict';
  const { api, esc, money, moneyBig, fmtDateTime, toast } = window.LW;
  const $ = (id) => document.getElementById(id);
  const state = { accounts: [], txns: [], filter: 'all' };

  const last4 = (n) => '\u2022\u2022\u2022\u2022 ' + String(n).slice(-4);
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

  // ------------------------------------------------------------ rendering
  function renderHero() {
    const total = state.accounts.reduce((sum, a) => sum + a.balance, 0);
    $('total-balance').innerHTML = moneyBig(total);
    const n = state.accounts.length;
    const paused = state.accounts.filter((a) => a.status !== 'active').length;
    $('total-sub').textContent =
      state.txns.length === 0 && total === 0
        ? 'Welcome. Add funds to your new account to start sending money.'
        : n + (n === 1 ? ' account' : ' accounts') + (paused ? ', ' + paused + ' paused' : '');
  }

  function renderAccounts() {
    const box = $('account-list');
    if (!state.accounts.length) {
      box.innerHTML = '<p class="empty">You have no accounts yet.</p>';
      return;
    }
    box.innerHTML = state.accounts
      .map(function (a) {
        const action =
          a.status === 'active'
            ? '<button class="btn btn-quiet" type="button" data-send-from="' + a.account_id + '">Send from here</button>'
            : '<span class="chip chip-confirmed">' + esc(cap(a.status)) + '</span>';
        return (
          '<div class="ledger-row">' +
          '<div class="ledger-main">' +
          '<div class="ledger-name">' + esc(cap(a.account_type)) + ' account</div>' +
          '<div class="ledger-sub"><span class="acct-no">' + esc(a.account_number) + '</span>' +
          '<button class="copy" type="button" data-copy="' + esc(a.account_number) + '" aria-label="Copy account number">Copy</button>' +
          ' at ' + esc(a.branch_name) + '</div>' +
          '</div>' +
          '<div class="ledger-balance">' + esc(money(a.balance)) + '</div>' +
          '<div class="ledger-action">' + action + '</div>' +
          '</div>'
        );
      })
      .join('');
  }

  function describe(t) {
    if (t.txn_type === 'deposit') return 'Deposit';
    if (t.txn_type === 'withdrawal') return 'Withdrawal';
    return t.outgoing ? 'Sent to ' + last4(t.to_account) : 'Received from ' + last4(t.from_account);
  }

  function renderActivity() {
    const rows = state.txns.filter(function (t) {
      if (state.filter === 'out') return t.outgoing;
      if (state.filter === 'in') return !t.outgoing;
      return true;
    });
    const box = $('activity-body');
    if (!rows.length) {
      box.innerHTML = '<p class="empty">' +
        (state.txns.length ? 'Nothing to show for this filter.' : 'No transactions yet. Use Add funds, then send some money to see activity here.') + '</p>';
      return;
    }
    box.innerHTML =
      '<table><thead><tr><th>When</th><th>Details</th><th class="num">Amount</th></tr></thead><tbody>' +
      rows.map(function (t) {
        const sub = [t.description, t.location_city].filter(Boolean).join(', ');
        return (
          '<tr><td class="nowrap">' + esc(fmtDateTime(t.created_at)) + '</td>' +
          '<td><div>' + esc(describe(t)) + '</div>' + (sub ? '<div class="muted small">' + esc(sub) + '</div>' : '') + '</td>' +
          '<td class="num ' + (t.outgoing ? 'amt-out' : 'amt-in') + '">' +
          (t.outgoing ? '\u2212' : '+') + esc(money(t.amount)) + '</td></tr>'
        );
      }).join('') +
      '</tbody></table>';
  }

  function renderFromSelect() {
    const select = $('from');
    const keep = select.value;
    const active = state.accounts.filter((a) => a.status === 'active');
    select.innerHTML = active.length
      ? active.map((a) => '<option value="' + a.account_id + '">' + esc(cap(a.account_type)) + ' ' + esc(last4(a.account_number)) + ', ' + esc(money(a.balance)) + '</option>').join('')
      : '<option value="">No account can send money right now</option>';
    if (keep && active.some((a) => String(a.account_id) === keep)) select.value = keep;
    $('send-submit').disabled = !active.length;
  }

  // ------------------------------------------------------------ data
  async function load() {
    const results = await Promise.all([api('/api/accounts'), api('/api/transactions')]);
    state.accounts = results[0].accounts;
    state.txns = results[1].transactions;
    renderHero();
    renderAccounts();
    renderActivity();
    renderFromSelect();
  }

  // ------------------------------------------------------------ recipient lookup
  let lookupTimer = null;
  let lookupSeq = 0;

  function setNote(text, kind) {
    const note = $('to-note');
    note.textContent = text;
    note.className = 'field-note' + (kind ? ' note-' + kind : '');
  }

  $('to').addEventListener('input', function () {
    const value = $('to').value.replace(/\D/g, '');
    $('to').value = value;
    clearTimeout(lookupTimer);
    setNote('');
    if (value.length < 10) return;
    const seq = ++lookupSeq;
    lookupTimer = setTimeout(async function () {
      try {
        const found = await api('/api/accounts/lookup?number=' + encodeURIComponent(value));
        if (seq === lookupSeq) {
          setNote(found.holder + ', ' + found.branch + (found.status !== 'active' ? ' (not accepting transfers)' : ''),
                  found.status === 'active' ? 'ok' : 'bad');
        }
      } catch (err) {
        if (seq === lookupSeq) setNote(err.message, 'bad');
      }
    }, 300);
  });

  // ------------------------------------------------------------ sending money
  const form = $('send-form');
  const errorBox = $('send-error');

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    errorBox.hidden = true;
    const body = {
      fromAccountId: Number($('from').value),
      toAccountNumber: $('to').value.trim(),
      amount: $('amount').value,
      city: $('city').value.trim(),
      description: $('note').value.trim(),
    };
    if (!body.fromAccountId || !body.toAccountNumber || !body.amount) {
      errorBox.textContent = 'Choose an account, enter the destination account number and an amount.';
      errorBox.hidden = false;
      return;
    }
    const button = $('send-submit');
    button.disabled = true;
    button.textContent = 'Sending\u2026';
    try {
      const result = await api('/api/transfer', { method: 'POST', body: body });
      toast(result.flagged ? 'Transfer sent. It was flagged for review (demo message).' : 'Transfer sent.');
      $('to').value = '';
      $('amount').value = '';
      $('note').value = '';
      setNote('');
      await load();
    } catch (err) {
      errorBox.textContent = err.message;
      errorBox.hidden = false;
    } finally {
      button.disabled = false;
      button.textContent = 'Send money';
      renderFromSelect();
    }
  });

  // ------------------------------------------------------------ small interactions
  $('account-list').addEventListener('click', function (e) {
    const send = e.target.closest('[data-send-from]');
    if (send) {
      $('from').value = send.getAttribute('data-send-from');
      $('send').scrollIntoView({ behavior: 'smooth', block: 'start' });
      $('to').focus({ preventScroll: true });
      return;
    }
    const copy = e.target.closest('[data-copy]');
    if (copy && navigator.clipboard) {
      navigator.clipboard.writeText(copy.getAttribute('data-copy')).then(
        () => toast('Account number copied.'),
        () => toast('Could not copy. Select the number and copy it manually.', 'error')
      );
    }
  });

  $('activity-tabs').addEventListener('click', function (e) {
    const tab = e.target.closest('.tab');
    if (!tab) return;
    state.filter = tab.getAttribute('data-filter');
    document.querySelectorAll('#activity-tabs .tab').forEach(function (t) {
      const on = t === tab;
      t.classList.toggle('is-active', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    renderActivity();
  });


  // ------------------------------------------------------------ dialogs: add funds, open account
  function showDialog(id) {
    const dlg = $(id);
    if (typeof dlg.showModal === 'function') dlg.showModal();
    else dlg.setAttribute('open', '');
  }
  function closeDialog(dlg) {
    if (typeof dlg.close === 'function') dlg.close();
    else dlg.removeAttribute('open');
  }
  document.querySelectorAll('dialog [data-close]').forEach(function (b) {
    b.addEventListener('click', function () { closeDialog(b.closest('dialog')); });
  });
  document.querySelectorAll('dialog').forEach(function (dlg) {
    dlg.addEventListener('click', function (e) { if (e.target === dlg) closeDialog(dlg); });
  });

  $('btn-fund').addEventListener('click', function () {
    const active = state.accounts.filter((a) => a.status === 'active');
    if (!active.length) { toast('None of your accounts can receive funds right now.', 'error'); return; }
    $('fund-account').innerHTML = active.map((a) =>
      '<option value="' + a.account_id + '">' + esc(cap(a.account_type)) + ' ' + esc(last4(a.account_number)) + '</option>').join('');
    $('fund-amount').value = '';
    $('fund-error').hidden = true;
    showDialog('fund-dialog');
    $('fund-amount').focus();
  });

  $('fund-form').addEventListener('submit', async function (e) {
    e.preventDefault();
    const errorBox = $('fund-error');
    errorBox.hidden = true;
    const button = $('fund-submit');
    button.disabled = true;
    try {
      await api('/api/deposit', { method: 'POST', body: { accountId: Number($('fund-account').value), amount: $('fund-amount').value } });
      closeDialog($('fund-dialog'));
      toast('Funds added.');
      await load();
    } catch (err) {
      errorBox.textContent = err.message;
      errorBox.hidden = false;
    } finally {
      button.disabled = false;
    }
  });

  let branches = null;
  $('btn-open').addEventListener('click', async function () {
    $('open-error').hidden = true;
    try {
      if (!branches) branches = (await api('/api/branches')).branches;
    } catch (err) {
      toast(err.message, 'error');
      return;
    }
    $('open-branch').innerHTML = branches.map((b) => '<option value="' + b.branch_id + '">' + esc(b.branch_name) + '</option>').join('');
    showDialog('open-dialog');
    $('open-type').focus();
  });

  $('open-form').addEventListener('submit', async function (e) {
    e.preventDefault();
    const errorBox = $('open-error');
    errorBox.hidden = true;
    const button = $('open-submit');
    button.disabled = true;
    try {
      const result = await api('/api/accounts/open', { method: 'POST', body: {
        accountType: $('open-type').value, branchId: Number($('open-branch').value) } });
      closeDialog($('open-dialog'));
      toast('Account ' + result.account.account_number + ' opened.');
      await load();
    } catch (err) {
      errorBox.textContent = err.message;
      errorBox.hidden = false;
    } finally {
      button.disabled = false;
    }
  });

  load().catch(function (err) {
    $('account-list').innerHTML = '<p class="empty">' + esc(err.message) + '</p>';
    $('activity-body').innerHTML = '';
    $('total-balance').textContent = '\u2014';
  });
})();
