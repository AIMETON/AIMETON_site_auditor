(() => {
  const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  }[char]));

  function currentAnalysis() {
    try {
      const history = JSON.parse(localStorage.getItem('aimeton_history') || '[]');
      const visibleUrl = document.querySelector('#resultInner .company-url')?.textContent?.trim();
      return history.find((item) => item.url === visibleUrl) || history[0] || null;
    } catch { return null; }
  }

  const refs = (ids) => (ids || []).map((id) => `[${esc(id)}]`).join(', ') || 'нет подтвержденного источника';

  function renderExtendedProfile() {
    const root = document.querySelector('#resultInner');
    if (!root || root.querySelector('[data-aimeton-extended]')) return;
    const data = currentAnalysis();
    if (!data) return;

    const facts = Array.isArray(data.company_facts) ? data.company_facts : [];
    const km = Array.isArray(data.business_machine_4x4) ? data.business_machine_4x4 : [];
    const section = document.createElement('div');
    section.dataset.aimetonExtended = 'true';

    const factsHtml = facts.length ? `
      <section class="panel">
        <h3>Проверяемый профиль компании</h3>
        <p><small>Профиль углубляет понимание компании и повышает точность AI-предложения. Он не заменяет коммерческую возможность.</small></p>
        <div class="grid">
          ${facts.map((fact) => `<article class="card">
            <span class="tag">${esc(fact.confidence || 'Средняя')}</span>
            <h3>${esc(fact.field)}</h3>
            <p><strong>${esc(fact.value)}</strong></p>
            ${fact.period ? `<p>Период: ${esc(fact.period)}</p>` : ''}
            ${fact.note ? `<p>${esc(fact.note)}</p>` : ''}
            <p><small>Источники: ${refs(fact.source_ids)}</small></p>
          </article>`).join('')}
        </div>
      </section>` : '';

    const kmHtml = km.length ? `
      <section class="panel">
        <h3>Профиль бизнес-машины по каноническому КМ</h3>
        <p><small>Четыре детализирующих оператора: коммуникационные системы, люди, технологии и менеджмент. Каждый раскрывается в четыре нормативные вершины. Это аналитическая оптика для усиления AI-продажи, а не замена коммерческого контура.</small></p>
        <div class="grid">
          ${km.map((cell) => `<article class="card">
            <span class="tag">${esc(cell.code)} · ${esc(cell.status)}</span>
            <h3>${esc(cell.detail_operator)}</h3>
            <p><strong>${esc(cell.vertex)}</strong></p>
            <p>${esc(cell.finding)}</p>
            ${cell.sales_relevance ? `<p><strong>Значение для AI-продажи</strong><br>${esc(cell.sales_relevance)}</p>` : ''}
            <p><small>Уверенность: ${esc(cell.confidence)} · Источники: ${refs(cell.source_ids)}</small></p>
          </article>`).join('')}
        </div>
      </section>` : '';

    section.innerHTML = factsHtml + kmHtml;
    const opportunityPanels = root.querySelectorAll('.panel');
    const firstOpportunity = opportunityPanels[0];
    if (firstOpportunity) firstOpportunity.insertAdjacentElement('afterend', section);
    else root.appendChild(section);
  }

  function renderEvidenceBlocks(source) {
    const blocks = Array.isArray(source.evidence_blocks) ? source.evidence_blocks : [];
    if (!blocks.length) return '';
    return `
      <details style="margin-top:8px">
        <summary>Релевантные evidence-блоки: ${blocks.length}</summary>
        <ol style="margin-top:8px">
          ${blocks.map((block) => `<li style="margin-bottom:10px">
            <small><strong>${esc(block.id)}</strong> · ${esc(block.query_kind || 'unknown')} · ${esc(block.entity_relation || 'unknown')} · ${esc(block.relevance || 'unknown')}</small>
            <blockquote style="margin:6px 0">${esc(block.evidence_quote || '')}</blockquote>
            <small>Locator: ${esc(block.evidence_locator || '—')} · ${esc(block.reason || '')}</small>
          </li>`).join('')}
        </ol>
      </details>`;
  }

  function renderSources() {
    renderExtendedProfile();
    const root = document.querySelector('#resultInner');
    if (!root || root.querySelector('[data-aimeton-sources]')) return;
    const data = currentAnalysis();
    const sources = Array.isArray(data?.sources) ? data.sources : [];
    if (!sources.length) return;

    const signalRefs = new Map();
    (data.economic_signals || []).forEach((signal) => {
      (signal.source_ids || []).forEach((id) => {
        if (!signalRefs.has(id)) signalRefs.set(id, []);
        signalRefs.get(id).push(signal.signal);
      });
    });

    const blockCount = sources.reduce(
      (total, source) => total + (Array.isArray(source.evidence_blocks) ? source.evidence_blocks.length : 0),
      0
    );
    const section = document.createElement('section');
    section.className = 'panel';
    section.dataset.aimetonSources = 'true';
    section.innerHTML = `
      <h3>Источники и доказательства</h3>
      <p><small>${sources.length} первичных документов · ${blockCount} релевантных evidence-блоков. Ссылки предназначены для ручного фактчекинга. Поисковый сниппет является сигналом до проверки первоисточника.</small></p>
      <ol>
        ${sources.map((source) => {
          const linked = signalRefs.get(source.id) || [];
          const checked = source.accessed_at ? new Date(source.accessed_at).toLocaleString('ru-RU') : 'не указана';
          return `<li style="margin-bottom:16px;overflow-wrap:anywhere">
            <strong>[${esc(source.id)}] ${esc(source.title)}</strong><br>
            <a href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.url)}</a><br>
            <small>Проверено: ${esc(checked)} · Тип: ${esc(source.source_type || 'не указан')} · Уровень: ${esc(source.evidence_level || 'не указан')}</small>
            <blockquote style="margin:8px 0">${esc(source.evidence_quote || 'Цитата не указана')}</blockquote>
            ${renderEvidenceBlocks(source)}
            ${linked.length ? `<small><strong>Подтверждает сигналы:</strong> ${linked.map(esc).join('; ')}</small>` : ''}
          </li>`;
        }).join('')}
      </ol>`;
    root.appendChild(section);
  }

  const observer = new MutationObserver(renderSources);
  observer.observe(document.querySelector('#result'), { childList: true, subtree: true });
  document.addEventListener('DOMContentLoaded', renderSources);
})();
