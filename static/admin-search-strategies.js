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
  const qualityMode = document.querySelector('#search-quality-mode');
  const qualityModeHelp = document.querySelector('#search-quality-mode-help');
  const applyQualityMode = document.querySelector('#apply-search-quality-mode');

  const QUALITY_MODES = {
    economy: {
      strategy: 'fallback_first_nonempty',
      target_results: 25,
      max_providers_per_query: 3,
      description: 'Минимум вызовов: поиск останавливается на первом непустом источнике. Быстро, но benchmark показал заметную потерю полноты.',
    },
    balanced: {
      strategy: 'adaptive_cost_quality',
      target_results: 40,
      max_providers_per_query: 3,
      description: 'Адаптивный режим: учитывает успешность, yield, latency и стоимость и каскадирует источники до целевого результата.',
    },
    quality: {
      strategy: 'consensus_union',
      target_results: 50,
      max_providers_per_query: 3,
      description: 'Режим качества: объединяет несколько независимых движков и повышает результаты, подтверждённые несколькими providers.',
    },
    maximum: {
      strategy: 'exhaustive_coverage',
      target_results: 75,
      max_providers_per_query: 3,
      description: 'Максимальный охват: использует все разрешённые providers в пределах budget, quota и circuit guards.',
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

  function modeForProfile(profile) {
    const match = Object.entries(QUALITY_MODES).find(([, preset]) =>
      preset.strategy === profile.strategy &&
      preset.target_results === profile.target_results &&
      preset.max_providers_per_query === profile.max_providers_per_query
    );
    return match ? match[0] : 'custom';
  }

  function refreshQualityModeFromActiveProfile() {
    if (!state || !qualityMode) return;
    const profile = state.tariffs[activeTariff.value];
    if (!profile) return;
    qualityMode.value = modeForProfile(profile);
    const preset = QUALITY_MODES[qualityMode.value];
    qualityModeHelp.textContent = preset
      ? preset.description
      : `Пользовательская стратегия: ${profile.strategy || state.global_settings.default_strategy}. Значения редактируются в профиле ниже.`;
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
    refreshQualityModeFromActiveProfile();
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

  function applyPresetToActiveProfile() {
    const preset = QUALITY_MODES[qualityMode.value];
    if (!preset) {
      refreshQualityModeFromActiveProfile();
      return;
    }
    const card = tariffsNode.querySelector(`[data-tariff="${activeTariff.value}"]`);
    if (!card) return;
    card.querySelector('[data-field="strategy"]').value = preset.strategy;
    card.querySelector('[data-field="target_results"]').value = preset.target_results;
    card.querySelector('[data-field="max_providers_per_query"]').value = preset.max_providers_per_query;
    qualityModeHelp.textContent = `${preset.description} Нажмите «Сохранить настройки поиска», чтобы применить к новым миссиям.`;
    setMessage(`Режим подготовлен для профиля ${activeTariff.value}. Сохраните настройки.`, 'success');
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
      setMessage('Настройки поиска сохранены. Новые Hunter-миссии используют выбранный режим активного тарифного профиля.', 'success');
      document.querySelector('#refresh-hunter-settings')?.click();
    } catch (error) {
      setMessage(`Стратегии не сохранены: ${error.message}`, 'error');
    } finally {
      submit.disabled = false;
    }
  });

  activeTariff?.addEventListener('change', refreshQualityModeFromActiveProfile);
  qualityMode?.addEventListener('change', () => {
    const preset = QUALITY_MODES[qualityMode.value];
    qualityModeHelp.textContent = preset ? preset.description : 'Пользовательский режим: используйте расширенные настройки ниже.';
  });
  applyQualityMode?.addEventListener('click', applyPresetToActiveProfile);
  refresh?.addEventListener('click', load);
  load();
})();
