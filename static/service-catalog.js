(() => {
  const cards = [...document.querySelectorAll('[data-service-card]')];
  const panels = [...document.querySelectorAll('[data-service-panel]')];

  const SUPPORTING_HOSTS = new Set([
    'prodoctorov.ru', '32top.ru', 'zoon.ru', 'flamp.ru', 'otzovik.com', 'irecommend.ru',
    'rusprofile.ru', 'checko.ru', 'list-org.com', 'audit-it.ru', 'companies.rbc.ru',
    'spark-interfax.ru', 'sbis.ru', 'kp.ru', 'rbc.ru', 'tass.ru', 'ria.ru',
    'vedomosti.ru', 'kommersant.ru', 'interfax.ru', 'dentistfind.ru', 'infodoctor.ru',
    'docdoc.ru', 'jsprav.ru', 'zubbo.ru', 'kleos.ru', 'dent-list.ru',
    'krasotaimedicina.ru', 'startsmile.ru', 'stomotologiya.ru', 'alldantist.ru',
    'doctu.ru', 'zdravzdrav.ru', 'totadres.ru', 'like.doctor', 'gdevrach.com',
    'dentistpro.ru', 'dentalclinics.care', 'napopravku.ru', 'vc.ru', 'wikipedia.org',
    'poidata.io', 'barb.pro', '1dentist.ru', 'yp.ru', '2gis.ru', 'vk.com', 'vk.ru', 'yandex.com', 'yandex.ru',
  ]);
  const SUPPORTING_TITLE_MARKERS = [
    'рейтинг', 'лучшие', 'отзывы', 'каталог', 'список', 'обзор', 'топ ',
    'новости', 'рейтинг клиник', 'рейтинг стоматолог', 'врачи', 'про докторов',
  ];

  function clearSelection() {
    cards.forEach(card => card.setAttribute('aria-pressed', 'false'));
    panels.forEach(panel => { panel.hidden = true; });
  }

  function selectService(name) {
    cards.forEach(card => card.setAttribute('aria-pressed', String(card.dataset.serviceCard === name)));
    panels.forEach(panel => { panel.hidden = panel.dataset.servicePanel !== name; });
    const active = panels.find(panel => panel.dataset.servicePanel === name);
    active?.scrollIntoView({behavior: 'smooth', block: 'start'});
    active?.querySelector('input')?.focus({preventScroll: true});
  }

  cards.forEach(card => {
    if (!card.disabled) card.addEventListener('click', () => selectService(card.dataset.serviceCard));
  });

  function setStatus(node, message, kind = '') {
    node.textContent = message;
    node.className = `service-status ${kind}`.trim();
  }

  async function postJson(path, payload) {
    const response = await fetch(path, {
      method: 'POST',
      credentials: 'same-origin',
      headers: researchHeaders(),
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : data.detail?.reason;
      throw new Error(detail || `HTTP ${response.status}`);
    }
    return data;
  }

  function safeHttpUrl(value) {
    if (!value) return '';
    try {
      const url = new URL(String(value));
      return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch {
      return '';
    }
  }

  function hostOf(value) {
    try {
      return new URL(value).hostname.toLowerCase().replace(/^www\./, '');
    } catch {
      return '';
    }
  }

  function isSupportingHost(host) {
    return [...SUPPORTING_HOSTS].some(known => host === known || host.endsWith(`.${known}`));
  }

  function classifyCandidate(candidate) {
    const url = safeHttpUrl(candidate.url || candidate.official_url || candidate.website);
    const host = hostOf(url);
    const sourceText = `${candidate.source_title || ''} ${candidate.source_snippet || ''}`.toLowerCase();
    if (isSupportingHost(host) || SUPPORTING_TITLE_MARKERS.some(marker => sourceText.includes(marker))) {
      return {kind: 'supporting', label: 'Источник для проверки'};
    }
    if (candidate.region_confirmed === false) {
      return {kind: 'observation', label: 'Регион не подтверждён'};
    }
    if (candidate.deep_analysis_performed === true) {
      return {kind: 'company', label: 'Компания-кандидат'};
    }
    return {kind: 'observation', label: 'Наблюдение'};
  }

  function leadFitPresentation(candidate) {
    const fit = String(candidate.lead_fit || '');
    const labels = {
      commercial_candidate: 'Коммерческий кандидат',
      unknown_candidate: 'Коммерческий статус не подтверждён',
      institutional_candidate: 'Институциональная организация',
    };
    if (!labels[fit]) return null;
    return {fit, label: labels[fit], reason: String(candidate.lead_fit_reason || '').trim()};
  }

  function priorityLabel(score, qualification) {
    if (score == null) return qualification || 'Недостаточно данных';
    if (score >= 70) return 'Высокий приоритет';
    if (score >= 55) return 'Перспективный';
    return qualification === 'Недостаточно данных' ? qualification : 'Наблюдение';
  }

  function appendItem(container, title, text, meta = '') {
    const item = document.createElement('article');
    item.className = 'service-summary__item';
    const heading = document.createElement('strong');
    heading.textContent = title;
    const body = document.createElement('div');
    body.textContent = text || 'Нет данных';
    item.append(heading, body);
    if (meta) {
      const small = document.createElement('div');
      small.className = 'service-summary__meta';
      small.textContent = meta;
      item.append(small);
    }
    container.append(item);
  }

  function appendGroup(container, title, description, kind) {
    const section = document.createElement('section');
    section.className = `hunter-group hunter-group--${kind}`;
    const heading = document.createElement('h3');
    heading.textContent = title;
    const note = document.createElement('p');
    note.className = 'hunter-group__note';
    note.textContent = description;
    const list = document.createElement('div');
    list.className = 'hunter-group__list';
    section.append(heading, note, list);
    container.append(section);
    return list;
  }

  function handoffCandidate(candidate, fallbackRegion) {
    const name = String(candidate.company_name || '').trim();
    const url = safeHttpUrl(candidate.url || candidate.official_url || candidate.website);
    const region = String(candidate.region || fallbackRegion || '').trim();
    document.querySelector('#companyName').value = name;
    document.querySelector('#companyUrl').value = url;
    document.querySelector('#companyRegion').value = region;
    const form = document.querySelector('#companyIntelligenceForm');
    if (form) {
      form.dataset.hunterLeadFit = String(candidate.lead_fit || '');
      form.dataset.hunterLeadFitReason = String(candidate.lead_fit_reason || '');
    }
    selectService('company-intelligence');
    const status = document.querySelector('#companyIntelligenceStatus');
    setStatus(status, 'Данные кандидата перенесены. Проверьте их и явно запустите исследование.', 'success');
    document.querySelector('#companyName').focus({preventScroll: true});
  }

  function appendCandidate(container, candidate, fallbackRegion) {
    const classification = classifyCandidate(candidate);
    const leadFit = leadFitPresentation(candidate);
    const item = document.createElement('article');
    item.className = `service-summary__item service-summary__candidate service-summary__candidate--${classification.kind}`;
    const name = String(candidate.company_name || candidate.url || 'Результат без названия').trim();
    const url = safeHttpUrl(candidate.url || candidate.official_url || candidate.website);
    const region = String(candidate.region || fallbackRegion || '').trim();
    const summary = candidate.recommended_solution || candidate.business_summary || '';
    const score = candidate.final_score ?? candidate.preliminary_score;
    const qualification = candidate.qualification || 'не определена';
    const nameOnly = !url && !summary && score == null;

    const top = document.createElement('div');
    top.className = 'service-summary__candidate-top';
    const heading = document.createElement('strong');
    heading.textContent = name;
    const badge = document.createElement('span');
    badge.className = `service-summary__kind service-summary__kind--${classification.kind}`;
    badge.textContent = classification.label;
    top.append(heading, badge);
    if (leadFit) {
      const leadBadge = document.createElement('span');
      leadBadge.className = 'service-summary__kind';
      leadBadge.textContent = leadFit.label;
      top.append(leadBadge);
    }
    item.append(top);

    const body = document.createElement('div');
    body.className = 'service-summary__description';
    body.textContent = summary || 'Недостаточно данных: найдено только название компании.';
    item.append(body);

    if (url) {
      const link = document.createElement('a');
      link.href = url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = url;
      link.className = 'service-summary__url';
      item.append(link);
    }

    const meta = document.createElement('div');
    meta.className = 'service-summary__meta';
    const humanPriority = priorityLabel(score, qualification);
    meta.textContent = `${region ? `Регион: ${region} · ` : ''}${humanPriority}${score == null ? '' : ` · балл ${score}/100`}${nameOnly ? ' · Недостаточно данных' : ''}`;
    item.append(meta);

    if (leadFit?.reason) {
      const leadReason = document.createElement('div');
      leadReason.className = 'service-summary__meta';
      leadReason.textContent = `Основание коммерческого статуса: ${leadFit.reason}`;
      item.append(leadReason);
    }

    if (classification.kind === 'company') {
      const action = document.createElement('button');
      action.type = 'button';
      action.className = 'btn-ghost hunter-candidate-action';
      action.textContent = 'Исследовать компанию';
      action.addEventListener('click', () => handoffCandidate(candidate, fallbackRegion));
      item.append(action);
    }
    container.append(item);
  }

  function candidateGroupCounts(candidates) {
    const counts = {company: 0, supporting: 0, observation: 0};
    candidates.forEach(candidate => {
      counts[classifyCandidate(candidate).kind] += 1;
    });
    return counts;
  }

  function renderHunterCandidates(container, candidates, fallbackRegion) {
    const groups = {company: [], supporting: [], observation: []};
    candidates.forEach(candidate => {
      const classification = classifyCandidate(candidate);
      groups[classification.kind].push(candidate);
    });

    if (groups.company.length) {
      const list = appendGroup(container, 'Компании-кандидаты', 'Наиболее пригодные для следующего шага: сайт был глубоко обработан.', 'company');
      groups.company.forEach(candidate => appendCandidate(list, candidate, fallbackRegion));
    }
    if (groups.supporting.length) {
      const details = document.createElement('details');
      details.className = 'hunter-supporting-details';
      details.open = true;
      const summary = document.createElement('summary');
      summary.textContent = `Источники для дополнительной проверки (${groups.supporting.length})`;
      const list = document.createElement('div');
      list.className = 'hunter-group__list';
      groups.supporting.forEach(candidate => appendCandidate(list, candidate, fallbackRegion));
      details.append(summary, list);
      container.append(details);
    }
    if (groups.observation.length) {
      const details = document.createElement('details');
      details.className = 'hunter-supporting-details';
      details.open = true;
      const summary = document.createElement('summary');
      summary.textContent = `Наблюдение (${groups.observation.length})`;
      const list = document.createElement('div');
      list.className = 'hunter-group__list';
      groups.observation.forEach(candidate => appendCandidate(list, candidate, fallbackRegion));
      details.append(summary, list);
      container.append(details);
    }
  }

  const COMPANY_FACT_LABELS = {
    legal_name: 'Юридическое лицо',
    phones: 'Телефон',
    emails: 'Email',
    website: 'Сайт',
    executives: 'Руководитель / ЛПР-факт',
  };

  function renderCompanySalesHandoff(container, data) {
    const analysis = data.site_analysis || null;
    if (!analysis) return;

    const facts = Array.isArray(analysis.company_facts) ? analysis.company_facts : [];
    facts
      .filter(fact => COMPANY_FACT_LABELS[fact.field] && String(fact.value || '').trim())
      .slice(0, 12)
      .forEach(fact => {
        const refs = Array.isArray(fact.source_ids) && fact.source_ids.length
          ? ` · источники: ${fact.source_ids.join(', ')}`
          : '';
        const period = fact.period ? ` · период: ${fact.period}` : '';
        appendItem(
          container,
          `Факт · ${COMPANY_FACT_LABELS[fact.field]}`,
          String(fact.value),
          `Уверенность: ${fact.confidence || 'не указана'}${period}${refs}`,
        );
      });

    const opportunity = analysis.commercial_opportunity || null;
    if (opportunity) {
      const opportunityText = [
        opportunity.problem_hypothesis ? `Проблема: ${opportunity.problem_hypothesis}` : '',
        opportunity.recommended_solution ? `Решение: ${opportunity.recommended_solution}` : '',
        opportunity.expected_value ? `Ожидаемая ценность: ${opportunity.expected_value}` : '',
      ].filter(Boolean).join(' · ');
      appendItem(
        container,
        'Коммерческая возможность — гипотеза',
        opportunityText || 'Гипотеза не сформирована',
        `Не является подтверждённым фактом · ${opportunity.qualification || 'без квалификации'} · балл ${opportunity.score ?? '—'}/100`,
      );
    }

    const action = analysis.action_package || null;
    if (action) {
      if (action.decision_maker_hypothesis) {
        appendItem(container, 'ЛПР — гипотеза', action.decision_maker_hypothesis, 'Требует проверки по первичным источникам');
      }
      if (action.contact_reason) {
        appendItem(container, 'Причина контакта — гипотеза', action.contact_reason, 'Рабочее основание для подготовки контакта, не факт');
      }
      if (Array.isArray(action.demo_scenario) && action.demo_scenario.length) {
        appendItem(container, 'Демо-сценарий — рабочая гипотеза', action.demo_scenario.join(' → '), 'Проверить перед внешним использованием');
      }
      if (action.first_message) {
        appendItem(container, 'Первое сообщение — черновик, не отправлено', action.first_message, 'Автоматическая отправка отключена; решение остаётся за человеком');
      }
      if (action.next_action) {
        appendItem(container, 'Следующий шаг — предлагаемое действие', action.next_action, 'Выполняется только после явного решения человека');
      }
    }

    const readiness = analysis.readiness || null;
    if (readiness && (readiness.client_release_eligible === false || (readiness.release_blockers || []).length)) {
      const blockers = Array.isArray(readiness.release_blockers) ? readiness.release_blockers : [];
      appendItem(
        container,
        'Статус готовности',
        'Предварительный результат: требуется проверка перед внешним использованием.',
        blockers.length ? `Блокеры выпуска: ${blockers.join(', ')}` : 'Client release пока не разрешён',
      );
    }
  }

  function companySalesPackageMarkdown(data) {
    const analysis = data.site_analysis || {};
    const facts = Array.isArray(analysis.company_facts) ? analysis.company_facts : [];
    const opportunity = analysis.commercial_opportunity || null;
    const action = analysis.action_package || null;
    const readiness = analysis.readiness || null;
    const form = document.querySelector('#companyIntelligenceForm');
    const companyName = String(data.company_name || document.querySelector('#companyName')?.value || 'Компания').trim();
    const url = safeHttpUrl(data.official_url || document.querySelector('#companyUrl')?.value || '');
    const region = String(data.region || document.querySelector('#companyRegion')?.value || '').trim();
    const leadFit = String(form?.dataset.hunterLeadFit || '').trim();
    const leadFitReason = String(form?.dataset.hunterLeadFitReason || '').trim();
    const leadFitLabels = {
      commercial_candidate: 'Коммерческий кандидат',
      unknown_candidate: 'Коммерческий статус не подтверждён',
      institutional_candidate: 'Институциональная организация',
    };

    const lines = [
      `# Sales-пакет — ${companyName}`,
      '',
      '## Контекст',
      `- Компания: ${companyName}`,
    ];
    if (url) lines.push(`- Сайт: ${url}`);
    if (region) lines.push(`- Регион: ${region}`);
    if (leadFitLabels[leadFit]) lines.push(`- Hunter lead-fit: ${leadFitLabels[leadFit]}`);
    if (leadFitReason) lines.push(`- Основание lead-fit: ${leadFitReason}`);

    const exportFacts = facts.filter(fact => COMPANY_FACT_LABELS[fact.field] && String(fact.value || '').trim()).slice(0, 12);
    lines.push('', '## Подтверждённые / структурированные факты');
    if (!exportFacts.length) {
      lines.push('- Подтверждённые контактные/идентификационные факты не получены.');
    } else {
      exportFacts.forEach(fact => {
        const refs = Array.isArray(fact.source_ids) && fact.source_ids.length ? `; источники: ${fact.source_ids.join(', ')}` : '';
        const period = fact.period ? `; период: ${fact.period}` : '';
        lines.push(`- ${COMPANY_FACT_LABELS[fact.field]}: ${String(fact.value)} (уверенность: ${fact.confidence || 'не указана'}${period}${refs})`);
      });
    }

    lines.push('', '## Коммерческая возможность — гипотеза, не подтверждённый факт');
    if (opportunity) {
      if (opportunity.problem_hypothesis) lines.push(`- Проблема: ${opportunity.problem_hypothesis}`);
      if (opportunity.recommended_solution) lines.push(`- Решение: ${opportunity.recommended_solution}`);
      if (opportunity.expected_value) lines.push(`- Ожидаемая ценность: ${opportunity.expected_value}`);
      lines.push(`- Квалификация: ${opportunity.qualification || 'не определена'}; балл: ${opportunity.score ?? '—'}/100`);
    } else {
      lines.push('- Гипотеза не сформирована.');
    }

    lines.push('', '## Рабочие гипотезы и действия');
    if (action?.decision_maker_hypothesis) lines.push(`- ЛПР — гипотеза: ${action.decision_maker_hypothesis}`);
    if (action?.contact_reason) lines.push(`- Причина контакта — гипотеза: ${action.contact_reason}`);
    if (Array.isArray(action?.demo_scenario) && action.demo_scenario.length) {
      lines.push(`- Демо-сценарий — рабочая гипотеза: ${action.demo_scenario.join(' → ')}`);
    }
    if (action?.first_message) {
      lines.push('', '### Первое сообщение — черновик — не отправлено', '', action.first_message);
    }
    if (action?.next_action) lines.push('', `- Следующий шаг — предлагаемое действие: ${action.next_action}`);

    lines.push('', '## Готовность и ограничения');
    if (readiness && (readiness.client_release_eligible === false || (readiness.release_blockers || []).length)) {
      lines.push('- Предварительный результат: требуется проверка перед внешним использованием.');
      const blockers = Array.isArray(readiness.release_blockers) ? readiness.release_blockers : [];
      if (blockers.length) lines.push(`- Блокеры выпуска: ${blockers.join(', ')}`);
    } else {
      lines.push('- Статус выпуска отдельно не ограничен данными результата; перед внешним использованием всё равно требуется человеческая проверка.');
    }
    lines.push('', '> Человеческий контроль: этот пакет только скопирован в буфер обмена. Никакое сообщение не отправлено и никакое внешнее действие не выполнено.');
    return lines.join('\n');
  }

  function appendSalesPackageCopyAction(output, data, status) {
    output.querySelector('.company-sales-package-copy')?.remove();
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn-ghost company-sales-package-copy';
    button.textContent = 'Скопировать sales-пакет';
    button.addEventListener('click', async () => {
      try {
        if (!navigator.clipboard?.writeText) {
          throw new Error('буфер обмена недоступен в этом браузере');
        }
        const packageText = companySalesPackageMarkdown(data);
        await navigator.clipboard.writeText(packageText);
        setStatus(status, 'Sales-пакет скопирован. Проверьте текст перед внешним использованием.', 'success');
      } catch (error) {
        setStatus(status, `Не удалось скопировать sales-пакет: ${error.message}`, 'error');
      }
    });
    output.append(button);
  }

  const companyForm = document.querySelector('#companyIntelligenceForm');
  companyForm?.addEventListener('submit', async event => {
    event.preventDefault();
    const status = document.querySelector('#companyIntelligenceStatus');
    const output = document.querySelector('#companyIntelligenceOutput');
    const list = output.querySelector('.service-summary');
    const button = companyForm.querySelector('button');
    button.disabled = true;
    output.hidden = true;
    list.replaceChildren();
    output.querySelector('.company-sales-package-copy')?.remove();
    setStatus(status, 'Исследуем компанию и открытые источники…');
    try {
      const companyName = document.querySelector('#companyName').value.trim();
      const url = document.querySelector('#companyUrl').value.trim();
      const region = document.querySelector('#companyRegion').value.trim();
      const payload = {company_name: companyName, ...await researchSettingsForLaunch("company-intelligence")};
      if (url) payload.url = url;
      if (region) payload.region = region;
      const data = await postJson('/api/company-intelligence', payload);
      appendItem(list, data.company_name || companyName, data.recommended_solution || 'Исследование завершено', `Статус: ${data.status || 'partial'} · коммерческий балл: ${data.commercial_score ?? '—'}`);
      (data.scent_summary || []).slice(0, 6).forEach((item, index) => appendItem(list, `Сигнал ${index + 1}`, item));
      renderCompanySalesHandoff(list, data);
      appendItem(list, 'Источники', `Найдено: ${(data.sources || []).length}`);
      appendSalesPackageCopyAction(output, data, status);
      output.hidden = false;
      setStatus(status, 'Профиль компании подготовлен.', 'success');
    } catch (error) {
      setStatus(status, `Исследование не выполнено: ${error.message}`, 'error');
    } finally {
      button.disabled = false;
    }
  });

  const huntForm = document.querySelector('#hunterForm');
  const hunterStopButton = document.querySelector('#hunterStopButton');
  let activeHunterRun = null;

  function hunterProgressMessage(data) {
    const progress = data.progress || {};
    const elapsed = Math.round(data.elapsed_seconds || 0);
    if (data.phase === 'inspecting_candidates') {
      return `Поиск продолжается ${elapsed} с. Проверено кандидатов: ${progress.processed_candidates || 0}/${progress.total_candidates || 0}. Можно дождаться полного результата или остановить поиск.`;
    }
    return `Поиск продолжается ${elapsed} с. Поисковых направлений завершено: ${progress.completed_queries || 0}/${progress.total_queries || 0}. Можно дождаться полного результата или остановить поиск.`;
  }

  async function hunterRequest(path, options = {}) {
    const response = await fetch(path, {credentials: 'same-origin', ...options});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : data.detail?.reason;
      throw new Error(detail || `HTTP ${response.status}`);
    }
    return data;
  }

  function renderHunterResult(list, data, fallbackRegion) {
    const candidates = data.candidates || [];
    const counts = candidateGroupCounts(candidates);
    const funnel = data.funnel || {};
    const rawResults = funnel.raw_results ?? '—';
    const uniqueCandidates = funnel.unique_candidates ?? data.discovered ?? 0;
    const qualifiedCandidates = funnel.qualified_candidates ?? candidates.length;
    const returnedCandidates = funnel.returned_candidates ?? candidates.length;
    appendItem(
      list,
      'Поисковая воронка',
      `Raw: ${rawResults} → уникальные: ${uniqueCandidates} → прошли фильтр: ${qualifiedCandidates} → возвращено API: ${returnedCandidates}`,
      `Компании-кандидаты: ${counts.company} · источники для проверки: ${counts.supporting} · наблюдение: ${counts.observation} · все возвращённые результаты раскрыты ниже`,
    );
    renderHunterCandidates(list, candidates, data.region || fallbackRegion);
    return candidates.length;
  }

  hunterStopButton?.addEventListener('click', async () => {
    if (!activeHunterRun) return;
    if (!window.confirm('Остановить поиск? Уже завершённые проверки будут сохранены и показаны.')) return;
    hunterStopButton.disabled = true;
    const statusNode = document.querySelector('#hunterStatus');
    setStatus(statusNode, 'Останавливаем поиск и сохраняем завершённые результаты…');
    try {
      await hunterRequest(activeHunterRun.stop_url, {
        method: 'POST',
        headers: {'X-Hunter-Run-Token': activeHunterRun.run_token},
      });
    } catch (error) {
      hunterStopButton.disabled = false;
      setStatus(statusNode, `Не удалось остановить поиск: ${error.message}`, 'error');
    }
  });

  huntForm?.addEventListener('submit', async event => {
    event.preventDefault();
    const status = document.querySelector('#hunterStatus');
    const output = document.querySelector('#hunterOutput');
    const list = output.querySelector('.service-summary');
    const button = huntForm.querySelector('button');
    button.disabled = true;
    hunterStopButton.hidden = true;
    hunterStopButton.disabled = false;
    activeHunterRun = null;
    output.hidden = true;
    list.replaceChildren();
    setStatus(status, 'Ищем и ранжируем компании…');
    try {
      const region = document.querySelector('#hunterRegion').value.trim();
      const industry = document.querySelector('#hunterIndustry').value.trim();
      const payload = {
        region,
        industries: industry ? [industry] : [],
      };
      const regime = document.querySelector('#hunterSearchRegime')?.value || 'auto';
      activeHunterRun = await hunterRequest(`/api/hunt/start?search_regime=${encodeURIComponent(regime)}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      hunterStopButton.hidden = false;
      let snapshot;
      let transientFailures = 0;
      while (activeHunterRun) {
        await new Promise(resolve => window.setTimeout(resolve, 1200));
        try {
          snapshot = await hunterRequest(activeHunterRun.status_url, {
            headers: {'X-Hunter-Run-Token': activeHunterRun.run_token},
          });
          transientFailures = 0;
        } catch (error) {
          transientFailures += 1;
          if (transientFailures < 5) {
            setStatus(status, 'Поиск продолжается. Временно не удалось обновить прогресс; повторяем подключение…');
            continue;
          }
          throw error;
        }
        if (snapshot.state === 'completed' || snapshot.state === 'stopped') break;
        if (snapshot.state === 'failed') throw new Error(snapshot.error || 'hunter_search_failed');
        setStatus(status, hunterProgressMessage(snapshot));
      }
      const data = snapshot?.result || {};
      const candidateCount = renderHunterResult(list, data, region);
      output.hidden = false;
      if (snapshot?.state === 'stopped') {
        setStatus(status, `Поиск остановлен по вашему запросу. Показано завершённых результатов: ${candidateCount}.`, 'success');
      } else {
        setStatus(status, `Полный поиск завершён: отображено ${candidateCount} результатов.`, 'success');
      }
    } catch (error) {
      setStatus(status, `Поиск не выполнен: ${error.message}`, 'error');
    } finally {
      activeHunterRun = null;
      hunterStopButton.hidden = true;
      hunterStopButton.disabled = false;
      button.disabled = false;
    }
  });

  clearSelection();
})();
