(() => {
  const form = document.querySelector('#llm-settings-form');
  if (!form) return;

  const roleSelect = document.querySelector('#llm-role');
  const profileSelect = document.querySelector('#llm-profile');
  const modelId = document.querySelector('#llm-model-id');
  const temperature = document.querySelector('#llm-temperature');
  const maxTokens = document.querySelector('#llm-max-tokens');
  const timeoutSeconds = document.querySelector('#llm-timeout-seconds');
  const outputMode = document.querySelector('#llm-output-mode');
  const reasoningMode = document.querySelector('#llm-reasoning-mode');
  const reasoningEffort = document.querySelector('#llm-reasoning-effort');
  const resolvedState = document.querySelector('#llm-resolved-state');
  const message = document.querySelector('#llm-settings-message');
  const updated = document.querySelector('#llm-settings-updated');
  const testResult = document.querySelector('#llm-test-result');
  const catalog = document.querySelector('#llm-profile-catalog');
  const refresh = document.querySelector('#refresh-llm-settings');
  const testButton = document.querySelector('#test-llm-model');

  let envelope = null;
  let activeRole = roleSelect.value;

  function csrfToken() {
    const prefix = 'aimeton_csrf=';
    const part = document.cookie.split(';').map(value => value.trim()).find(value => value.startsWith(prefix));
    return part ? decodeURIComponent(part.slice(prefix.length)) : '';
  }

  function setMessage(text, kind = '') {
    message.textContent = text;
    message.className = `message ${kind}`.trim();
  }

  function card(title, lines) {
    const node = document.createElement('article');
    node.className = 'mission-card';
    const heading = document.createElement('h3');
    heading.textContent = title;
    node.append(heading);
    lines.forEach(line => {
      const p = document.createElement('p');
      p.textContent = line;
      node.append(p);
    });
    return node;
  }

  function currentRole() {
    return roleSelect.value;
  }

  function optionalNumber(element) {
    return element.value.trim() === '' ? null : Number(element.value);
  }

  function readRoleForm() {
    return {
      profile_name: profileSelect.value,
      model_id: modelId.value.trim() || null,
      temperature: optionalNumber(temperature),
      max_tokens: optionalNumber(maxTokens),
      timeout_seconds: optionalNumber(timeoutSeconds),
      output_mode: outputMode.value,
      reasoning_mode: reasoningMode.value,
      reasoning_effort: reasoningMode.value !== 'off' && reasoningEffort.value ? reasoningEffort.value : null,
    };
  }

  function stashRole(role = activeRole) {
    if (!envelope?.record?.settings) return;
    envelope.record.settings[role] = readRoleForm();
  }

  function updateReasoningControls() {
    reasoningEffort.disabled = reasoningMode.value === 'off';
    if (reasoningEffort.disabled) reasoningEffort.value = '';
  }

  function fillRole(role) {
    const settings = envelope?.record?.settings?.[role];
    if (!settings) return;
    profileSelect.value = settings.profile_name;
    modelId.value = settings.model_id || '';
    temperature.value = settings.temperature ?? '';
    maxTokens.value = settings.max_tokens ?? '';
    timeoutSeconds.value = settings.timeout_seconds ?? '';
    outputMode.value = settings.output_mode;
    reasoningMode.value = settings.reasoning_mode;
    reasoningEffort.value = settings.reasoning_effort || '';
    updateReasoningControls();
    const resolved = envelope?.resolved?.[role];
    resolvedState.textContent = resolved
      ? `${resolved.configured ? 'Готов' : 'Не настроен'} · ${resolved.provider || '—'} · ${resolved.model || '—'}`
      : 'Состояние не определено';
    testResult.replaceChildren();
  }

  function fillProfiles(profiles) {
    profileSelect.replaceChildren(...profiles.map(profile => {
      const option = document.createElement('option');
      option.value = profile.profile_name;
      option.textContent = `${profile.profile_name} · ${profile.model || 'model from env'} · ${profile.configured ? 'configured' : 'not configured'}`;
      return option;
    }));
    catalog.replaceChildren(...profiles.map(profile => card(profile.profile_name, [
      `Provider: ${profile.provider}`,
      `Model: ${profile.model || 'задаётся environment/model override'}`,
      `Tier: ${profile.tier}`,
      `Credential/config: ${profile.configured ? 'готов' : 'неполный'}`,
    ])));
  }

  async function load() {
    setMessage('Загрузка LLM-настроек…');
    try {
      const response = await fetch('/api/admin/llm-settings', {credentials: 'same-origin'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      envelope = await response.json();
      fillProfiles(envelope.profiles || []);
      activeRole = currentRole();
      fillRole(activeRole);
      const record = envelope.record || {};
      updated.textContent = record.updated_at
        ? `Последнее изменение: ${record.updated_at} · admin user ${record.updated_by ?? '—'} · ${record.reason || 'без комментария'}`
        : 'Используются системные LLM defaults.';
      setMessage('LLM-настройки загружены.', 'success');
    } catch (error) {
      setMessage(`Не удалось загрузить LLM-настройки: ${error.message}`, 'error');
    }
  }

  roleSelect.addEventListener('change', event => {
    const nextRole = event.target.value;
    if (envelope?.record?.settings) stashRole(activeRole);
    activeRole = nextRole;
    if (envelope?.record?.settings) fillRole(activeRole);
  });

  reasoningMode.addEventListener('change', updateReasoningControls);

  testButton.addEventListener('click', async () => {
    if (!envelope) return;
    stashRole(activeRole);
    testButton.disabled = true;
    testResult.replaceChildren(card('Проверка модели', ['Выполняется один короткий live provider-вызов…']));
    try {
      const response = await fetch('/api/admin/llm-settings/test', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken()},
        body: JSON.stringify({role: currentRole(), settings: readRoleForm()}),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const reason = typeof data.detail === 'string' ? data.detail : data.detail?.reason;
        throw new Error(reason || `HTTP ${response.status}`);
      }
      testResult.replaceChildren(card(data.ok ? 'Модель отвечает' : 'Проверка не пройдена', [
        `Role: ${data.role}`,
        `Profile: ${data.profile_name}`,
        `Resolved model: ${data.resolved_model || '—'}`,
        `Latency: ${data.latency_ms ?? '—'} ms`,
        `Finish: ${data.finish_reason || '—'}`,
        `Structured output: ${data.structured_output_valid ? 'valid' : 'not validated'}`,
        `Tokens: prompt=${data.prompt_tokens ?? '—'}, completion=${data.completion_tokens ?? '—'}, total=${data.total_tokens ?? '—'}`,
        `Error: ${data.error_code || 'none'}`,
      ]));
    } catch (error) {
      testResult.replaceChildren(card('Ошибка проверки', [error.message]));
    } finally {
      testButton.disabled = false;
    }
  });

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!envelope?.record?.settings) return;
    stashRole(activeRole);
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    setMessage('Сохраняем LLM-настройки…');
    try {
      const response = await fetch('/api/admin/llm-settings', {
        method: 'PUT',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken()},
        body: JSON.stringify({
          settings: envelope.record.settings,
          reason: document.querySelector('#llm-settings-reason').value.trim(),
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const reason = typeof data.detail === 'string' ? data.detail : data.detail?.reason;
        throw new Error(reason || `HTTP ${response.status}`);
      }
      envelope = data;
      fillProfiles(envelope.profiles || []);
      fillRole(currentRole());
      updated.textContent = `Последнее изменение: ${envelope.record.updated_at} · admin user ${envelope.record.updated_by ?? '—'} · ${envelope.record.reason || 'без комментария'}`;
      setMessage('LLM-настройки сохранены и будут применяться к новым вызовам.', 'success');
    } catch (error) {
      setMessage(`LLM-настройки не сохранены: ${error.message}`, 'error');
    } finally {
      submit.disabled = false;
    }
  });

  refresh?.addEventListener('click', load);
  load();
})();