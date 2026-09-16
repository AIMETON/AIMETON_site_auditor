import asyncio
import sqlite3

import pytest

import app.analysis_async_api as api
from app.analysis_runtime_projection import AnalysisRuntimeProjectionStore
from app.heuristics import heuristic_analysis
from app.mission_orchestrator import EntryPoint, reset_mission_orchestrator
from app.research_control import ResearchControl, CONTROLS, bind_analysis_control
from app.research_settings import ResearchSettings


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['timeout', 'stop', 'error', 'success', 'restart'])
async def test_acquired_site_partial_survives_interruption_without_claiming_completion(tmp_path, monkeypatch, outcome):
    monkeypatch.setenv('AIMETON_RUNTIME_DB', str(tmp_path / 'runtime.db'))
    monkeypatch.setenv('AIMETON_TRACE_DB', str(tmp_path / 'runtime.db'))
    monkeypatch.setattr(api, 'runtime_instance_id', lambda: 'before')
    reset_mission_orchestrator()
    started = api.create_analysis_runtime('https://example.org', entry_point=EntryPoint.LEGACY_ADAPTER)
    control = ResearchControl(owner_id=1, deep=True, settings=ResearchSettings(
        mission_timeout_seconds=0.03 if outcome == 'timeout' else None, hard_limit_action='stop'))
    bind_analysis_control(control, started.analysis_id)
    async def fetch(url):
        return {'final_url': 'https://example.org/company', 'title': 'Example', 'text': 'Облачные серверы и хостинг'}
    async def enrich(url, title, text):
        stored = AnalysisRuntimeProjectionStore(tmp_path / 'runtime.db').get(started.analysis_id)
        assert stored.partial_result['url'] == url
        assert stored.result is None
        if outcome in ('timeout', 'restart'):
            await asyncio.Event().wait()
        if outcome == 'stop':
            control.stop_requested = True
            control.stop_reason = 'stopped_by_user'
        if outcome == 'error':
            raise RuntimeError('test failure')
        return heuristic_analysis(url, title, text)
    monkeypatch.setattr(api, 'fetch_site', fetch)
    monkeypatch.setattr(api, 'run_enriched_site_analysis', enrich)
    task = asyncio.create_task(api._run_analysis(source_url='https://example.org',
        mission_id=started.mission_id, analysis_id=started.analysis_id))
    if outcome == 'restart':
        for _ in range(100):
            if api.get_analysis_status_payload(started.analysis_id).get('partial_result'):
                break
            await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await task
    api._ANALYSES.pop(started.analysis_id)
    CONTROLS.pop(started.analysis_id)
    monkeypatch.setattr(api, 'runtime_instance_id', lambda: 'after')
    recovered = api.get_analysis_status_payload(started.analysis_id)
    partial = recovered['partial_result']
    assert partial['analysis_id'] == started.analysis_id
    assert partial['mission_id'] == started.mission_id
    assert partial['research_status']['result_quality'] == 'partial'
    assert partial['readiness']['client_release_eligible'] is False
    assert 'audit_not_completed' in partial['readiness']['release_blockers']
    assert partial['readiness']['provider_states']['external_enrichment'] == 'not_completed'
    assert recovered['state'] == ('completed' if outcome == 'success' else 'stalled' if outcome == 'restart' else 'failed')
    assert (recovered['result'] is not None) == (outcome == 'success')
    reset_mission_orchestrator()


def test_existing_projection_database_migrates_without_losing_result(tmp_path):
    path = tmp_path / 'old.db'
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE runtime_analysis_projection (
            analysis_id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, source_url TEXT NOT NULL,
            state TEXT NOT NULL, phase TEXT NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, runtime_instance_id TEXT NOT NULL, result_json TEXT)''')
        db.execute("INSERT INTO runtime_analysis_projection VALUES ('a','m','https://example.org','completed','completed','t','t','r','{}')")
    store = AnalysisRuntimeProjectionStore(path)
    assert store.get('a').result == {}
    assert store.get('a').partial_result is None
    AnalysisRuntimeProjectionStore(path)  # idempotent migration


def test_partial_workspace_event_never_renders_completed_heading():
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which('node')
    if not node:
        pytest.skip('Node needed for UI event check')
    script = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const root = {innerHTML:'', hidden:true}; const events = {};
const context = {URL, document:{querySelector:()=>root}, window:{addEventListener:(name, fn)=>events[name]=fn}};
vm.runInNewContext(fs.readFileSync('static/business-audit-workspace.js', 'utf8'), context);
events['aimeton:analysis-partial']({detail:{state:'failed', result:{research_status:{result_quality:'partial'}}}});
assert.ok(root.innerHTML.includes('сохранён частичный отчёт'));
assert.ok(!root.innerHTML.includes('Исследование завершено'));
events['aimeton:analysis-complete']({detail:{state:'completed', result:{research_status:{}}}});
assert.ok(root.innerHTML.includes('Исследование завершено'));
assert.ok(!root.innerHTML.includes('сохранён частичный отчёт'));
'''
    result = subprocess.run([node, '-e', script], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
