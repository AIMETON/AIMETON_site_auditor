(() => {
  const form = document.querySelector('#search-strategy-form');
  if (!form) return;

  const message = document.querySelector('#search-strategy-message');
  const updated = document.querySelector('#search-strategy-updated');
  const refresh = document.querySelector('#refresh-search-strategies');
  const tariffsNode = document.querySelector('#search-tariff-profiles');
  const catalogNode = document.querySelector('#search-strategy-catalog');
  const activeTariff = document.querySelector('#search-active-tariff');
  const defaultStrategy = document.querySelector('#search-default-strategy');
  const emergencyStrategy = document.querySelector('#search-emergency-strategy');
  const providersSelect = document.querySelector('#search-enabled-providers');
  const canonicalProviderOrder = document.querySelector('#search-canonical-provider-order');
  const searchModePreset = document.querySelector('#search-mode-preset');
  const searchModeDescription = document.querySelector('#search-mode-description');
  const applySearchMode = document.querySelector('#apply-search-mode');

  const SEARCH_MODES = {
    fast: {
      strategy: 'fallback_first_nonempty',
      label: 'Быстрый',
      description: 'Останавливается на первом непустом ответе. Минимальная стоимость и задержка, но ниже recall: слабая выдача первого provider может не дать дойти до остальных.',
    },
    balanced: {
      strategy: 'cascade_until_target',
      label: 'Баланс',
      description: 'Последовательно объединяет результаты до target_results. Рекомендуемый базовый режим для рабочего поиска: выше recall без обязательного полного fan-out.',
    },
    quality: {
      strategy: 'consensus_union',
      label: 'Качество',
      description: 'Собирает выдачу нескольких разрешённых providers и поднимает домены, подтверждённые несколькими источниками. Требует fan-out и может стоить дороже.',
    },
    coverage: {
      strategy: 'exhaustive_coverage',
      label: 'Максимальный охват',
      description: 'Использует все разрешённые providers в пределах max_providers, quota и budget guards. Максимальный recall, максимальная потенциальная стоимость.',
    },
  };

  let state = null;
  let catalog = [];

  function csrfToken() {
    const prefix = 'aimeton_csrf=';
    const part = document.cookie.split(';').map(v => v.trim()).find(v => v.startsWith(prefix));
    return part ? decodeURIComponent(part.slice(prefix.length)) : '';
  }

  function setMessage(text, kind = '') {
    message.textContent = text;
    message.className = `message ${kind}`.trim();
  }

  function implementedStrategies(tariffSafeOnly = false) {
    return catalog.filter(item => item.implemented && (!tariffSafeOnly || item.tariff_safe));
  }

  function strategyOptions(selected, {emptyLabel = '', tariffSafeOnly = false} = {}) {
    const options = [];
    if (emptyLabel) {
      options.push(`<option value="" ${selected ? '' : 'selected'}>${emptyLabel}</option>`);
    }
    implementedStrategies(tariffSafeOnly).forEach(item => {
      const isSelected = item.id === selected ? ' selected' : '';
      options.push(`<option value="${item.id}"${isSelected}>${item.label}</option>`);
    });
    return options.join('');
  }

  function selectedValues(select) {
    return [...select.selectedOptions].map(option => option.value);
  }

  function parseProviderOrder(value) {
    return String(value || '').split(',').map(item => item.trim()).filter(Boolean);
  }

  function tariffCard(profile) {
    const article = document.createElement('article');
    article.className = 'mission-card';
    article.dataset.tariff = profile.id;
    article.innerHTML = `
      <h3>${profile.label} <small>(${profile.id})</small></h3>
      <label>Включён<input data-field="enabled" type="checkbox" ${profile.enabled ? 'checked' : ''}></label>
      <label>Стратегия<select data-field="strategy">${strategyOptions(profile.strategy, {emptyLabel: 'Наследовать глобальную стратегию', tariffSafeOnly: true})}</select></label>
      <label>Порядок providers<input data-field="provider_order" value="${profile.provider_order.join(',')}"></label>
      <label>Платные providers<select data-field="paid_policy">
        <option value="inherit" ${profile.paid_policy === 'inherit' ? 'selected' : ''}>Наследовать</option>
        <option value="deny" ${profile.paid_policy === 'deny' ? 'selected' : ''}>Запретить</option>
        <option value="allow_with_budget" ${profile.paid_policy === 'allow_with_budget' ? 'selected' : ''}>Разрешить с бюджетом</option>
      </select></label>
      <label>Платный fan-out<select data-field="paid_fanout_policy">
        <option value="inherit" ${profile.paid_fanout_policy === 'inherit' ? 'selected' : ''}>Наследовать</option>
        <option value="deny" ${profile.paid_fanout_policy === 'deny' ? 'selected' : ''}>Запретить</option>
        <option value="allow_with_budget" ${profile.paid_fanout_policy === 'allow_with_budget' ? 'selected' : ''}>Разрешить с бюджетом</option>
      </select></label>
      <label>Budget RUB<input data-field="max_cost_rub" type="number" min="0" step="0.01" value="${profile.max_cost_rub}"></label>
      <label>Budget USD<input data-field="max_cost_usd" type="number" min="0" step="0.0001" value="${profile.max_cost_usd}"></label>
      <label>Целевых уникальных результатов на query<input data-field="target_results" type="number" min="1" max="100" value="${profile.target_results}"></label>
      <label>Максимум providers на query<input data-field="max_providers_per_query" type="number" min="1" max="3" value="${profile.max_providers_per_query}"></label>
      <label>Максимум query-вариантов<input data-field="max_queries" type="number" min="1" max="100" value="${profile.max_queries}"></label>
      <label>Результатов с provider на query<input data-field="results_per_query" type="number" min="1" max="30" value="${profile.results_per_query}"></label>
      <label>Candidate pool<input data-field="max_candidates" type="number" min="1" max="500" value="${profile.max_candidates}"></label>
      <label>Output limit<input data-field="output_limit" type="number" min="1" max="100" value="${profile.output_limit}"></label>
      <label>Minimum pre-score<input data-field="minimum_pre_score" type="number" min="0" max="100" value="${profile.minimum_pre_score}"></label>
      <label>Deep audit from<input data-field="deep_audit_score" type="number" min="0" max="100" value="${profile.deep_audit_score}"></label>
      <label>Concurrency<input data-field="concurrency" type="number" min="1" max="12" value="${profile.concurrency}"></label>
    `;
    return article;
  }

  function activeTariffCard() {
    return tariffsNode.querySelector(`[data-tariff="${activeTariff.value}"]`);
  }

  function modeForStrategy(strategy) {
    return Object.entries(SEARCH_MODES).find(([, mode]) => mode.strategy === strategy)?.[0] || 'custom';
  }

  function updateModeDescription() {
    const mode = SEARCH_MODES[searchModePreset?.value];
    if (!searchModeDescription) return;
    searchModeDescription.textContent = mode
      ? `${mode.label}: ${mode.description} Сохранение настроек выполняется кнопкой «Сохранить стратегии и тарифы».`
      : 'Пользовательский режим: стратегия активного профиля задаётся вручную ниже. Cost/budget guards не обходятся.';
  }

  function syncSearchModeFromActiveProfile() {
    if (!searchModePreset || !state) return;
    const card = activeTariffCard();
    const strategy = card?.querySelector('[data-field="strategy"]')?.value || state.global_settings.default_strategy;
    searchModePreset.value = modeForStrategy(strategy);
    updateModeDescription();
  }

  function applySelectedSearchMode() {
    const mode = SEARCH_MODES[searchModePreset?.value];
    if (!mode) {
      setMessage('Пользовательский режим выбран: стратегия активного профиля не изменена.');
      return;
    }
    const card = activeTariffCard();
    const strategySelect = card?.querySelector('[data-field="strategy"]');
    if (!strategySelect) {
      setMessage('Не удалось определить активный тарифный профиль.', 'error');
      return;
    }
    strategySelect.value = mode.strategy;
    if (strategySelect.value !== mode.strategy) {
      setMessage(`Стратегия ${mode.strategy} недоступна в текущем каталоге.`, 'error');
      return;
    }
    document.querySelector('#search-strategy-reason').value = `Режим поиска: ${mode.label}`;
    setMessage(`Режим «${mode.label}» применён к профилю ${activeTariff.value}. Для вступления в силу сохраните настройки.`, 'success');
    updateModeDescription();
  }

  function renderCatalog() {
    catalogNode.replaceChildren();
    catalog.forEach(item => {
      const card = document.createElement('article');
      card.className = 'mission-card';
      const scope = item.tariff_safe ? 'доступно глобально и в тарифах' : 'только global/debug';
      card.innerHTML = `<strong>${item.label}</strong><div>${item.description}</div><div class="message">${item.id} · охват: ${item.coverage} · стоимость: ${item.cost_profile} · ${item.implemented ? 'реализовано' : 'planned'} · ${scope}</div>`;
      catalogNode.append(card);
    });
  }

  function render(record) {
    state = record.settings;
    const global = state.global_settings;
    const profiles = Object.values(state.tariffs);

    activeTariff.innerHTML = profiles.map(p => {
      const selected = p.id === global.active_tariff ? ' selected' : '';
      const disabled = !p.enabled && !selected ? ' disabled' : '';
      return `<option value="${p.id}"${selected}${disabled}>${p.label}${p.enabled ? '' : ' [выключен]'}</option>`;
    }).join('');
    defaultStrategy.innerHTML = strategyOptions(global.default_strategy);
    emergencyStrategy.innerHTML = strategyOptions(global.emergency_strategy_override || '', {emptyLabel: 'Нет аварийного override'});
    document.querySelector('#search-paid-policy').value = global.paid_policy;
    document.querySelector('#search-paid-fanout-policy').value = global.paid_fanout_policy;
    document.querySelector('#search-hard-rub').value = global.hard_max_cost_rub;
    document.querySelector('#search-hard-usd').value = global.hard_max_cost_usd;
    canonicalProviderOrder.value = (global.canonical_provider_order || []).join(',');
    [...providersSelect.options].forEach(option => { option.selected = global.enabled_providers.includes(option.value); });

    tariffsNode.replaceChildren(...profiles.map(tariffCard));
    syncSearchModeFromActiveProfile();
    updated.textContent = record.updated_at
      ? `Последнее изменение: ${record.updated_at} · admin user ${record.updated_by ?? '—'} · ${record.reason || ''}`
      : 'Используются безопасные базовые тарифные профили; цены к ним не привязаны.';
  }

  function numberField(card, name) {
    return Number(card.querySelector(`[data-field="${name}"]`).value);
  }

  function collectTariffs() {
    const tariffs = {};
    tariffsNode.querySelectorAll('[data-tariff]').forEach(card => {
      const old = state.tariffs[card.dataset.tariff];
      const order = parseProviderOrder(card.querySelector('[data-field="provider_order"]').value);
      tariffs[old.id] = {
        id: old.id,
        label: old.label,
        enabled: card.querySelector('[data-field="enabled"]').checked,
        strategy: card.querySelector('[data-field="strategy"]').value || null,
        provider_order: order,
        paid_policy: card.querySelector('[data-field="paid_policy"]').value,
        paid_fanout_policy: card.querySelector('[data-field="paid_fanout_policy"]').value,
        max_cost_rub: numberField(card, 'max_cost_rub'),
        max_cost_usd: numberField(card, 'max_cost_usd'),
        target_results: numberField(card, 'target_results'),
        max_providers_per_query: numberField(card, 'max_providers_per_query'),
        max_queries: numberField(card, 'max_queries'),
        results_per_query: numberField(card, 'results_per_query'),
        max_candidates: numberField(card, 'max_candidates'),
        output_limit: numberField(card, 'output_limit'),
        minimum_pre_score: numberField(card, 'minimum_pre_score'),
        deep_audit_score: numberField(card, 'deep_audit_score'),
        concurrency: numberField(card, 'concurrency'),
      };
    });
    return tariffs;
  }

  async function load() {
    setMessage('Загрузка стратегий…');
    try {
      const response = await fetch('/api/admin/search-strategies', {credentials: 'same-origin'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      catalog = data.catalog || [];
      renderCatalog();
      render(data.record);
      setMessage('Стратегии и тарифные профили загружены.', 'success');
    } catch (error) {
      setMessage(`Не удалось загрузить стратегии: ${error.message}`, 'error');
    }
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    setMessage('Сохраняем стратегию…');
    const payload = {
      settings: {
        global_settings: {
          active_tariff: activeTariff.value,
          default_strategy: defaultStrategy.value,
          enabled_providers: selectedValues(providersSelect),
          canonical_provider_order: parseProviderOrder(canonicalProviderOrder.value),
          paid_policy: document.querySelector('#search-paid-policy').value,
          paid_fanout_policy: document.querySelector('#search-paid-fanout-policy').value,
          hard_max_cost_rub: Number(document.querySelector('#search-hard-rub').value || 0),
          hard_max_cost_usd: Number(document.querySelector('#search-hard-usd').value || 0),
          emergency_strategy_override: emergencyStrategy.value || null,
        },
        tariffs: collectTariffs(),
      },
      reason: document.querySelector('#search-strategy-reason').value.trim(),
    };

    try {
      const response = await fetch('/api/admin/search-strategies', {
        method: 'PUT',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken()},
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const reason = typeof data.detail === 'string' ? data.detail : data.detail?.reason;
        throw new Error(reason || `HTTP ${response.status}`);
      }
      render(data);
      setMessage('Стратегии сохранены. Новые Hunter-миссии используют активный тарифный профиль.', 'success');
      document.querySelector('#refresh-hunter-settings')?.click();
    } catch (error) {
      setMessage(`Стратегии не сохранены: ${error.message}`, 'error');
    } finally {
      submit.disabled = false;
    }
  });

  activeTariff?.addEventListener('change', syncSearchModeFromActiveProfile);
  searchModePreset?.addEventListener('change', updateModeDescription);
  applySearchMode?.addEventListener('click', applySelectedSearchMode);
  tariffsNode.addEventListener('change', event => {
    if (event.target?.matches('[data-field="strategy"]') && event.target.closest('[data-tariff]')?.dataset.tariff === activeTariff.value) {
      syncSearchModeFromActiveProfile();
    }
  });
  refresh?.addEventListener('click', load);
  load();
})();
