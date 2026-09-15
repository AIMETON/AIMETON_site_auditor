(() => {
  'use strict';

  const state = {
    user: null,
    phase: 'loading',
  };

  const elements = {};

  function byId(id) {
    return document.getElementById(id);
  }

  function setMessage(message, kind = 'info') {
    const node = elements.message;
    node.textContent = message || '';
    node.dataset.kind = kind;
    node.hidden = !message;
  }

  function setBusy(busy) {
    elements.submit.disabled = busy;
    elements.username.disabled = busy;
    elements.password.disabled = busy;
    elements.submit.textContent = busy ? 'Входим…' : 'Войти';
  }

  function showLogin(message = '') {
    state.user = null;
    state.phase = 'anonymous';
    window.dispatchEvent(new CustomEvent('aimeton:auth-changed'));
    elements.login.hidden = false;
    elements.workspace.hidden = true;
    elements.identity.hidden = true;
    elements.form.reset();
    setBusy(false);
    setMessage(message, message ? 'error' : 'info');
    window.setTimeout(() => elements.username.focus(), 0);
  }

  function roleLabel(role) {
    return role === 'admin' ? 'Администратор' : 'Пользователь';
  }

  function showWorkspace(user) {
    state.user = user;
    state.phase = 'authenticated';
    window.dispatchEvent(new CustomEvent('aimeton:auth-changed'));
    elements.login.hidden = true;
    elements.workspace.hidden = false;
    elements.identity.hidden = false;
    elements.identityName.textContent = user.username;
    elements.identityRole.textContent = roleLabel(user.role);
    elements.identity.dataset.role = user.role;
    setMessage('');
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {
      credentials: 'same-origin',
      headers: {
        'Accept': 'application/json',
        ...(options.body ? {'Content-Type': 'application/json'} : {}),
        ...(options.headers || {}),
      },
      ...options,
    });
    return response;
  }

  function takeMagicLinkToken() {
    if (!window.location.hash.startsWith('#')) return '';
    const params = new URLSearchParams(window.location.hash.slice(1));
    const token = params.get('access_token') || '';
    if (!token) return '';
    // Remove the secret before any further navigation or third-party interaction.
    history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);
    return token;
  }

  async function consumeMagicLink() {
    const token = takeMagicLinkToken();
    if (!token) return false;

    state.phase = 'token-login';
    elements.login.hidden = false;
    elements.workspace.hidden = true;
    elements.identity.hidden = true;
    setBusy(true);
    setMessage('Открываем персональный временный доступ…');
    try {
      const response = await request('/api/auth/token-login', {
        method: 'POST',
        body: JSON.stringify({token}),
      });
      if (response.ok) {
        showWorkspace(await response.json());
        return true;
      }
      showLogin('Временная ссылка недействительна или срок её действия закончился.');
      return true;
    } catch (_error) {
      showLogin('Не удалось открыть временный доступ. Проверьте подключение и повторите переход по ссылке.');
      return true;
    } finally {
      setBusy(false);
    }
  }

  async function restoreSession() {
    state.phase = 'loading';
    elements.login.hidden = true;
    elements.workspace.hidden = true;
    elements.identity.hidden = true;
    try {
      const response = await request('/api/auth/me');
      if (response.ok) {
        showWorkspace(await response.json());
        return;
      }
      if (response.status === 401) {
        showLogin();
        return;
      }
      showLogin('Сервис авторизации временно недоступен. Повторите попытку.');
    } catch (_error) {
      showLogin('Нет связи с сервером. Проверьте подключение и повторите попытку.');
    }
  }

  async function login(event) {
    event.preventDefault();
    setBusy(true);
    setMessage('');

    const username = elements.username.value.trim();
    const password = elements.password.value;
    try {
      const response = await request('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({username, password}),
      });
      elements.password.value = '';
      if (response.ok) {
        showWorkspace(await response.json());
        return;
      }
      if (response.status === 401) {
        showLogin('Неверное имя пользователя или пароль.');
        elements.username.value = username;
        return;
      }
      showLogin('Вход не выполнен. Повторите попытку позже.');
      elements.username.value = username;
    } catch (_error) {
      elements.password.value = '';
      showLogin('Нет связи с сервером. Проверьте подключение и повторите попытку.');
      elements.username.value = username;
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    elements.logout.disabled = true;
    try {
      await request('/api/auth/logout', {method: 'POST'});
    } finally {
      elements.logout.disabled = false;
      showLogin('Сессия завершена.');
    }
  }

  async function restoreOrConsumeMagicLink() {
    if (await consumeMagicLink()) return;
    await restoreSession();
  }

  function bind() {
    elements.login = byId('authGate');
    elements.form = byId('loginForm');
    elements.username = byId('loginUsername');
    elements.password = byId('loginPassword');
    elements.submit = byId('loginSubmit');
    elements.message = byId('loginMessage');
    elements.workspace = byId('workspace');
    elements.identity = byId('userIdentity');
    elements.identityName = byId('userIdentityName');
    elements.identityRole = byId('userIdentityRole');
    elements.logout = byId('logoutBtn');

    elements.form.addEventListener('submit', login);
    elements.logout.addEventListener('click', logout);
    restoreOrConsumeMagicLink();
  }

  document.addEventListener('DOMContentLoaded', bind, {once: true});

  window.AIMETON_AUTH_UI = Object.freeze({
    restoreSession,
    getState: () => ({phase: state.phase, user: state.user ? {...state.user} : null}),
  });
})();
