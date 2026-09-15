"""Exercise the real settings script with a minimal DOM/network event harness."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_settings_save_launch_and_account_change_events():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for JS event tests")
    script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(value = '', type = '') { this.value=value; this.type=type; this.events={}; this.disabled=false; this.textContent=''; }
  addEventListener(name, fn) { this.events[name]=fn; }
  async emit(name) { return this.events[name]?.({target:this, preventDefault(){}}); }
}
const fields = {
  mission_timeout_seconds: new Element('', 'number'),
  request_timeout_seconds: new Element('', 'number'),
  cost_limit_amount: new Element('', 'text'),
  unknown_price_action: new Element('', 'select-one'),
};
const submit = new Element();
const form = new Element();
form.elements = [...Object.values(fields), submit];
form.elements.namedItem = key => fields[key];
form.querySelector = () => submit;
form.reset = () => Object.values(fields).forEach(x => { x.value=''; });
const service = new Element('site-audit');
const panel = new Element();
const message = new Element();
const reload = new Element();
const dom = {'#researchSettingsForm':form, '#researchSettingsService':service,
 '#researchSettingsPanel':panel, '#researchSettingsMessage':message, '#reloadResearchSettings':reload};
const window = new Element();
let record = {service:'site-audit', revision:1, execution_enabled:true,
 settings:{mission_timeout_seconds:null,request_timeout_seconds:20,cost_limit_amount:null,unknown_price_action:'allow_unpriced'}};
let requests=[];
let pending=null;
let context = {window, document:{querySelector:key=>dom[key]},
 researchHeaders:()=>({'X-CSRF-Token':'test'}),
 fetch:async (url, options={})=>{
  requests.push({url,options});
  if(pending) return await pending;
  if(options.method==='PUT') {
    const payload=JSON.parse(options.body);
    assert.equal(payload.expected_revision,record.revision);
    assert.equal(options.headers['X-CSRF-Token'],'test');
    record={...record, revision:record.revision+1, settings:payload.settings};
  }
  return {ok:true,json:async()=>structuredClone(record)};
 }};
vm.runInNewContext(fs.readFileSync('static/research-settings.js','utf8'),context);
(async()=>{
 panel.open=true; await panel.emit('toggle'); await new Promise(resolve=>setImmediate(resolve));
 assert.equal(fields.request_timeout_seconds.value,20);
 fields.mission_timeout_seconds.value='900';
 fields.request_timeout_seconds.value='77';
 fields.cost_limit_amount.value='';
 await form.emit('input');
 await assert.rejects(window.researchSettingsForLaunch('site-audit'), /Сохраните/);
 await form.emit('submit');
 assert.equal(record.settings.mission_timeout_seconds,900);
 assert.equal(record.settings.request_timeout_seconds,77);
 assert.equal(record.settings.cost_limit_amount,null);
 assert.equal((await window.researchSettingsForLaunch('site-audit')).research_settings_revision,2);
 fields.cost_limit_amount.value='500.25'; await form.emit('input'); await form.emit('submit');
 assert.equal(record.settings.cost_limit_amount,'500.25');
 assert.equal((await window.researchSettingsForLaunch('site-audit')).research_settings_revision,3);
 record.execution_enabled=false; record.execution_block_reason='deadline_pause_resume_unavailable:select_stop';
 await assert.rejects(window.researchSettingsForLaunch('site-audit'), /Возобновление/);
 let release; pending=new Promise(resolve=>release=resolve);
 const loading=reload.emit('click');
 assert.ok(submit.disabled && service.disabled);
 await window.emit('aimeton:auth-changed');
 assert.equal(panel.open,false);
 assert.equal(fields.cost_limit_amount.value,'');
 release({ok:true,json:async()=>structuredClone(record)}); await loading;
 assert.equal(fields.cost_limit_amount.value,'');
 assert.ok(submit.disabled);
 assert.equal(message.textContent,'');
 console.log('settings DOM events passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1],
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "settings DOM events passed" in result.stdout
