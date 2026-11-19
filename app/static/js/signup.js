(function () {
  'use strict';
  const { api } = window.LW;
  const form = document.getElementById('signup-form');
  const submit = document.getElementById('signup-submit');
  const formError = document.getElementById('signup-error');

  // keep people from picking a date that would make them under 18 (the server checks this too)
  const dob = document.getElementById('dob');
  const today = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const iso = (d) => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  dob.max = iso(new Date(today.getFullYear() - 18, today.getMonth(), today.getDate()));
  dob.min = iso(new Date(today.getFullYear() - 120, today.getMonth(), today.getDate()));

  function clearErrors() {
    formError.hidden = true;
    form.querySelectorAll('.field-error').forEach(function (p) { p.hidden = true; p.textContent = ''; });
    form.querySelectorAll('[aria-invalid]').forEach(function (i) { i.removeAttribute('aria-invalid'); });
  }

  function fieldError(name, message) {
    const slot = form.querySelector('.field-error[data-for="' + name + '"]');
    const input = form.elements[name];
    if (!slot || !input) { formError.textContent = message; formError.hidden = false; return; }
    slot.textContent = message;
    slot.hidden = false;
    input.setAttribute('aria-invalid', 'true');
    input.focus();
  }

  function localCheck(v) {
    if (v.fullName.trim().length < 2) return ['fullName', 'Enter your full name.'];
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(v.email.trim())) return ['email', 'Enter a valid email address.'];
    if (!/^\d{10,15}$/.test(v.phone.replace(/[\s\-()+]/g, ''))) return ['phone', 'Enter a phone number with 10 to 15 digits.'];
    if (!v.dob) return ['dob', 'Enter your date of birth.'];
    if (!v.homeCity.trim()) return ['homeCity', 'Enter your home city.'];
    if (v.password.length < 8 || !/[A-Za-z]/.test(v.password) || !/\d/.test(v.password)) {
      return ['password', 'Use at least 8 characters with a letter and a number.'];
    }
    if (v.password !== v.confirmPassword) return ['confirmPassword', 'The two passwords do not match.'];
    return null;
  }

  document.getElementById('toggle-passwords').addEventListener('click', function (e) {
    const showing = e.currentTarget.getAttribute('aria-pressed') === 'true';
    ['password', 'confirmPassword'].forEach(function (n) { form.elements[n].type = showing ? 'password' : 'text'; });
    e.currentTarget.setAttribute('aria-pressed', showing ? 'false' : 'true');
    e.currentTarget.textContent = showing ? 'Show passwords' : 'Hide passwords';
  });

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    clearErrors();
    const values = {
      fullName: form.fullName.value, email: form.email.value, phone: form.phone.value, dob: form.dob.value,
      homeCity: form.homeCity.value, branchId: Number(form.branchId.value),
      password: form.password.value, confirmPassword: form.confirmPassword.value,
    };
    const problem = localCheck(values);
    if (problem) { fieldError(problem[0], problem[1]); return; }

    submit.disabled = true;
    submit.textContent = 'Creating your account\u2026';
    try {
      await api('/api/register', { method: 'POST', body: values });
      window.location.assign('/dashboard');
    } catch (err) {
      if (err.field) fieldError(err.field, err.message);
      else { formError.textContent = err.message; formError.hidden = false; }
      submit.disabled = false;
      submit.textContent = 'Create account';
    }
  });
})();
