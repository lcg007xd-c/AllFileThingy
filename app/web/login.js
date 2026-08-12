const form = document.querySelector('#login-form');
const error = document.querySelector('#error');
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  error.textContent = '';
  const button = form.querySelector('button');
  button.disabled = true;
  try {
    const response = await fetch('/api/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({password: form.password.value})
    });
    if (!response.ok) throw new Error((await response.json()).detail || 'Login failed');
    location.href = '/';
  } catch (reason) {
    error.textContent = reason.message;
    button.disabled = false;
  }
});

