import { createHash } from 'node:crypto';

const hash = value => createHash('sha256').update(value).digest('hex');
const uuid = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const sha = /^[a-f0-9]{40}$/;
const names = ['ORIGIN','PROFILES','SERVICE_KEY','SOURCE_SHA','STATE','TRANSFER_KEY'];
const workerId = '9014b895a31e4345a36ed1cdfdb08c08';
const workerName = 'mithril-profile-pilot-20261101';
const applicationId = 'a030e633-f183-44b0-9ccf-14b89e28f0db';
const namespaceId = 'aad2d4bec5d5427783ef9c0775b0b23e';
const image = 'registry.cloudflare.com/62e1fda53188460698f8c66d3aef59c6/gwqual@sha256:9d80b7baae8a27cff8391f1e0163d194016266ac016cc065af7e0039e247f8af';
const files = ['hermes_cli/profile_placement.py','cloud/profile-pilot/runtime.py',
  'cloud/profile-pilot/install.py','cloud/profile-pilot/worker.mjs'];

/** Verify the built public overlay against all exact source bytes, without
 * reading any Profile, credential or Container data. */
export function verifyPilotOverlay(source, overlayText) {
  if (!source || files.some(path => typeof source[path] !== 'string') || typeof overlayText !== 'string')
    throw Error('Complete pilot overlay source required');
  const match = /^export default (.*);\s*$/s.exec(overlayText);
  if (!match) throw Error('Pilot overlay format differs');
  let overlay;
  try { overlay=JSON.parse(match[1]); } catch { throw Error('Pilot overlay is not JSON'); }
  if (Object.keys(overlay).sort().join() !== 'files,hashes,installer,source' ||
      Object.keys(overlay.hashes ?? {}).sort().join() !== [...files].sort().join() ||
      Object.keys(overlay.files ?? {}).sort().join() !== [...files.slice(0,2)].sort().join())
    throw Error('Pilot overlay inventory differs');
  const hashes=Object.fromEntries(files.map(path=>[path,hash(source[path])]));
  if (files.some(path=>overlay.hashes[path]!==hashes[path]) ||
      files.slice(0,2).some(path=>overlay.files[path]!==Buffer.from(source[path]).toString('base64')) ||
      overlay.installer!==Buffer.from(source[files[2]]).toString('base64'))
    throw Error('Pilot overlay source digest differs');
  const canonical=Object.fromEntries(Object.entries(hashes).sort(([a],[b])=>a.localeCompare(b)));
  if (overlay.source!==hash(JSON.stringify(canonical))) throw Error('Pilot overlay aggregate digest differs');
  return overlay.source;
}

/** Fail closed if the live Worker, namespace, image, or preserved binding
 * inventory differs. No secret values are read or returned. */
export function verifyPilotOwner({worker, activeVersion, predecessor, application}) {
  if (worker?.id!==workerId || worker?.name!==workerName || !uuid.test(activeVersion ?? '') ||
      predecessor?.id!==activeVersion || application?.id!==applicationId ||
      application?.name!==workerName+'-profilecontainer' ||
      application?.account_id!=='62e1fda53188460698f8c66d3aef59c6' ||
      application?.scheduling_policy!=='default' || application?.max_instances!==10 ||
      application?.durable_objects?.namespace_id!==namespaceId || application?.configuration?.image!==image)
    throw Error('Pilot worker, predecessor, namespace or Container image differs');
  const bindings=predecessor?.resources?.bindings;
  if (!Array.isArray(bindings) || bindings.length!==names.length ||
      bindings.some(b=>!b || typeof b.name!=='string') ||
      bindings.map(b=>b.name).sort().join()!==[...names].sort().join())
    throw Error('Pilot binding inventory differs');
  const one=name=>bindings.find(b=>b.name===name);
  if (one('ORIGIN')?.type!=='plain_text' || one('ORIGIN')?.text!=='https://'+workerName+'.cloud-kotoba.workers.dev' ||
      one('SOURCE_SHA')?.type!=='plain_text' || !sha.test(one('SOURCE_SHA')?.text ?? '') ||
      one('PROFILES')?.type!=='durable_object_namespace' || one('PROFILES')?.class_name!=='ProfileContainer' ||
      one('PROFILES')?.namespace_id!==namespaceId || one('STATE')?.type!=='r2_bucket' ||
      one('STATE')?.bucket_name!=='mithril-hermes-gateway-trial-state' ||
      ['SERVICE_KEY','TRANSFER_KEY'].some(name=>one(name)?.type!=='secret_text'))
    throw Error('Pilot resource binding differs');
  const runtime=predecessor.resources.script_runtime;
  if (runtime?.compatibility_date!=='2026-11-01' || runtime?.usage_model!=='standard' ||
      runtime?.exports?.ProfileContainer?.type!=='durable-object' ||
      runtime.exports.ProfileContainer.storage!=='sqlite' ||
      runtime.exports.ProfileContainer.container!=='profile-pilot' ||
      !Array.isArray(runtime.containers) || runtime.containers.length!==1 ||
      runtime.containers[0].class_name!=='ProfileContainer' || runtime.containers[0].name!=='profile-pilot')
    throw Error('Pilot Worker runtime differs');
  return {predecessor:activeVersion,previousSource:one('SOURCE_SHA').text,
    applicationId,namespaceId,image};
}

/** Construct only a source-bound binding plan. Upload and activation require
 * a separate qualified publisher, concurrency control and live read-back. */
export function pilotBindingPlan(owner, sourceSha) {
  if (!owner || !uuid.test(owner.predecessor ?? '') || !sha.test(sourceSha ?? ''))
    throw Error('Pinned predecessor and exact source required');
  return names.map(name=>name==='SOURCE_SHA'
    ? {name,type:'plain_text',text:sourceSha}
    : {name,type:'inherit',version_id:owner.predecessor});
}
