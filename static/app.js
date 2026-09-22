/* ── State ── */
let analysis = null;
let activeAnalysisId = null;

/* ── DOM refs ── */
const f           = document.querySelector('#form');
const statusEl    = document.querySelector('#status');
const resultEl    = document.querySelector('#result');
const chatEl      = document.querySelector('#chat');
const analyzeBtn  = document.querySelector('#analyzeBtn');
const messages    = document.querySelector('#messages');
const historyEl   = document.querySelector('#history');
const historyList = document.querySelector('#historyList');

/* ── Marked config ── */
marked.setOptions({ breaks: true, gfm: true });

/* ── Helpers ── */
function esc(v) {
  return String(v ?? '').replace(/[&<>'"]/g, c =>
    ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[c]));
}

function safeHref(v) {
  try {
    const url = new URL(String(v ?? ''));
    return ['http:', 'https:'].includes(url.protocol) ? url.href : '#';
  } catch {
    return '#';
  }
}

function researchHeaders() {
  const token = document.cookie.split('; ').find(x => x.startsWith('aimeton_csrf='))?.split('=').slice(1).join('=');
  return { 'Content-Type': 'application/json', ...(token ? { 'X-CSRF-Token': decodeURIComponent(token) } : {}) };
}

function setStatus(msg, loading) {
  statusEl.innerHTML = loading
    ? `<span class="spinner"></span>${esc(msg)}`
    : esc(msg);
}

/* ── History (localStorage) ── */
const HIST_KEY = 'aimeton_history';
const CHAT_KEY = 'aimeton_chat_sessions';

function newAnalysisId() {
  if (globalThis.crypto?.randomUUID) return `analysis_${crypto.randomUUID()}`;
  return `analysis_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function ensureAnalysisId(data) {
  if (!data.ui_analysis_id) data.ui_analysis_id = newAnalysisId();
  return data.ui_analysis_id;
}

function getChatSessions() {
  try { return JSON.parse(localStorage.getItem(CHAT_KEY) || '{}'); }
  catch { return {}; }
}

function setChatSession(session) {
  if (!activeAnalysisId) return;
  const sessions = getChatSessions();
  sessions[activeAnalysisId] = session.slice(-40);
  localStorage.setItem(CHAT_KEY, JSON.stringify(sessions));
}

function currentChatSession() {
  if (!activeAnalysisId) return [];
  return getChatSessions()[activeAnalysisId] || [];
}

function renderChatSession() {
  messages.innerHTML = '';
  currentChatSession().forEach(item => addMessage(item.content, item.role, false));
}

function getHistory() {
  try { return JSON.parse(localStorage.getItem(HIST_KEY) || '[]'); }
  catch { return []; }
}

function saveToHistory(data) {
  ensureAnalysisId(data);
  let h = getHistory();
  // replace if same URL already exists
  const replacedIds = h
    .filter(x => x.url === data.url && x.ui_analysis_id !== data.ui_analysis_id)
    .map(x => x.ui_analysis_id)
    .filter(Boolean);
  h = h.filter(x => x.url !== data.url);
  h.unshift({ saved_at: new Date().toISOString(), ...data });
  if (h.length > 30) h.length = 30;
  localStorage.setItem(HIST_KEY, JSON.stringify(h));
  if (replacedIds.length) {
    const sessions = getChatSessions();
    replacedIds.forEach(id => delete sessions[id]);
    localStorage.setItem(CHAT_KEY, JSON.stringify(sessions));
  }
  renderHistory();
}

function renderHistory() {
  const h = getHistory();
  if (!h.length) { historyEl.classList.add('hidden'); return; }
  historyEl.classList.remove('hidden');
  historyList.innerHTML = h.map((item, i) => {
    const score = item.commercial_opportunity?.score ?? '?';
    const date  = new Date(item.saved_at).toLocaleString('ru', { day:'2-digit', month:'2-digit', year:'2-digit', hour:'2-digit', minute:'2-digit' });
    return `
      <div class="history-item">
        <div class="history-item-info">
          <strong>${esc(item.company_name || item.url)}</strong>
          <span class="history-url">${esc(item.url)}</span>
          <span class="history-date">${date}</span>
        </div>
        <div class="history-item-actions">
          <span class="tag">${esc(score)}/100</span>
          <button class="btn-ghost btn-sm" onclick="loadFromHistory(${i})">Открыть</button>
          <button class="btn-ghost btn-sm btn-danger" onclick="deleteHistory(${i})" title="Удалить">✕</button>
        </div>
      </div>`;
  }).join('');
}

function loadFromHistory(i) {
  const h = getHistory();
  analysis = h[i];
  activeAnalysisId = ensureAnalysisId(analysis);
  h[i] = analysis;
  localStorage.setItem(HIST_KEY, JSON.stringify(h));
  render();
  renderChatSession();
  setStatus('Загружено из истории: ' + analysis.company_name);
  resultEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function deleteHistory(i) {
  const h = getHistory();
  const [deleted] = h.splice(i, 1);
  localStorage.setItem(HIST_KEY, JSON.stringify(h));
  if (deleted?.ui_analysis_id) {
    const sessions = getChatSessions();
    delete sessions[deleted.ui_analysis_id];
    localStorage.setItem(CHAT_KEY, JSON.stringify(sessions));
    if (activeAnalysisId === deleted.ui_analysis_id) {
      activeAnalysisId = null;
      messages.innerHTML = '';
      chatEl.classList.add('hidden');
    }
  }
  renderHistory();
}

document.querySelector('#clearHistoryBtn').onclick = () => {
  if (confirm('Очистить всю историю?')) {
    localStorage.removeItem(HIST_KEY);
    localStorage.removeItem(CHAT_KEY);
    activeAnalysisId = null;
    messages.innerHTML = '';
    renderHistory();
  }
};

/* ── Structured export ── */
function exportName(extension) {
  const name = (analysis?.company_name || 'report')
    .replace(/[^а-яёa-z0-9\s_-]/gi, '').trim().replace(/\s+/g, '_') || 'report';
  return `AIMETON_${name}.${extension}`;
}

async function exportStructured(format) {
  if (!analysis) return;
  const config = {
    md: {
      endpoint: '/api/export/analysis.md',
      extension: 'md',
      buttonId: 'exportMdBtn',
      pending: '⏳ Формирую Markdown…',
      ready: '⬇ Markdown',
    },
    docx: {
      endpoint: '/api/export/analysis.docx',
      extension: 'docx',
      buttonId: 'exportDocxBtn',
      pending: '⏳ Формирую Word…',
      ready: '⬇ Word (.docx)',
    },
  }[format];
  if (!config) return;
  const btn = document.querySelector(`#${config.buttonId}`);
  btn.disabled = true;
  btn.textContent = config.pending;
  try {
    const response = await fetch(config.endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(analysis),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    const blob = await response.blob();
    const href = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = href;
    link.download = exportName(config.extension);
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(href);
  } catch (err) {
    alert(`Не удалось сформировать ${format.toUpperCase()}: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = config.ready;
  }
}

/* ── PDF export (preliminary report only; chat is isolated) ── */
function exportPDF() {
  const btn = document.querySelector('#exportBtn');
  btn.disabled = true;
  btn.textContent = '⏳ Формирую PDF…';

  // Build a temporary container from the active analysis only.
  const container = document.createElement('div');
  container.style.cssText = 'font-family:Inter,system-ui,sans-serif;color:#152033;padding:4px';

  const reportClone = document.querySelector('#resultInner').cloneNode(true);
  container.appendChild(reportClone);

  // The container MUST be rendered inside the real document in normal flow:
  //  - position:fixed/absolute -> html2pdf 0.10.1 produces a 0-height (blank) canvas.
  //  - detached element + windowWidth override -> html2canvas mismatches the real
  //    viewport width and clips content on the left (worse the wider the window).
  // So we attach it in-flow, scroll to top, and let html2canvas use real coords.
  // A full-screen overlay hides the brief on-page flash.
  container.style.cssText +=
    // 718px = A4 width (210mm=794px) minus the 10mm PDF margins on each side:
    // html2pdf does NOT shrink content to fit inside margins — it shifts it,
    // so a 794px-wide container gets its right edge pushed off the page.
    ';width:718px;max-width:718px;background:white;padding:20px;box-sizing:border-box;margin:0;';

  // В PDF сетка карточек — одноколоночная: html2pdf при переносе двигает
  // каждую карточку отдельно, и в 2-колоночной сетке появляются "дырки".
  const pdfStyle = document.createElement('style');
  pdfStyle.textContent =
    '.pdf-export-container .grid{display:block !important}' +
    '.pdf-export-container .grid > *{margin-bottom:12px}';
  container.classList.add('pdf-export-container');
  container.appendChild(pdfStyle);

  const overlay = document.createElement('div');
  overlay.style.cssText =
    'position:fixed;inset:0;z-index:99999;background:#f4f6fb;' +
    'display:flex;align-items:center;justify-content:center;' +
    'font:600 16px Inter,system-ui,sans-serif;color:#5b61e6;';
  overlay.textContent = '⏳ Формирую PDF…';

  document.body.appendChild(container);
  document.body.appendChild(overlay);
  const prevScroll = window.scrollY;
  window.scrollTo(0, 0);

  const cleanup = () => {
    container.remove();
    overlay.remove();
    window.scrollTo(0, prevScroll);
    btn.disabled = false;
    btn.innerHTML = '⬇ PDF';
  };

  try {
  html2pdf()
    .set({
      margin: [10, 10, 10, 10],
      filename: exportName('pdf'),
      image: { type: 'jpeg', quality: 0.95 },
      html2canvas: { scale: 2, useCORS: true, logging: false },
      jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' },
      // Не разрывать карточки/панели между страницами PDF
      pagebreak: { mode: ['css', 'legacy'], avoid: ['.card', '.panel', '.company-card', '.export-row'] }
    })
    .from(container)
    .save()
    .finally(cleanup);
  } catch (err) {
    // Синхронная ошибка (например, html2pdf не загрузился) — убрать оверлей
    cleanup();
    alert('Не удалось сформировать PDF: ' + (err && err.message ? err.message : err));
  }
}

/* ── Analyze form ── */
f.onsubmit = async (e) => {
  e.preventDefault();
  analyzeBtn.disabled = true;
  setStatus('Исследуем экономические сигналы…', true);
  resultEl.classList.add('hidden');
  chatEl.classList.add('hidden');

  try {
    const r = await fetch('/api/analyze', {
      method: 'POST',
      headers: researchHeaders(),
      body: JSON.stringify({ url: document.querySelector('#url').value,
        deep_research: document.querySelector('#deepResearch').checked,
        unlimited_llm_budget: document.querySelector('#deepResearch').checked })
    });
    if (!r.ok) throw new Error((await r.json()).detail || 'Ошибка сервера');
    analysis = await r.json();
    activeAnalysisId = ensureAnalysisId(analysis);
    setChatSession([]);
    renderChatSession();
    render();
    saveToHistory(analysis);
    setStatus(
      analysis.target_applicability?.applicability === 'not_applicable'
        ? 'Этот адрес не подходит для аудита компании'
        : 'Коммерческая возможность подготовлена'
    );
  } catch (err) {
    setStatus('Ошибка: ' + err.message);
  } finally {
    analyzeBtn.disabled = false;
  }
};

/* ── Render results ── */
function render() {
  const applicability = analysis.target_applicability;
  if (applicability?.applicability === 'not_applicable') {
    resultEl.innerHTML = `
      <div id="resultInner">
        <div class="notice notice-warning">
          <strong>Аудит компании не запущен.</strong>
          Этот адрес классифицирован как неподходящий вход для восстановления профиля коммерческой организации.
        </div>
        <section class="panel">
          <h3>Классификация входа</h3>
          <p><strong>Тип:</strong> ${esc(applicability.target_kind || 'unknown')}</p>
          <p><strong>Уверенность:</strong> ${Math.round((Number(applicability.confidence) || 0) * 100)}%</p>
          <p><strong>Основание:</strong><br>${esc(applicability.reason || 'Недостаточно признаков сайта компании.')}</p>
          ${applicability.target_entity_name ? `<p><strong>Распознанный оператор/объект:</strong> ${esc(applicability.target_entity_name)}</p>` : ''}
          <p><strong>Что делать:</strong><br>Укажите официальный сайт конкретной коммерческой организации или её собственную страницу продукта/услуги.</p>
        </section>
      </div>
    `;
    resultEl.classList.remove('hidden');
    chatEl.classList.add('hidden');
    return;
  }

  const o = analysis.commercial_opportunity;
  const p = analysis.action_package;
  const facts = analysis.company_facts || [];
  const machine = analysis.business_machine_4x4 || [];
  const sources = analysis.sources || [];
  const readiness = analysis.readiness || {
    analysis_state: 'preliminary_hypothesis',
    client_release_eligible: false,
    sufficiency_level: 'L0',
    identity_state: 'unresolved',
    profile_completeness: 0,
    evidence_quality: 0,
    commercial_priority: o.score || 0,
    budget_state: 'unknown',
    provider_states: {},
    required_verticals: [],
    release_blockers: ['legacy_result_without_release_state']
  };

  const score = Number(o.score) || 0;
  const scoreClass = score >= 70 ? 'high' : score >= 40 ? 'mid' : 'low';

  resultEl.innerHTML = `
    <div class="export-row">
      <button class="btn-export" id="exportBtn" onclick="exportPDF()">
        ⬇ PDF
      </button>
      <button class="btn-export" id="exportMdBtn" onclick="exportStructured('md')">
        ⬇ Markdown
      </button>
      <button class="btn-export" id="exportDocxBtn" onclick="exportStructured('docx')">
        ⬇ Word (.docx)
      </button>
    </div>

    <div id="resultInner">
      <div class="notice notice-warning">
        <strong>Предварительный результат.</strong>
        Этот анализ ещё не является подписанным Report v1 и не прошёл human sign-off.
        Диалог консультанта хранится отдельно и в экспорт не включается.
      </div>

      <section class="panel">
        <h3>Состояние допустимости результата</h3>
        <p>
          <strong>Выпуск клиенту:</strong> ${readiness.client_release_eligible ? 'разрешён' : 'заблокирован'} ·
          <strong>AI-анализ:</strong> ${esc(readiness.analysis_state)} ·
          <strong>УДП:</strong> ${esc(readiness.sufficiency_level)} ·
          <strong>Identity:</strong> ${esc(readiness.identity_state)}
        </p>
        <p>
          <strong>Полнота профиля:</strong> ${Math.round((Number(readiness.profile_completeness) || 0) * 100)}% ·
          <strong>Качество evidence:</strong> ${Math.round((Number(readiness.evidence_quality) || 0) * 100)}% ·
          <strong>Коммерческий приоритет:</strong> ${esc(readiness.commercial_priority)}/100 ·
          <strong>Budget:</strong> ${esc(readiness.budget_state)}
        </p>
        <p><strong>Providers:</strong> ${esc(Object.entries(readiness.provider_states || {}).map(([key, value]) => `${key}=${value}`).join(', ') || 'not_reported')}</p>
        <p><strong>Обязательные вертикали:</strong> ${esc((readiness.required_verticals || []).map(item => `${item.code}=${item.state}`).join(', ') || 'not_reported')}</p>
        <p><strong>Блокеры:</strong> ${esc((readiness.release_blockers || []).join(', ') || '—')}</p>
      </section>

      <!-- Company card -->
      <div class="company-card">
        <div class="company-card-body">
          <h2>${esc(analysis.company_name)}</h2>
          <a class="company-url" href="${esc(safeHref(analysis.url))}" target="_blank" rel="noopener">${esc(analysis.url)}</a>
          <p class="company-summary">${esc(analysis.business_summary)}</p>
        </div>
        <div class="company-card-score">
          <div class="score-circle ${scoreClass}">${esc(o.score)}</div>
          <div class="score-label">${esc(o.qualification)}</div>
        </div>
      </div>

      <!-- Commercial opportunity -->
      <section class="panel">
        <h3>Коммерческая возможность — ${esc(o.opportunity_type)}</h3>
        <p><strong>Гипотеза проблемы</strong><br>${esc(o.problem_hypothesis)}</p>
        <p><strong>Рекомендуемое решение</strong><br>${esc(o.recommended_solution)}</p>
        <p><strong>Ожидаемая ценность</strong><br>${esc(o.expected_value)}</p>
      </section>

      <!-- Company facts -->
      <h3>Факты о компании</h3>
      <div class="table-scroll">
        <table class="data-table">
          <thead><tr><th>Поле</th><th>Значение</th><th>Период</th><th>Уверенность</th><th>Источники</th></tr></thead>
          <tbody>
            ${facts.length ? facts.map(item => `
              <tr>
                <td>${esc(item.field)}</td>
                <td>${esc(item.value)}</td>
                <td>${esc(item.period || '—')}</td>
                <td>${esc(item.confidence)}</td>
                <td>${esc((item.source_ids || []).join(', ') || '—')}</td>
              </tr>`).join('') : '<tr><td colspan="5">Проверенных фактов нет.</td></tr>'}
          </tbody>
        </table>
      </div>

      <!-- Economic signals -->
      <h3>Экономические сигналы</h3>
      <div class="grid">
        ${analysis.economic_signals.map(s => `
          <article class="card">
            <span class="tag">Уверенность: ${esc(s.confidence)}</span>
            <h3>${esc(s.signal)}</h3>
            <p><strong>Основание</strong><br>${esc(s.evidence)}</p>
            <p><strong>Возможный эффект</strong><br>${esc(s.business_effect)}</p>
          </article>`).join('')}
      </div>

      <!-- KM business machine -->
      <h3>Бизнес-машина AIMETON 4×4</h3>
      <div class="grid">
        ${machine.length ? machine.map(item => `
          <article class="card">
            <span class="tag">${esc(item.code)} · ${esc(item.status)}</span>
            <h3>${esc(item.vertex)}</h3>
            <p class="muted">${esc(item.detail_operator)}</p>
            <p>${esc(item.finding)}</p>
            <p><strong>Источники</strong><br>${esc((item.source_ids || []).join(', ') || '—')}</p>
            <p><strong>Значение для продажи</strong><br>${esc(item.sales_relevance || '—')}</p>
          </article>`).join('') : '<p>Нет данных.</p>'}
      </div>

      <!-- AI agents -->
      <h3>Подходящие AI-инструменты</h3>
      <div class="grid">
        ${analysis.agents.map(a => `
          <article class="card">
            <span class="tag">${esc(a.priority)}</span>
            <h3>${esc(a.name)}</h3>
            <p>${esc(a.purpose)}</p>
            <strong>Польза</strong>
            <p>${esc(a.benefit)}</p>
          </article>`).join('')}
      </div>

      <!-- Action package -->
      <section class="panel">
        <h3>Пакет действия</h3>
        <p><strong>Предполагаемый ЛПР</strong><br>${esc(p.decision_maker_hypothesis)}</p>
        <p><strong>Основание для контакта</strong><br>${esc(p.contact_reason)}</p>
        <p><strong>Демонстрационный сценарий</strong></p>
        <ol>${p.demo_scenario.map(x => `<li>${esc(x)}</li>`).join('')}</ol>
        <p><strong>Первое сообщение</strong></p>
        <blockquote>${esc(p.first_message)}</blockquote>
        <p><strong>Следующий шаг</strong><br>${esc(p.next_action)}</p>
      </section>

      <!-- Sources -->
      <h3>Источники</h3>
      <div class="source-list">
        ${sources.length ? sources.map(source => `
          <article class="source-card">
            <div>
              <span class="tag">${esc(source.id)} · ${esc(source.evidence_level)}</span>
              <h3>${esc(source.title)}</h3>
              <a href="${esc(safeHref(source.url))}" target="_blank" rel="noopener">${esc(source.url)}</a>
            </div>
            <p><strong>Цитата</strong><br>${esc(source.evidence_quote)}</p>
            <p class="muted">Проверено: ${esc(source.accessed_at)} · Тип: ${esc(source.source_type)}</p>
          </article>`).join('') : '<p>Источники не представлены.</p>'}
      </div>

      <h3>Промежуточный профиль · ревизия ${Number(analysis.profile_revision) || 0}</h3>
      <p>Проверено документов: ${Number(analysis.research_status?.verified_documents) || 0}.
      Ожидают проверки: ${Number(analysis.research_status?.unverified_documents) || 0}.
      Поиск: ${esc(analysis.research_status?.search_state || 'не выполнялся')}.</p>
      ${(analysis.research_queries || []).length ? `<details><summary>План уточняющего поиска</summary><ul>${analysis.research_queries.map(x => `<li>${esc(x)}</li>`).join('')}</ul></details>` : ''}
      <p>Какие сведения уточнить дальше: продукты и услуги, руководство, филиалы, финансы или реквизиты?</p>
      ${(analysis.user_clarifications || []).length ? `<h3>Уточнения пользователя — требуют проверки</h3><ul>${analysis.user_clarifications.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}

      <!-- Assumptions -->
      <h3>Ограничения и предположения</h3>
      <ul>${analysis.risks_and_assumptions.map(x => `<li>${esc(x)}</li>`).join('')}</ul>
    </div>
  `;

  resultEl.classList.remove('hidden');
  chatEl.classList.remove('hidden');
}

/* ── Chat ── */
function addMessage(text, role, persist = true) {
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  if (role === 'assistant') {
    d.innerHTML = marked.parse(text);
  } else {
    d.textContent = text;
  }
  messages.append(d);
  messages.scrollTop = messages.scrollHeight;
  if (persist) {
    const session = currentChatSession();
    session.push({ role, content: text });
    setChatSession(session);
  }
  return d;
}

let activeChatResearch = null;
document.querySelector('#chatDeepResearch').addEventListener('change', (event) => {
  if (event.target.checked) document.querySelector('#refineSearch').checked = true;
});
document.querySelector('#stopChatResearch').onclick = async () => {
  if (!activeChatResearch) return;
  const response = await fetch(`/api/analyze/${encodeURIComponent(activeChatResearch.analysis_id)}/stop`, {
    method: 'POST', credentials: 'same-origin', headers: researchHeaders(),
  });
  if (!response.ok) alert('Не удалось остановить исследование. Повторите запрос.');
};

async function deepChatResearch(payload, thinking) {
  const response = await fetch('/api/analyze/refine/start', {
    method: 'POST', credentials: 'same-origin', headers: researchHeaders(), body: JSON.stringify(payload),
  });
  const started = await response.json();
  if (!response.ok) throw new Error(started.detail || `HTTP ${response.status}`);
  activeChatResearch = started;
  const stop = document.querySelector('#stopChatResearch');
  stop.hidden = false;
  while (true) {
    await new Promise(resolve => setTimeout(resolve, 1200));
    let status;
    try {
      const update = await fetch(started.status_url, { credentials: 'same-origin' });
      if (!update.ok) throw new Error(`HTTP ${update.status}`);
      status = await update.json();
    } catch {
      thinking.textContent = 'Восстанавливаем связь с исследованием. Кнопка остановки доступна.';
      continue;
    }
    const usage = status.research || {};
    thinking.textContent = `Углублённое исследование: документов ${usage.documents_attempted || 0}, LLM-вызовов ${usage.llm_calls || 0}, токенов ${(usage.prompt_tokens || 0) + (usage.completion_tokens || 0)}, обработано порций ${usage.completed_chunks || 0}.${usage.stop_requested ? ' Останавливаем…' : ''}`;
    if (['completed', 'failed', 'blocked', 'degraded'].includes(status.state)) {
      stop.hidden = true;
      activeChatResearch = null;
      if (!status.result) throw new Error('Исследование прервано; прежний профиль сохранён.');
      return { analysis: status.result, reply: status.dialogue_reply || 'Уточнённый профиль сохранён.' };
    }
  }
}

document.querySelector('#chatForm').onsubmit = async (e) => {
  e.preventDefault();
  const q       = document.querySelector('#question');
  const chatBtn = document.querySelector('#chatBtn');
  const text    = q.value.trim();
  if (!text) return;
  q.value = '';
  chatBtn.disabled = true;

  const requestAnalysisId = activeAnalysisId;
  const requestAnalysis = analysis;
  addMessage(text, 'user');

  const thinking = document.createElement('div');
  thinking.className = 'msg assistant thinking';
  thinking.innerHTML = '<span class="spinner"></span> Формирую ответ…';
  messages.append(thinking);
  messages.scrollTop = messages.scrollHeight;

  try {
    const deep = document.querySelector('#chatDeepResearch').checked;
    const payload = {
      analysis: requestAnalysis,
      refine_search: deep || document.querySelector('#refineSearch').checked,
      deep_research: deep,
      unlimited_llm_budget: deep,
      messages: currentChatSession().slice(-12),
    };
    let data;
    if (deep) {
      data = await deepChatResearch(payload, thinking);
    } else {
      const r = await fetch('/api/chat', {
        method: 'POST', headers: researchHeaders(), body: JSON.stringify(payload),
      });
      data = await r.json();
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
    }
    thinking.remove();
    if (activeAnalysisId !== requestAnalysisId) return;
    if (data.analysis) {
      analysis = { ...data.analysis, ui_analysis_id: requestAnalysisId };
      saveToHistory(analysis);
      render();
    }
    addMessage(data.reply, 'assistant');
  } catch (err) {
    thinking.remove();
    if (activeAnalysisId === requestAnalysisId) addMessage('Ошибка: ' + err.message, 'assistant');
  } finally {
    chatBtn.disabled = false;
    q.focus();
  }
};

/* ── Init ── */
renderHistory();
