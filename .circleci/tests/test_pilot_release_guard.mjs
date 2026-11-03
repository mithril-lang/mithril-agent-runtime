import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { verifyPilotOverlay, verifyPilotOwner, pilotBindingPlan } from '../../cloud/profile-pilot/release-guard.mjs';

const previous='4602e89b-2b76-47e7-b66a-cff07a27d368';
const oldSha='d422610e6fe90df99184c0f73071750dc5171d22';
const namespace='aad2d4bec5d5427783ef9c0775b0b23e';
const app='a030e633-f183-44b0-9ccf-14b89e28f0db';
const img='registry.cloudflare.com/62e1fda53188460698f8c66d3aef59c6/gwqual@sha256:9d80b7baae8a27cff8391f1e0163d194016266ac016cc065af7e0039e247f8af';
const paths=['hermes_cli/profile_placement.py','cloud/profile-pilot/runtime.py','cloud/profile-pilot/install.py','cloud/profile-pilot/worker.mjs'];
function state() { return {
  worker:{id:'9014b895a31e4345a36ed1cdfdb08c08',name:'mithril-profile-pilot-20261101'},
  activeVersion:previous,
  predecessor:{id:previous,resources:{
    bindings:[
      {name:'ORIGIN',type:'plain_text',text:'https://mithril-profile-pilot-20261101.cloud-kotoba.workers.dev'},
      {name:'PROFILES',type:'durable_object_namespace',class_name:'ProfileContainer',namespace_id:namespace},
      {name:'SERVICE_KEY',type:'secret_text'},{name:'SOURCE_SHA',type:'plain_text',text:oldSha},
      {name:'STATE',type:'r2_bucket',bucket_name:'mithril-hermes-gateway-trial-state'},
      {name:'TRANSFER_KEY',type:'secret_text'},
    ],
    script_runtime:{compatibility_date:'2026-11-01',usage_model:'standard',
      exports:{ProfileContainer:{type:'durable-object',storage:'sqlite',container:'profile-pilot'}},
      containers:[{class_name:'ProfileContainer',name:'profile-pilot'}]},
  }},
  application:{id:app,name:'mithril-profile-pilot-20261101-profilecontainer',
    account_id:'62e1fda53188460698f8c66d3aef59c6',scheduling_policy:'default',max_instances:10,
    durable_objects:{namespace_id:namespace},configuration:{image:img}},
};}
test('overlay matches the exact committed Worker and installer inputs',()=>{
  const files=Object.fromEntries(paths.map(path=>[path,readFileSync(path,'utf8')]));
  const overlay=readFileSync('cloud/profile-pilot/overlay.mjs','utf8');
  assert.match(verifyPilotOverlay(files,overlay),/^[a-f0-9]{64}$/);
  assert.throws(()=>verifyPilotOverlay({...files,[paths[0]]:files[paths[0]]+'changed'},overlay),/digest differs/);
  assert.throws(()=>verifyPilotOverlay(files,overlay+'untrusted'),/format differs/);
});
test('pilot owner check binds source, image, namespace and exact resources',()=>{
  const value=state(),owner=verifyPilotOwner(value);
  assert.equal(owner.predecessor,previous);
  for (const mutate of [
    x=>x.worker.id='b'.repeat(32),
    x=>x.predecessor.id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    x=>x.application.configuration.image='foreign',
    x=>x.application.durable_objects.namespace_id='b'.repeat(32),
    x=>x.predecessor.resources.bindings.find(b=>b.name==='STATE').bucket_name='foreign',
    x=>x.predecessor.resources.bindings.push({name:'UNREVIEWED',type:'secret_text'}),
    x=>x.predecessor.resources.script_runtime.exports.ProfileContainer.storage='kv',
  ]) {const changed=structuredClone(value);mutate(changed);assert.throws(()=>verifyPilotOwner(changed));}
});
test('the plan pins each inherited binding to the reviewed predecessor',()=>{
  const owner=verifyPilotOwner(state()),next='f'.repeat(40),plan=pilotBindingPlan(owner,next);
  assert.equal(plan.length,6);
  assert.deepEqual(plan.find(x=>x.name==='SOURCE_SHA'),{name:'SOURCE_SHA',type:'plain_text',text:next});
  assert.ok(plan.filter(x=>x.name!=='SOURCE_SHA').every(x=>x.type==='inherit' && x.version_id===previous));
  assert.throws(()=>pilotBindingPlan({...owner,namespaceId:'foreign'},next));
  assert.throws(()=>pilotBindingPlan(owner,'bad'));
  assert.equal(JSON.stringify(plan).includes('fixture-secret'),false);
});
