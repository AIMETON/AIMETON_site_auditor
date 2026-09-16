(() => {
  const root = document.querySelector('#businessAuditWorkspace');
  if (!root) return;

  const state = {
    mission: null,
    runtimeState: null,
    events: [],
    result: null,
    updatedAt: null,
  };

  const asArray = value => Array.isArray(value) ? value : [];
  const asText = value => value == null ? '' : String(value);
  const esc = value => asText(value).replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));

  function safeHref(value) {
    try {
      const url = new URL(asText(value));
      return ['http:', 'https:'].includes(url.protocol) ? url.href : '#';
    } catch {
      return '#';
    }
  }

  function lastEvent() {
    return state.events.length ? state.events[state.events.length - 1] : null;
  }

  function renderTimeline() {
    if (!state.events.length) {
      return '<p class="baw-empty">Ожидаем первое подтверждённое событие миссии.</p>';
    }
    const recent = state.events.slice(-6).reverse();
    return `<details class="baw-details baw-timeline-details" ${state.events.length <= 3 ? 'open' : ''}>
      <summary>Ход миссии · ${state.events.length} событий</summary>
      <ol class="baw-timeline">${recent.map(event => `
        <li class="baw-event">
          <span class="baw-event__icon" aria-hidden="true">${esc(event.icon || '•')}</span>
          <div>
            <strong>${esc(event.message || event.phase || event.event_code || 'Событие миссии')}</strong>
            ${event.detail ? `<p>${esc(event.detail)}</p>` : ''}
            ${event.next_action ? `<p class="baw-muted">Далее: ${esc(event.next_action)}</p>` : ''}
            ${event.timestamp ? `<time>${esc(new Date(event.timestamp).toLocaleTimeString('ru-RU'))}</time>` : ''}
          </div>
        </li>`).join('')}</ol>
    </details>`;
  }

  function renderFacts(result) {
    const facts = asArray(result.company_facts);
    if (!facts.length) return '';
    return `<section class="baw-section">
      <div class="baw-section__head"><h3>Подтверждённые факты</h3><span>${facts.length}</span></div>
      <div class="baw-facts">${facts.map(item => `
        <article class="baw-fact">
          <strong>${esc(item.field)}</strong>
          <p>${esc(item.value)}</p>
          <small>${item.confidence ? `Уверенность: ${esc(item.confidence)}` : ''}${item.period ? ` · ${esc(item.period)}` : ''}</small>
        </article>`).join('')}</div>
    </section>`;
  }

  function renderDecisionCard(result) {
    const opportunity = result.commercial_opportunity || {};
    const action = result.action_package || {};
    const hasOpportunity = Object.keys(opportunity).length > 0;
    return `<section class="baw-hero baw-decision-card">
      <p class="baw-kicker">Решение для руководителя</p>
      <div class="baw-hero__row">
        <div>
          <h2>${esc(result.company_name || 'Компания')}</h2>
          ${result.url ? `<a href="${esc(safeHref(result.url))}" target="_blank" rel="noopener">${esc(result.url)}</a>` : ''}
        </div>
        ${hasOpportunity && (opportunity.score != null || opportunity.qualification) ? `<div class="baw-decision-score">${opportunity.score != null ? `<span class="baw-score">${esc(opportunity.score)}/100</span>` : ''}${opportunity.qualification ? `<span class="baw-chip">${esc(opportunity.qualification)}</span>` : ''}</div>` : ''}
      </div>
      ${result.business_summary ? `<p class="baw-summary">${esc(result.business_summary)}</p>` : ''}
      ${hasOpportunity ? `<div class="baw-decision-flow">
        ${(opportunity.opportunity_type || opportunity.problem_hypothesis) ? `<div><span class="baw-decision-label">Главная возможность</span>${opportunity.opportunity_type ? `<h3>${esc(opportunity.opportunity_type)}</h3>` : ''}${opportunity.problem_hypothesis ? `<p>${esc(opportunity.problem_hypothesis)}</p>` : ''}</div>` : ''}
        ${opportunity.expected_value ? `<div><span class="baw-decision-label">Почему это важно</span><p>${esc(opportunity.expected_value)}</p></div>` : ''}
        ${opportunity.recommended_solution ? `<div><span class="baw-decision-label">Что предлагает AIMETON</span><p>${esc(opportunity.recommended_solution)}</p></div>` : ''}
        ${action.next_action ? `<div class="baw-decision-next"><span class="baw-decision-label">Ближайший шаг</span><strong>${esc(action.next_action)}</strong></div>` : ''}
      </div>` : ''}
    </section>`;
  }

  function renderAgents(result) {
    const agents = asArray(result.agents);
    if (!agents.length) return '';
    return `<section class="baw-section">
      <div class="baw-section__head"><h3>AI-возможности</h3><span>${agents.length}</span></div>
      <div class="baw-grid">${agents.map(agent => `
        <article class="baw-card">
          <div class="baw-card__top">${agent.priority ? `<span class="baw-chip">${esc(agent.priority)}</span>` : ''}${agent.name ? `<h4>${esc(agent.name)}</h4>` : ''}</div>
          ${agent.purpose ? `<p><strong>Задача:</strong> ${esc(agent.purpose)}</p>` : ''}
          ${agent.benefit ? `<p class="baw-value"><strong>Практическая польза:</strong> ${esc(agent.benefit)}</p>` : ''}
        </article>`).join('')}</div>
    </section>`;
  }

  function renderZoneCard(zone) {
    return `<article class="baw-card">
      <div class="baw-card__meta">${esc(zone.code)}${zone.status ? ` · ${esc(zone.status)}` : ''}</div>
      ${zone.vertex ? `<h4>${esc(zone.vertex)}</h4>` : ''}
      ${zone.finding ? `<p>${esc(zone.finding)}</p>` : ''}
      ${zone.sales_relevance ? `<p class="baw-value"><strong>Коммерческое значение:</strong> ${esc(zone.sales_relevance)}</p>` : ''}
    </article>`;
  }

  function renderZones(result) {
    const zones = asArray(result.business_machine_4x4);
    if (!zones.length) return '';
    const prioritized = [...zones].sort((a, b) => Number(Boolean(b.sales_relevance)) - Number(Boolean(a.sales_relevance)));
    const featured = prioritized.slice(0, Math.min(5, prioritized.length));
    const remaining = prioritized.slice(featured.length);
    const statusCounts = zones.reduce((acc, zone) => {
      const key = asText(zone.status || 'без статуса');
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {});
    return `<section class="baw-section">
      <div class="baw-section__head"><h3>Анализ бизнеса по 16 зонам</h3><span>${zones.length}</span></div>
      <div class="baw-zone-summary">${Object.entries(statusCounts).map(([status, count]) => `<span class="baw-chip">${esc(status)} · ${count}</span>`).join('')}</div>
      <div class="baw-grid">${featured.map(renderZoneCard).join('')}</div>
      ${remaining.length ? `<details class="baw-details baw-zone-details"><summary>Показать все зоны (${zones.length})</summary><div class="baw-grid">${remaining.map(renderZoneCard).join('')}</div></details>` : ''}
    </section>`;
  }

  function renderAction(result) {
    const action = result.action_package || {};
    if (!Object.keys(action).length) return '';
    const scenario = asArray(action.demo_scenario);
    return `<section class="baw-section baw-next">
      <p class="baw-kicker">Предлагаемый следующий шаг</p>
      ${action.next_action ? `<h3>${esc(action.next_action)}</h3>` : '<h3>Перейти от анализа к пилоту</h3>'}
      ${action.contact_reason ? `<p>${esc(action.contact_reason)}</p>` : ''}
      ${action.decision_maker_hypothesis ? `<p><strong>Кому адресовать:</strong> ${esc(action.decision_maker_hypothesis)}</p>` : ''}
      ${scenario.length ? `<details class="baw-details"><summary>Сценарий демонстрации</summary><ol>${scenario.map(step => `<li>${esc(step)}</li>`).join('')}</ol></details>` : ''}
      ${action.first_message ? `<details class="baw-details"><summary>Черновик первого сообщения</summary><blockquote>${esc(action.first_message)}</blockquote></details>` : ''}
    </section>`;
  }

  function renderEvidence(result) {
    const sources = asArray(result.sources);
    const facts = asArray(result.company_facts);
    const assumptions = asArray(result.risks_and_assumptions);
    if (!sources.length && !facts.length && !assumptions.length) return '';
    return `<section class="baw-section">
      <details class="baw-details">
        <summary>Почему AIMETON так решил? · ${facts.length} фактов · ${sources.length} источников</summary>
        ${sources.length ? `<div class="baw-sources">${sources.map(source => `
          <article>
            <strong>${esc(source.title || source.id)}</strong>
            ${source.evidence_level ? `<span class="baw-chip">${esc(source.evidence_level)}</span>` : ''}
            ${source.url ? `<a href="${esc(safeHref(source.url))}" target="_blank" rel="noopener">${esc(source.url)}</a>` : ''}
            ${source.evidence_quote ? `<details class="baw-source-detail"><summary>Показать подтверждение</summary><p>${esc(source.evidence_quote)}</p>${source.accessed_at ? `<small>Проверено: ${esc(source.accessed_at)}</small>` : ''}${source.source_type ? `<small> · Тип: ${esc(source.source_type)}</small>` : ''}</details>` : ''}
          </article>`).join('')}</div>` : ''}
        ${assumptions.length ? `<div class="baw-assumptions"><h4>Что требует проверки</h4><ul>${assumptions.map(item => `<li>${esc(item)}</li>`).join('')}</ul></div>` : ''}
      </details>
    </section>`;
  }

  function renderQuality(result) {
    const readiness = result.readiness || {};
    if (!Object.keys(readiness).length) return '';
    const blockers = asArray(readiness.release_blockers);
    const providerEntries = Object.entries(readiness.provider_states || {});
    return `<section class="baw-section">
      <details class="baw-details">
        <summary>Качество и достоверность анализа</summary>
        <div class="baw-quality">
          ${readiness.evidence_quality != null ? `<p><strong>Качество evidence:</strong> ${esc(Math.round(Number(readiness.evidence_quality) * 100))}%</p>` : ''}
          ${readiness.profile_completeness != null ? `<p><strong>Полнота профиля:</strong> ${esc(Math.round(Number(readiness.profile_completeness) * 100))}%</p>` : ''}
          ${readiness.identity_state ? `<p><strong>Идентификация:</strong> ${esc(readiness.identity_state)}</p>` : ''}
          ${readiness.sufficiency_level ? `<p><strong>Достаточность:</strong> ${esc(readiness.sufficiency_level)}</p>` : ''}
        </div>
        ${(readiness.analysis_state || blockers.length || providerEntries.length) ? `<details class="baw-details baw-technical"><summary>Технические ограничения</summary>${readiness.analysis_state ? `<p><strong>Состояние анализа:</strong> ${esc(readiness.analysis_state)}</p>` : ''}${blockers.length ? `<p><strong>Блокеры выпуска:</strong> ${esc(blockers.join(', '))}</p>` : ''}${providerEntries.length ? `<p><strong>Провайдеры:</strong> ${esc(providerEntries.map(([key, value]) => `${key}=${value}`).join(', '))}</p>` : ''}</details>` : ''}
      </details>
    </section>`;
  }

  function renderResult(result) {
    if (!result) return '';
    return `<div class="baw-result">
      ${renderDecisionCard(result)}
      ${renderAgents(result)}
      ${renderZones(result)}
      ${renderAction(result)}
      ${renderFacts(result)}
      ${renderEvidence(result)}
      ${renderQuality(result)}
      <section class="baw-section baw-continue">
        <h3>Продолжить работу с результатом</h3>
        <p>Чат и экспорт Markdown, Word и PDF доступны ниже в существующем интерфейсе AIMETON.</p>
      </section>
    </div>`;
  }

  function renderWorkspace() {
    const latest = lastEvent();
    const mission = state.mission || {};
    const running = !state.result;
    const partial = state.result?.research_status?.result_quality === 'partial';
    root.innerHTML = `
      <div class="baw-shell">
        <section class="baw-runtime ${running ? '' : 'baw-runtime--done'}">
          <div class="baw-runtime__head">
            <div>
              <p class="baw-kicker">AIMETON · живая миссия</p>
              <h2>${partial ? 'Исследование не завершено — сохранён частичный отчёт' : running ? 'Исследование бизнеса выполняется' : 'Исследование завершено'}</h2>
              <p class="baw-muted">${mission.mission_id ? `Миссия ${esc(mission.mission_id)}` : 'Миссия создаётся'}</p>
            </div>
            <span class="baw-state">${esc(state.runtimeState || 'queued')}</span>
          </div>
          ${latest ? `<div class="baw-current"><span>${esc(latest.icon || '●')}</span><div><strong>${esc(latest.message || 'Выполняется')}</strong>${latest.detail ? `<p>${esc(latest.detail)}</p>` : ''}${latest.next_action ? `<p class="baw-muted">Далее: ${esc(latest.next_action)}</p>` : ''}</div></div>` : ''}
          ${running ? renderTimeline() : ''}
        </section>
        ${renderResult(state.result)}
      </div>`;
  }

  window.addEventListener('aimeton:analysis-started', event => {
    state.mission = event.detail?.mission || null;
    state.runtimeState = state.mission?.state || 'queued';
    state.events = [];
    state.result = null;
    state.updatedAt = null;
    root.hidden = false;
    renderWorkspace();
  });

  window.addEventListener('aimeton:analysis-update', event => {
    state.mission = event.detail?.mission || state.mission;
    state.runtimeState = event.detail?.state || state.runtimeState;
    state.events = asArray(event.detail?.events);
    state.updatedAt = event.detail?.updated_at || null;
    root.hidden = false;
    renderWorkspace();
  });

  for (const eventName of ['aimeton:analysis-complete', 'aimeton:analysis-partial']) {
  window.addEventListener(eventName, event => {
    state.mission = event.detail?.mission || state.mission;
    state.runtimeState = event.detail?.state || state.runtimeState;
    state.result = event.detail?.result || null;
    state.updatedAt = event.detail?.updated_at || null;
    root.hidden = false;
    renderWorkspace();
  });
  }
})();
