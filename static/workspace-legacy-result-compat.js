(() => {
  const HISTORY_LIMIT = 30;

  function commercialScoreAvailable(data) {
    return data?.research_status?.commercial_score_available !== false;
  }

  function compactHistoryEntry(data) {
    const scoreAvailable = commercialScoreAvailable(data);
    const opportunity = data?.commercial_opportunity || {};
    return {
      saved_at: new Date().toISOString(),
      ui_analysis_id: ensureAnalysisId(data),
      analysis_id: data?.analysis_id || null,
      mission_id: data?.mission_id || null,
      url: data?.url || '',
      company_name: data?.company_name || '',
      business_summary: String(data?.business_summary || '').slice(0, 600),
      commercial_score_available: scoreAvailable,
      commercial_score: scoreAvailable && Number.isFinite(Number(opportunity.score))
        ? Number(opportunity.score)
        : null,
      qualification: scoreAvailable ? (opportunity.qualification || '') : 'Оценка не рассчитана',
      result_quality: data?.research_status?.result_quality || null,
      compact_history_version: 1,
    };
  }

  function persistHistory(entries) {
    let compact = entries.slice(0, HISTORY_LIMIT);
    while (compact.length) {
      try {
        localStorage.setItem(HIST_KEY, JSON.stringify(compact));
        return true;
      } catch (error) {
        if (error?.name !== 'QuotaExceededError' && error?.name !== 'NS_ERROR_DOM_QUOTA_REACHED') {
          console.warn('AIMETON history persistence unavailable', error);
          return false;
        }
        compact = compact.slice(0, -1);
      }
    }
    try { localStorage.removeItem(HIST_KEY); } catch {}
    return false;
  }

  saveToHistory = function saveCompactHistory(data) {
    ensureAnalysisId(data);
    const entry = compactHistoryEntry(data);
    let history = getHistory();
    const replacedIds = history
      .filter(item => item.url === entry.url && item.ui_analysis_id !== entry.ui_analysis_id)
      .map(item => item.ui_analysis_id)
      .filter(Boolean);
    history = history.filter(item => item.url !== entry.url);
    history.unshift(entry);
    persistHistory(history);

    if (replacedIds.length) {
      try {
        const sessions = getChatSessions();
        replacedIds.forEach(id => delete sessions[id]);
        localStorage.setItem(CHAT_KEY, JSON.stringify(sessions));
      } catch (error) {
        console.warn('AIMETON chat history cleanup unavailable', error);
      }
    }
    renderHistory();
    return true;
  };

  const originalSetChatSession = setChatSession;
  setChatSession = function safeSetChatSession(session) {
    try {
      return originalSetChatSession(session);
    } catch (error) {
      console.warn('AIMETON chat session persistence unavailable', error);
      return false;
    }
  };

  function historyScoreLabel(item) {
    if (item?.commercial_score_available === false) return 'не рассчитана';
    if (item?.compact_history_version === 1) {
      return item.commercial_score == null ? '?' : `${item.commercial_score}/100`;
    }
    const legacyScore = item?.commercial_opportunity?.score;
    return legacyScore == null ? '?' : `${legacyScore}/100`;
  }

  renderHistory = function renderCompactHistory() {
    const history = getHistory();
    if (!history.length) {
      historyEl.classList.add('hidden');
      return;
    }
    historyEl.classList.remove('hidden');
    historyList.innerHTML = history.map((item, index) => {
      const date = new Date(item.saved_at).toLocaleString('ru', {
        day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit'
      });
      return `
        <div class="history-item">
          <div class="history-item-info">
            <strong>${esc(item.company_name || item.url)}</strong>
            <span class="history-url">${esc(item.url || '')}</span>
            <span class="history-date">${date}</span>
          </div>
          <div class="history-item-actions">
            <span class="tag">${esc(historyScoreLabel(item))}</span>
            <button class="btn-ghost btn-sm" onclick="loadFromHistory(${index})">Открыть</button>
            <button class="btn-ghost btn-sm btn-danger" onclick="deleteHistory(${index})" title="Удалить">✕</button>
          </div>
        </div>`;
    }).join('');
  };

  async function fetchDurableAnalysis(item) {
    if (!item?.analysis_id) return null;
    const response = await fetch(`/api/analyze/${encodeURIComponent(item.analysis_id)}`, {
      credentials: 'same-origin',
    });
    if (!response.ok) return null;
    const status = await response.json();
    return status.result || status.partial_result || null;
  }

  loadFromHistory = async function loadCompactHistory(index) {
    const history = getHistory();
    const item = history[index];
    if (!item) return;

    let restored = null;
    if (item.compact_history_version === 1) {
      try {
        restored = await fetchDurableAnalysis(item);
      } catch (error) {
        console.warn('AIMETON durable history read unavailable', error);
      }
      if (!restored) {
        setStatus('Запись истории найдена, но полный серверный результат сейчас недоступен.');
        return;
      }
    } else if (item.commercial_opportunity) {
      // Read old browser history without migrating the full legacy payload back into storage.
      restored = item;
    }
    if (!restored) return;

    analysis = restored;
    activeAnalysisId = ensureAnalysisId(analysis);
    render();
    renderChatSession();
    setStatus('Загружено из истории: ' + (analysis.company_name || analysis.url));
    resultEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const originalRender = render;
  render = function renderWithCommercialIntegrity() {
    if (!analysis || commercialScoreAvailable(analysis)) return originalRender();

    const original = analysis;
    const display = {
      ...original,
      commercial_opportunity: {
        ...(original.commercial_opportunity || {}),
        score: '—',
        qualification: 'Оценка не рассчитана',
      },
      agents: [],
      action_package: {
        decision_maker_hypothesis: 'Не рассчитано',
        contact_reason: 'Коммерческий reasoning не завершён.',
        demo_scenario: [],
        first_message: 'Не рассчитано',
        next_action: 'Повторить коммерческий reasoning по сохранённому профилю.',
      },
      readiness: {
        ...(original.readiness || {}),
        commercial_priority: 'не рассчитан',
      },
    };
    analysis = display;
    try {
      originalRender();
      const inner = document.querySelector('#resultInner');
      if (inner && !inner.querySelector('[data-commercial-unavailable]')) {
        const notice = document.createElement('div');
        notice.className = 'notice notice-warning';
        notice.dataset.commercialUnavailable = 'true';
        notice.innerHTML = '<strong>Коммерческая оценка не рассчитана.</strong> Факты сохранены, но финальный reasoning не завершён; 0/100 не является оценкой компании.';
        inner.prepend(notice);
      }
      for (const paragraph of document.querySelectorAll('#resultInner .panel p')) {
        if (paragraph.textContent.includes('Коммерческий приоритет:')) {
          paragraph.innerHTML = paragraph.innerHTML.replace(
            /<strong>Коммерческий приоритет:<\/strong>[^·]*·/,
            '<strong>Коммерческий приоритет:</strong> не рассчитан ·'
          );
        }
      }
    } finally {
      analysis = original;
    }
  };

  function patchBusinessWorkspace(result) {
    if (!result || commercialScoreAvailable(result)) return;
    const workspace = document.querySelector('#businessAuditWorkspace');
    if (!workspace) return;

    const score = workspace.querySelector('.baw-decision-score');
    if (score) score.innerHTML = '<span class="baw-chip">Оценка не рассчитана</span>';

    const decision = workspace.querySelector('.baw-decision-card');
    if (decision && !decision.querySelector('[data-commercial-unavailable]')) {
      const warning = document.createElement('p');
      warning.className = 'baw-muted';
      warning.dataset.commercialUnavailable = 'true';
      warning.textContent = 'Факты извлечены, но коммерческий reasoning не завершён. Числовая оценка и рекомендации недоступны.';
      decision.append(warning);
    }

    for (const section of workspace.querySelectorAll('.baw-section')) {
      const heading = section.querySelector('h3');
      if (!heading) continue;
      if (heading.textContent.trim() === 'AI-возможности' || section.classList.contains('baw-next')) {
        section.hidden = true;
      }
    }
  }

  for (const eventName of ['aimeton:analysis-complete', 'aimeton:analysis-partial']) {
    window.addEventListener(eventName, event => {
      if (!event.detail?.result) return;
      document.querySelector('#resultInner')?.setAttribute('hidden', '');
      patchBusinessWorkspace(event.detail.result);
    });
  }

  renderHistory();
})();
