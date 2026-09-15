(() => {
  const form = document.querySelector('#researchSettingsForm');
  const service = document.querySelector('#researchSettingsService');
  const message = document.querySelector('#researchSettingsMessage');
  let record = null;
  let generation = 0;
  let dirty = false;
  function busy(value) {
    for (const input of form.elements) input.disabled = value;
    service.disabled = value;
  }

  function explain(reason) {
    if (reason?.startsWith('budget_enforcement_unavailable')) return 'Запуск заблокирован: сквозной денежный и токеновый лимит пока недоступен. Сохранённые пороги не будут молча проигнорированы.';
    if (reason?.startsWith('price_unknown')) return 'Запуск заблокирован: точная стоимость неизвестна. Для продолжения без денежного лимита требуется ваше явное разрешение.';
    if (reason?.startsWith('deadline_pause_resume_unavailable')) return 'Возобновление после паузы ещё недоступно. Для общего тайм-аута выберите остановку.';
    if (reason === 'settings_revision_conflict') return 'Настройки изменены в другом окне. Загрузите их заново.';
    return reason || 'Не удалось выполнить запрос.';
  }
  async function read(selected) {
    const response = await fetch(`/api/user/research-settings/${selected}`, {credentials: 'same-origin'});
    if (!response.ok) throw new Error('Войдите в аккаунт, чтобы загрузить настройки.');
    return response.json();
  }
  async function load() {
    const token = ++generation;
    record = null;
    busy(true);
    try {
      const next = await read(service.value);
      if (token !== generation) return;
      record = next;
      for (const [key, value] of Object.entries(record.settings)) {
        const input = form.elements.namedItem(key);
        if (input) input.value = value ?? '';
      }
      dirty = false;
      message.textContent = record.revision ? `Загружены настройки №${record.revision}. ${record.execution_enabled ? 'Применяются при запуске.' : explain(record.execution_block_reason)}` : 'Задайте и сохраните настройки для этой услуги.';
      busy(false);
    } catch (error) {
      if (token === generation) {
        busy(false);
        form.querySelector('button[type="submit"]').disabled = true;
        message.textContent = error.message;
      }
    }
  }
  service.addEventListener('change', () => {
    if (dirty && record) {
      service.value = record.service;
      message.textContent = 'Сохраните изменения или загрузите настройки заново перед сменой услуги.';
      return;
    }
    load();
  });
  window.addEventListener('aimeton:auth-changed', () => {
    generation++;
    record = null;
    dirty = false;
    form.reset();
    busy(false);
    document.querySelector('#researchSettingsPanel').open = false;
    message.textContent = '';
    form.querySelector('button[type="submit"]').disabled = true;
  });
  document.querySelector('#reloadResearchSettings').addEventListener('click', load);
  form.addEventListener('input', () => { dirty = true; });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!record) return;
    const selected = service.value;
    const token = generation;
    const settings = {...record.settings};
    for (const key of Object.keys(settings)) {
      const input = form.elements.namedItem(key);
      if (!input) continue;
      settings[key] = input.value === '' ? null : input.type === 'number' ? Number(input.value) : input.value;
    }
    busy(true);
    try {
      const response = await fetch(`/api/user/research-settings/${selected}`, {
        method: 'PUT', credentials: 'same-origin', headers: researchHeaders(),
        body: JSON.stringify({expected_revision: record.revision, settings}),
      });
      const next = await response.json();
      if (token !== generation) return;
      if (!response.ok) throw new Error(typeof next.detail === 'string' ? explain(next.detail) : 'Проверьте значения: пороги положительные, предупреждение не выше лимита.');
      record = next;
      dirty = false;
      message.textContent = `Настройки №${record.revision} сохранены. ${record.execution_enabled ? 'Будут применены к новым запускам.' : explain(record.execution_block_reason)}`;
    } catch (error) {
      if (token === generation) message.textContent = error.message;
    } finally {
      if (token === generation) busy(false);
    }
  });
  document.querySelector('#researchSettingsPanel').addEventListener('toggle', event => {
    if (event.target.open && !record) load();
  });
  window.researchSettingsForLaunch = async selected => {
    if (dirty && service.value === selected) throw new Error('Сохраните изменённые настройки перед запуском.');
    const saved = await read(selected);
    if (!saved.revision) return {};
    if (!saved.execution_enabled) throw new Error(explain(saved.execution_block_reason));
    return {research_settings_revision: saved.revision};
  };
})();
