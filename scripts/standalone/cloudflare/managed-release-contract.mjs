import {timingSafeEqual} from 'node:crypto';
import {seal, profile as trialProfile} from '../gateway-contract.mjs';
export const managedProfile = Object.freeze({...trialProfile, name:'managed-gateway-linux-amd64',
  coverage:[...trialProfile.coverage,'private Fund tenant admission, paid-lease deadline, settlement retries and cancellation fences','fixed production publishing contract'],
  excluded:['native Desktop and installers','messaging adapter delivery','other architectures','live paid acceptance']});
export const managedOwner = Object.freeze({accountId:'62e1fda53188460698f8c66d3aef59c6',worker:'mithril-hermes-runtime',bucket:'mithril-hermes-runtime-state',maximumInstances:2});
export function requireManagedOwner(owner) {
  if(JSON.stringify(owner.managedPublication)!==JSON.stringify(managedOwner))throw Error('Dedicated managed publication owner required');
}
export function requireManagedSource({sha,main,clean}) {
  if(!/^[a-f0-9]{40}$/.test(sha) || sha!==main || !clean)throw Error('Clean exact integrated current main required');
}
export function admitManaged(envelope,key,expected,now) {
  // Verify the same cryptographic envelope and image stages, without changing the trial contract.
  // A managed receipt is a separate profile; a trial receipt can never cross this gate.
  if(envelope.receipt?.profile!==managedProfile.name || envelope.receipt?.productionEligible!==true)throw Error('Managed source qualification required');
  const signature=seal(envelope.receipt,key).signature;
  if(!/^[a-f0-9]{64}$/.test(envelope.signature || '') || !timingSafeEqual(Buffer.from(signature),Buffer.from(envelope.signature)))throw Error('Invalid managed receipt signature');
  const r=envelope.receipt;
  for(const [name,value] of Object.entries(expected))if(r[name]!==value)throw Error('Managed receipt identity mismatch');
  const age=(now ?? Date.now())-Date.parse(r.finishedAt);
  if(r.status!=='success' || !Number.isFinite(age) || age<0 || age>86400000 || !/^sha256:[a-f0-9]{64}$/.test(r.imageId || ''))throw Error('Fresh managed image qualification required');
  for(const check of ['image-build','offline-regressions','image-runtime'])if(!r.checks?.includes(check))throw Error('Missing qualification stage');
  return r;
}
