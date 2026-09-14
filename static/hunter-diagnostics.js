(() => {
  const POLL_MS = 1200;
  const TAB_DEFS = [
    ['query_plan', 'Query plan'],
    ['providers', 'Providers'],
    ['raw_intake', 'Raw / intake'],
    ['qualification', 'Qualification'],
    ['deep_audit', 'Deep audit'],
    ['funnel_trace', 'Funnel / trace'],
  ];

  let timer = null;
  let selectedTab = 'query_plan';
  let currentSnapshot = null;
  let currentAttempts = [];

  async function apiGet(path) {
    const response = await fetch(path, {credentials: 'same-origin'});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : data.detail?.reason;
      throw new Error(detail || `HTTP ${response.status}`);
    }
    return data;
  }

  function addStylesheet() {
    if (document.querySelector('link[data-hunter-diagnostics]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/static/hunter-diagnostics.css?v=20260914a';
    link.dataset.hunterDiagnostics = 'true';
    document.head.append(link);
  }

  function text(tag, value, className = '') {
    const node = document.createElement(tag);
    node.textContent = value;
    if (className) node.className = className;
    return node;
  }

  function buildUi() {
    const panel = document.querySelector('[data-service-panel="hunter"]');
    if (!panel || document.querySelector('#hunterDiagnosticsControls')) return null;

    const controls = document.createElement('section');
    controls.id = 'hunterDiagnosticsControls';
    controls.className = 'hunter-diagnostics-controls';

    const toggleLabel = document.createElement('label');
    toggleLabel.className = 'hunter-diagnostics-toggle';
    const toggle = document.createElement('input');
    toggle.id = 'hunterDebugMode';
    toggle.type = 'checkbox';
    toggleLabel.append(toggle, document.createTextNode(' Режим диагностики (администратор)'));

    const hint = text(
      'p',
      'Показывает санитизированные события Hunter/TraceLedger. Секреты, raw credentials и prompt не отображаются.',
      'service-summary__meta',
    );
    controls.append(toggleLabel, hint);

    const diagnostics = document.createElement('section');
    diagnostics.id = 'hunterDiagnosticsPanel';
    diagnostics.className = 'hunter-diagnostics';
    diagnostics.hidden = true;

    const head = document.createElement('div');
    head.className = 'hunter-diagnostics__head';
    head.append(text('strong', 'Developer diagnostics'));

    const selector = document.createElement('select');
    selector.id = 'hunterDiagnosticsAttempt';
    selector.setAttribute('aria-label', 'Запуск Hunter для диагностики');
    head.append(selector);

    const refresh = document.createElement('button');
    refresh.type = 'button';
    refresh.className = 'btn-ghost btn-sm';
    refresh.textContent = 'Обновить';
    head.append(refresh);

    const status = text('p', 'Диагностика выключена.', 'service-status');
    status.id = 'hunterDiagnosticsStatus';

    const tabs = document.createElement('div');
    tabs.className = 'hunter-diagnostics__tabs';
    tabs.setAttribute('role', 'tablist');
    TAB_DEFS.forEach(([key, label]) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.hunterDiagnosticTab = key;
      button.className = 'btn-ghost btn-sm';
      button.textContent = label;
      button.setAttribute('role', 'tab');
      button.setAttribute('aria-selected', key === selectedTab ? 'true' : 'false');
      tabs.append(button);
    });

    const scope = document.createElement('div');
    scope.id = 'hunterDiagnosticsScope';
    scope.className = 'hunter-diagnostics__scope';

    const body = document.createElement('div');
    body.id = 'hunterDiagnosticsBody';
    body.className = 'hunter-diagnostics__body';

    const limitations = document.createElement('div');
    limitations.id = 'hunterDiagnosticsLimitations';
    limitations.className = 'hunter-diagnostics__limitations';

    diagnostics.append(head, status, tabs, scope, body, limitations);
    controls.append(diagnostics);

    const output = document.querySelector('#hunterOutput');
    if (output) panel.insertBefore(controls, output);
    else panel.append(controls);

    toggle.addEventListener('change', () => {
      diagnostics.hidden = !toggle.checked;
      if (toggle.checked) startPolling();
      else stopPolling();
    });
    selector.addEventListener('change', () => loadSelectedAttempt());
    refresh.addEventListener('click', () => refreshDiagnostics());
    tabs.addEventListener('click', event => {
      const button = event.target.closest('[data-hunter-diagnostic-tab]');
      if (!button) return;
      selectedTab = button.dataset.hunterDiagnosticTab;
      tabs.querySelectorAll('[data-hunter-diagnostic-tab]').forEach(item => {
        item.setAttribute('aria-selected', item === button ? 'true' : 'false');
      });
      renderSnapshot();
    });

    return controls;
  }

  function normalize(value) {
    return String(value || '').trim().toLocaleLowerCase('ru-RU');
  }

  function attemptLabel(attempt) {
    const scope = attempt.scope || {};
    const region = scope.effective_region || scope.input_region || 'регион не указан';
    const industries = Array.isArray(scope.effective_industries)
      ? scope.effective_industries.join(', ')
      : (scope.effective_industries || 'отрасль не указана');
    const marker = attempt.complete ? '✓' : '…';
    return `${marker} ${region} · ${industries} · ${attempt.updated_at || attempt.started_at || ''}`;
  }

  function chooseAttempt(attempts) {
    const selector = document.querySelector('#hunterDiagnosticsAttempt');
    const previous = selector?.value || '';
    const currentRegion = normalize(document.querySelector('#hunterRegion')?.value);
    const previousMatch = attempts.find(item => `${item.mission_id}::${item.attempt_id}` === previous);
    if (previousMatch) return previousMatch;
    if (currentRegion) {
      const scoped = attempts.find(item => {
        const scope = item.scope || {};
        return normalize(scope.effective_region || scope.input_region) === currentRegion;
      });
      if (scoped) return scoped;
    }
    return attempts[0] || null;
  }

  function renderAttemptSelector(attempts, selected) {
    const selector = document.querySelector('#hunterDiagnosticsAttempt');
    if (!selector) return;
    selector.replaceChildren();
    if (!attempts.length) {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = 'Нет Hunter trace за период retention';
      selector.append(option);
      selector.disabled = true;
      return;
    }
    selector.disabled = false;
    attempts.forEach(attempt => {
      const option = document.createElement('option');
      option.value = `${attempt.mission_id}::${attempt.attempt_id}`;
      option.textContent = attemptLabel(attempt);
      if (selected && attempt.mission_id === selected.mission_id && attempt.attempt_id === selected.attempt_id) {
        option.selected = true;
      }
      selector.append(option);
    });
  }

  function renderScope(snapshot) {
    const node = document.querySelector('#hunterDiagnosticsScope');
    if (!node) return;
    node.replaceChildren();
    if (!snapshot) return;
    const scope = snapshot.scope || {};
    const items = [
      `mission: ${snapshot.mission_id}`,
      `attempt: ${snapshot.attempt_id}`,
      `region: ${scope.effective_region || scope.input_region || '—'}`,
      `industries: ${Array.isArray(scope.effective_industries) ? scope.effective_industries.join(', ') : (scope.effective_industries || '—')}`,
      `plan: ${scope.plan_source || '—'}`,
      `min/deep: ${scope.minimum_pre_score ?? '—'} / ${scope.deep_audit_score ?? '—'}`,
      `state: ${snapshot.complete ? 'complete' : 'running/degraded'} · ${snapshot.last_operation || '—'}`,
    ];
    items.forEach(item => node.append(text('span', item)));
  }

  function renderEvent(event) {
    const article = document.createElement('article');
    article.className = 'hunter-diagnostic-event';
    const top = document.createElement('div');
    top.className = 'hunter-diagnostic-event__top';
    top.append(
      text('strong', `#${event.sequence ?? '—'} · ${event.operation || 'event'}`),
      text('span', `${event.state || '—'} · ${event.created_at || ''}`, 'service-summary__meta'),
    );
    article.append(top);
    article.append(text('div', event.summary || 'Без описания'));
    article.append(text('div', `reason: ${event.reason_code || '—'}${event.provider ? ` · provider: ${event.provider}` : ''}`, 'service-summary__meta'));

    const counters = event.counters && Object.keys(event.counters).length
      ? JSON.stringify(event.counters, null, 2)
      : '';
    const metadata = event.metadata && Object.keys(event.metadata).length
      ? JSON.stringify(event.metadata, null, 2)
      : '';
    if (counters) {
      const pre = text('pre', counters, 'hunter-diagnostic-event__json');
      pre.setAttribute('aria-label', 'Counters');
      article.append(pre);
    }
    if (metadata) {
      const pre = text('pre', metadata, 'hunter-diagnostic-event__json');
      pre.setAttribute('aria-label', 'Metadata');
      article.append(pre);
    }
    return article;
  }

  function renderSnapshot() {
    const body = document.querySelector('#hunterDiagnosticsBody');
    const status = document.querySelector('#hunterDiagnosticsStatus');
    const limitations = document.querySelector('#hunterDiagnosticsLimitations');
    if (!body || !status || !limitations) return;
    body.replaceChildren();
    limitations.replaceChildren();
    renderScope(currentSnapshot);

    if (!currentSnapshot) {
      status.textContent = currentAttempts.length ? 'Выберите запуск Hunter.' : 'Trace Hunter пока не найден.';
      return;
    }

    const events = currentSnapshot.sections?.[selectedTab] || [];
    status.textContent = currentSnapshot.complete
      ? `Trace завершён. Событий во вкладке: ${events.length}.`
      : `Live trace: ${currentSnapshot.last_operation || 'поиск выполняется'}. Событий во вкладке: ${events.length}.`;

    if (!events.length) {
      body.append(text('p', 'В этой секции пока нет событий. Для live-запуска данные появляются по мере прохождения этапов.'));
    } else {
      events.forEach(event => body.append(renderEvent(event)));
    }

    (currentSnapshot.limitations || []).forEach(item => {
      limitations.append(text('p', item, 'service-summary__meta'));
    });
  }

  async function loadSelectedAttempt() {
    const selector = document.querySelector('#hunterDiagnosticsAttempt');
    if (!selector?.value) {
      currentSnapshot = null;
      renderSnapshot();
      return;
    }
    const [missionId, attemptId] = selector.value.split('::');
    currentSnapshot = await apiGet(
      `/api/runtime/hunter-diagnostics/${encodeURIComponent(missionId)}/${encodeURIComponent(attemptId)}`,
    );
    renderSnapshot();
  }

  async function refreshDiagnostics() {
    const status = document.querySelector('#hunterDiagnosticsStatus');
    try {
      const data = await apiGet('/api/runtime/hunter-diagnostics/attempts?limit=30');
      currentAttempts = Array.isArray(data.attempts) ? data.attempts : [];
      const selected = chooseAttempt(currentAttempts);
      renderAttemptSelector(currentAttempts, selected);
      if (!selected) {
        currentSnapshot = null;
        renderSnapshot();
        return;
      }
      currentSnapshot = await apiGet(
        `/api/runtime/hunter-diagnostics/${encodeURIComponent(selected.mission_id)}/${encodeURIComponent(selected.attempt_id)}`,
      );
      renderSnapshot();
    } catch (error) {
      if (status) status.textContent = `Диагностика недоступна: ${error.message}`;
    }
  }

  function stopPolling() {
    if (timer !== null) window.clearInterval(timer);
    timer = null;
    const status = document.querySelector('#hunterDiagnosticsStatus');
    if (status) status.textContent = 'Диагностика выключена.';
  }

  function startPolling() {
    if (timer !== null) return;
    refreshDiagnostics();
    timer = window.setInterval(refreshDiagnostics, POLL_MS);
  }

  async function init() {
    try {
      const user = await apiGet('/api/auth/me');
      if (!String(user.role || '').toLowerCase().includes('admin')) return;
    } catch {
      return;
    }
    addStylesheet();
    buildUi();
  }

  init();
})();
