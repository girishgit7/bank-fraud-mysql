(function () {
  'use strict';
  const { api } = window.LW;
  const form = document.getElementById('login-form');
  const errorBox = document.getElementById('login-error');
  const submit = document.getElementById('login-submit');

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
  }

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    errorBox.hidden = true;
    const email = form.email.value.trim();
    const password = form.password.value;
    if (!email || !password) {
      showError('Enter your email and password.');
      return;
    }
    submit.disabled = true;
    submit.textContent = 'Signing in\u2026';
    try {
      const data = await api('/api/login', { method: 'POST', body: { email: email, password: password } });
      window.location.assign(data.role === 'admin' ? '/admin' : '/dashboard');
    } catch (err) {
      showError(err.message);
      submit.disabled = false;
      submit.textContent = 'Sign in';
      form.password.value = '';
      form.password.focus();
    }
  });
})();
