(() => {
  const ACTIVE_KEY = 'aimeton_active_analysis_v1';
  const TERMINAL = new Set(['completed', 'degraded', 'blocked', 'failed']);
  const form = document.querySelector('#form');
  const statusNode = document.querySelector('#status');
  const button = document.querySelector('#analyzeBtn');
  if (!form || !statusNode || !button) return;

  const stateLabels = {
    queued: 'В очереди',
    running: 'Выполняется',
    stalled: 'Нет новых событий',
    degraded: 'Завершено с ограничениями',
    blocked: 'Остановлено',
    completed: 'Завершено',
    failed: 'Ошибка',
  };

  const iconLabels = {
    inbox: '📥', globe: '🌐', check: '✓', building: '🏢',
    search: '🔎', brain: '🧠', briefcase: '💼', file: '📄',
    'check-circle': '✅', 'alert-triangle': '⚠️', clock: '🕒',
  };

  let pollTimer = null;
  let elapsedTimer = null;
  let startedAt = null;
  let current = null;
  let latestEvents = [];
  let latestState = 'queued';
  let latestUpdatedAt = null;

  function emitWorkspaceEvent(name, detail) {
    window.dispatchEvent(new CustomEvent(name, { detail }));
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[char]));
  }

  function installStyles() {
    if (document.querySelector('#liveAnalysisStyles')) return;
    const style = document.createElement('style');
    style.id = 'liveAnalysisStyles';
    style.textContent = `
      .mission-reporter{margin-top:16px;padding:16px;border:1px solid #dfe3f5;border-radius:16px;background:#f8f9ff;text-align:left}
      .mission-reporter__head{display:flex;gap:12px;justify-content:space-between;align-items:flex-start;margin-bottom:10px}
      .mission-reporter__title{font-weight:750;color:#172033}
      .mission-reporter__meta{font-size:12px;color:#68708a;margin-top:3px;overflow-wrap:anywhere}
      .mission-reporter__state{padding:5px 9px;border-radius:999px;background:#e9ebff;color:#4148ba;font-size:12px;font-weight:700;white-space:nowrap}
      .mission-heartbeat{display:grid;grid-template-columns:12px 1fr;gap:9px;align-items:start;margin:0 0 12px;padding:10px 11px;border:1px solid #dfe3f5;border-radius:11px;background:#fff;color:#38405a;font-size:13px;line-height:1.35}
      .mission-heartbeat__dot{width:10px;height:10px;margin-top:3px;border-radius:50%;background:#5b61e6;box-shadow:0 0 0 0 rgba(91,97,230,.35);animation:missionPulse 1.6s infinite}
      .mission-heartbeat__title{font-weight:700;color:#202943}
      .mission-heartbeat__detail{margin-top:2px;color:#68708a}
      .mission-heartbeat[data-stale="true"]{border-color:#f0d59a;background:#fffaf0}
      .mission-heartbeat[data-stale="true"] .mission-heartbeat__dot{background:#c18412}
      @keyframes missionPulse{0%{box-shadow:0 0 0 0 rgba(91,97,230,.35)}70%{box-shadow:0 0 0 7px rgba(91,97,230,0)}100%{box-shadow:0 0 0 0 rgba(91,97,230,0)}}
      .mission-reporter__events{display:grid;gap:8px;margin:0;padding:0;list-style:none}
      .mission-event{display:grid;grid-template-columns:24px 1fr;gap:8px;padding:9px 10px;border-radius:10px;background:#fff;border:1px solid #eaecf6}
      .mission-event__message{font-weight:650;color:#202943}
      .mission-event__detail,.mission-event__next{font-size:13px;color:#68708a;margin-top:3px}
      .mission-event__time{font-size:11px;color:#8d94aa;margin-top:4px}
      .mission-reporter[data-state="blocked"] .mission-reporter__state,.mission-reporter[data-state="failed"] .mission-reporter__state{background:#ffe8e8;color:#a12828}
      .mission-reporter[data-state="degraded"] .mission-reporter__state,.mission-reporter[data-state="stalled"] .mission-reporter__state{background:#fff1d6;color:#855b00}
      .mission-reporter[data-state="completed"] .mission-reporter__state{background:#e0f6e8;color:#176b37}
      @media (max-width:640px){.mission-reporter__head{display:block}.mission-reporter__state{display:inline-block;margin-top:8px}.mission-reporter{padding:14px}.mission-heartbeat{font-size:12px}}
    `;
    document.head.appendChild(style);
  }

  function secondsSinceStart() {
    if (!startedAt) return 0;
    return Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  }

  function eventAgeSeconds(events, updatedAt) {
    const lastEvent = events.at(-1)?.timestamp;
    const source = lastEvent || updatedAt;
    if (!source) return 0;
    const value = new Date(source).getTime();
    return Number.isFinite(value) ? Math.max(0, Math.floor((Date.now() - value) / 1000)) : 0;
  }

  function heartbeatText(events, state, age) {
    if (TERMINAL.has(state)) {
      return {title: 'Миссия завершила выполнение.', detail: 'Итоговое состояние получено от сервера.', stale: false};
    }
    const last = events.at(-1);
    const currentWork = last?.next_action || last?.detail || last?.message || 'Ожидаем первый этап миссии.';
    if (age >= 45) {
      return {
        title: 'Длительная операция продолжается, новых этапов пока нет.',
        detail: `Последний сигнал ${age} сек. назад. Сейчас: ${currentWork} Проверка сервера выполняется каждые 1,2 сек.`,
        stale: true,
      };
    }
    if (age >= 15) {
      return {
        title: 'Система работает, ожидаем завершение текущей подзадачи.',
        detail: `Последний сигнал ${age} сек. назад. Сейчас: ${currentWork}`,
        stale: false,
      };
    }
    return {
      title: 'Связь с миссией активна.',
      detail: `Последний сигнал ${age} сек. назад. Сейчас: ${currentWork}`,
      stale: false,
    };
  }

  function renderReporter(events = [], state = 'queued', updatedAt = null) {
    installStyles();
    latestEvents = Array.isArray(events) ? events : [];
    latestState = state;
    latestUpdatedAt = updatedAt;
    const visible = latestEvents.slice(-8);
    const lastUpdate = updatedAt ? new Date(updatedAt) : new Date();
    const age = eventAgeSeconds(latestEvents, updatedAt);
    const heartbeat = heartbeatText(latestEvents, state, age);
    statusNode.innerHTML = `
      <div class="mission-reporter" data-state="${escapeHtml(state)}" role="status" aria-live="polite">
        <div class="mission-reporter__head">
          <div>
            <div class="mission-reporter__title">Живой репортаж миссии</div>
            <div class="mission-reporter__meta">Миссия ${escapeHtml(current?.mission_id || 'создаётся')} · прошло <span id="missionElapsed">${secondsSinceStart()}</span> сек. · обновлено ${escapeHtml(lastUpdate.toLocaleTimeString('ru-RU'))}</div>
          </div>
          <span class="mission-reporter__state">${escapeHtml(stateLabels[state] || state)}</span>
        </div>
        <div class="mission-heartbeat" data-stale="${heartbeat.stale}">
          <span class="mission-heartbeat__dot" aria-hidden="true"></span>
          <div><div class="mission-heartbeat__title">${escapeHtml(heartbeat.title)}</div><div class="mission-heartbeat__detail">${escapeHtml(heartbeat.detail)}</div></div>
        </div>
        <ol class="mission-reporter__events">
          ${visible.map(event => `
            <li class="mission-event">
              <span aria-hidden="true">${escapeHtml(iconLabels[event.icon_key] || '•')}</span>
              <div>
                <div class="mission-event__message">${escapeHtml(event.message)}</div>
                ${event.detail ? `<div class="mission-event__detail">${escapeHtml(event.detail)}</div>` : ''}
                ${event.next_action ? `<div class="mission-event__next">Далее: ${escapeHtml(event.next_action)}</div>` : ''}
                <div class="mission-event__time">${escapeHtml(new Date(event.timestamp).toLocaleTimeString('ru-RU'))}</div>
              </div>
            </li>`).join('')}
        </ol>
      </div>`;

    emitWorkspaceEvent('aimeton:analysis-update', {
      mission: current ? { ...current } : null,
      state,
      updated_at: updatedAt,
      events: latestEvents.map(event => ({ ...event })),
      terminal: TERMINAL.has(state),
    });
  }

  function refreshClockAndHeartbeat() {
    if (!current) return;
    renderReporter(latestEvents, latestState, latestUpdatedAt);
  }

  function rememberActive(value) {
    if (value) localStorage.setItem(ACTIVE_KEY, JSON.stringify(value));
    else localStorage.removeItem(ACTIVE_KEY);
  }

  async function readJson(url) {
    const response = await fetch(url, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function poll() {
    if (!current) return;
    try {
      const [status, events] = await Promise.all([
        readJson(current.status_url), readJson(current.events_url)
      ]);
      renderReporter(events, status.state, status.updated_at);
      const usage = status.research;
      document.querySelector('#researchUsage').textContent = (usage?.deep_research || usage?.settings_revision !== undefined && usage?.settings_revision !== null)
        ? `${usage.accounting_recovered ? 'Восстановлены сохранённые счётчики. ' : ''}Стоимость не подтверждена.${Object.keys(usage.search_cost_estimate_by_currency || {}).length ? ' Оценка поиска по тарифам: ' + Object.entries(usage.search_cost_estimate_by_currency).map(([currency, amount]) => `${amount} ${currency}`).join(', ') + '.' : ''} Документов: ${usage.documents_attempted || 0}; вызовов: ${usage.llm_calls}; токенов: ${usage.prompt_tokens + usage.completion_tokens}; обработано порций: ${usage.completed_chunks}.${Object.values(usage.spending_thresholds || {}).includes('reached') ? ' Плановый порог токенов достигнут; исследование продолжается.' : ''}${usage.progress_warning ? ' Исследование продолжается; можно дождаться результата или остановить.' : ''}${usage.stop_requested ? ' Останавливаем исследование…' : ''}` : '';
      document.querySelector('#stopResearch').hidden = !(usage?.deep_research || usage?.settings_revision !== undefined && usage?.settings_revision !== null) || TERMINAL.has(status.state) || !!usage?.accounting_recovered;
      if (TERMINAL.has(status.state)) {
        clearInterval(pollTimer);
        clearInterval(elapsedTimer);
        pollTimer = null;
        elapsedTimer = null;
        button.disabled = false;
        rememberActive(null);
        if (status.result) {
          analysis = status.result;
          activeAnalysisId = ensureAnalysisId(analysis);
          setChatSession([]);
          renderChatSession();
          render();
          saveToHistory(analysis);
          emitWorkspaceEvent('aimeton:analysis-complete', {
            mission: current ? { ...current } : null,
            state: status.state,
            updated_at: status.updated_at,
            result: status.result,
          });
        }
      }
    } catch (error) {
      renderReporter([{ timestamp: new Date().toISOString(), icon_key: 'alert-triangle', message: 'Не удалось получить обновление миссии.', detail: error.message, next_action: 'Повторная проверка выполняется автоматически.' }], 'stalled');
    }
  }

  async function launch(url) {
    const response = await fetch('/api/analyze/start', {
      method: 'POST',
      credentials: 'same-origin',
      headers: researchHeaders(),
      body: JSON.stringify({ url, ...await researchSettingsForLaunch("site-audit"),
        deep_research: document.querySelector('#deepResearch').checked,
        unlimited_llm_budget: document.querySelector('#deepResearch').checked }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    current = payload;
    startedAt = Date.now();
    rememberActive({ ...payload, started_at: startedAt });
    emitWorkspaceEvent('aimeton:analysis-started', { mission: { ...payload } });
    renderReporter([], payload.state, new Date().toISOString());
    await poll();
    pollTimer = setInterval(poll, 1200);
    elapsedTimer = setInterval(refreshClockAndHeartbeat, 1000);
  }

  document.querySelector('#stopResearch').onclick = async () => {
    if (!current) return;
    const response = await fetch(`/api/analyze/${encodeURIComponent(current.analysis_id)}/stop`, {
      method: 'POST', credentials: 'same-origin', headers: researchHeaders(),
    });
    if (!response.ok) alert('Не удалось остановить исследование. Повторите запрос.');
  };

  form.addEventListener('submit', async event => {
    event.preventDefault();
    event.stopImmediatePropagation();
    clearInterval(pollTimer);
    clearInterval(elapsedTimer);
    button.disabled = true;
    document.querySelector('#result')?.classList.add('hidden');
    document.querySelector('#chat')?.classList.add('hidden');
    try {
      await launch(document.querySelector('#url').value);
    } catch (error) {
      button.disabled = false;
      current = null;
      rememberActive(null);
      renderReporter([{ timestamp: new Date().toISOString(), icon_key: 'alert-triangle', message: 'Миссия не запущена.', detail: error.message, next_action: 'Проверьте адрес сайта и повторите запуск.' }], 'failed');
    }
  }, true);

  try {
    const saved = JSON.parse(localStorage.getItem(ACTIVE_KEY) || 'null');
    if (saved?.analysis_id && saved?.status_url && saved?.events_url) {
      current = saved;
      startedAt = Number(saved.started_at) || Date.now();
      button.disabled = true;
      emitWorkspaceEvent('aimeton:analysis-started', { mission: { ...saved }, resumed: true });
      poll();
      pollTimer = setInterval(poll, 1200);
      elapsedTimer = setInterval(refreshClockAndHeartbeat, 1000);
    }
  } catch {
    rememberActive(null);
  }
})();
