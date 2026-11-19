(function () {
  'use strict';

  class ApiError extends Error {
    constructor(message, status, field) {
      super(message);
      this.status = status;
      this.field = field || null;
    }
  }

  const csrf = () => (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

  async function api(path, options) {
    const opts = options || {};
    const method = opts.method || 'GET';
    const init = { method: method, credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (method !== 'GET') {
      init.headers['Content-Type'] = 'application/json';
      init.headers['X-CSRF-Token'] = csrf();
    }
    if (opts.body !== undefined) init.body = JSON.stringify(opts.body);

    let res;
    try {
      res = await fetch(path, init);
    } catch (e) {
      throw new ApiError('Could not reach the server. Check your connection and try again.', 0);
    }
    let data = null;
    try {
      data = await res.json();
    } catch (e) {
      /* response had no JSON body */
    }
    if (res.status === 401 && path !== '/api/login') {
      window.location.assign('/login');
      throw new ApiError('Please sign in.', 401);
    }
    if (!res.ok) {
      throw new ApiError((data && data.error) || 'Something went wrong. Please try again.', res.status, data && data.field);
    }
    return data;
  }

  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = (v) => String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, (c) => ESC[c]);

  const money = (n) =>
    new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(Number(n));

  function moneyBig(n) {
    const parts = Number(n).toFixed(2).split('.');
    return (
      '<span class="cur">\u20B9</span>' +
      Number(parts[0]).toLocaleString('en-IN') +
      '<span class="paise">.' + parts[1] + '</span>'
    );
  }

  const fmtDateTime = (iso) =>
    new Date(iso).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });

  function timeAgo(iso) {
    const secs = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
    if (secs < 60) return 'just now';
    const mins = Math.round(secs / 60);
    if (mins < 60) return mins + ' min ago';
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return hrs + (hrs === 1 ? ' hour ago' : ' hours ago');
    const days = Math.round(hrs / 24);
    return days + (days === 1 ? ' day ago' : ' days ago');
  }

  function toast(message, kind) {
    const host = document.getElementById('toasts');
    if (!host) return;
    const el = document.createElement('div');
    el.className = 'toast' + (kind === 'error' ? ' toast-error' : '');
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => {
      el.classList.add('toast-out');
      setTimeout(() => el.remove(), 250);
    }, 4200);
  }

  document.addEventListener('DOMContentLoaded', function () {
    const signout = document.getElementById('signout');
    if (signout) {
      signout.addEventListener('click', async function () {
        try {
          await api('/api/logout', { method: 'POST' });
        } catch (e) {
          /* leave the page either way */
        }
        window.location.assign('/login');
      });
    }

    // Highlight the rail link for the section currently in view.
    const links = Array.prototype.slice.call(document.querySelectorAll('.rail-nav a[href^="#"]'));
    if (links.length) {
      const sections = links
        .map((a) => ({ link: a, el: document.getElementById(a.getAttribute('href').slice(1)) }))
        .filter((x) => x.el);
      const update = function () {
        const line = window.innerHeight * 0.35;
        let current = sections[0];
        if (window.scrollY > 40) {
          sections.forEach((x) => {
            if (x.el.getBoundingClientRect().top <= line) current = x;
          });
        }
        links.forEach((l) => l.classList.toggle('active', current && l === current.link));
      };
      window.addEventListener('scroll', update, { passive: true });
      window.addEventListener('resize', update);
      update();
    }
  });

  window.LW = { api: api, ApiError: ApiError, esc: esc, money: money, moneyBig: moneyBig,
                fmtDateTime: fmtDateTime, timeAgo: timeAgo, toast: toast };
})();
