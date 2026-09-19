import asyncio

import pytest

from app.routerai_profile_extraction import extract_profile_parallel
from app.research_control import current_research, deep_research_enabled, record_llm_start, record_llm_usage


@pytest.mark.asyncio
@pytest.mark.parametrize("external", [False, True])
async def test_ordinary_large_corpus_uses_checkpointed_chunks_without_losing_late_facts(tmp_path, monkeypatch, external):
    monkeypatch.setenv('AIMETON_RUNTIME_DB', str(tmp_path / 'runtime.db'))
    calls = []
    active, peak = 0, 0
    control = None
    async def request(phase, model_type, **kwargs):
        nonlocal active, peak, control
        assert not deep_research_enabled()  # scheduling does not escalate discovery/consent
        control = current_research()
        record_llm_start()
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.001)
        active -= 1
        record_llm_usage({'usage': {'prompt_tokens': 1, 'completion_tokens': 1}})
        calls.append(kwargs['prompt'])
        if phase == 'profile_identity_core':
            return model_type(company_name='Example', business_summary='Observed')
        if phase == 'profile_management' and 'LATE_FACT' in kwargs['prompt']:
            return model_type(company_facts=[{'field':'executives','value':'Late manager','source_ids':['E39' if external else 'S1']}])
        return model_type()
    text = '' if external else 'x' * (12000 * 17) + 'директор LATE_FACT'
    sources = [{'id':f'E{i}', 'query_kind':'official', 'lifecycle_state':'evidence',
                'url':'https://example.org', 'snippet':'x' * 6500 + ('LATE_FACT' if i == 39 else '')}
               for i in range(40)] if external else []
    result = await extract_profile_parallel(request_json=request,
        url='https://example.org', title='Example', text=text,
        external_sources=sources, accessed_at='2026-09-16T00:00:00Z')
    if external:
        assert len(calls) > 80  # external evidence remains loss-preserving across routed slices
    else:
        assert len(calls) == 39  # 18 broad chunks x2 + one management chunk + two empty narrow DTOs
    assert peak <= 4
    assert result.coverage.complete
    assert result.coverage.official_chars_total == len(text)
    assert result.coverage.extraction_units_processed == len(calls)
    assert result.coverage.sources_processed == len(sources)
    assert any(f.value == 'Late manager' for f in result.company_facts)
    assert control.llm_calls == control.llm_usage_reports == len(calls)
    assert control.completed_chunks == len(calls)
    assert current_research() is None
