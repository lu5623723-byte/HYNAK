/* Read-only snapshot refresh with stale-response and form-edit protection. */
(function(root){
'use strict';
function create({context,fetchSnapshot,applySnapshot,onVersionChange,onSessionChange,onError}){
let issued=0,pending=0;
return {
invalidate(){issued++},
async refresh({force=false}={}){
const before=context();
if(!before.userId)return {skipped:'signed-out'};
if(before.editing)return {skipped:'editing'};
if(before.loading)return {skipped:'loading'};
if(!force&&(before.hidden||pending))return {skipped:'busy-or-hidden'};
const ticket=++issued;pending++;
try{
const fresh=await fetchSnapshot(),after=context();
if(ticket!==issued||after.userId!==before.userId||after.view!==before.view)return {skipped:'stale'};
if(after.editing||after.loading||(!force&&after.hidden))return {skipped:'deferred'};
if(fresh.user.id!==after.userId){onSessionChange?.();return {sessionChanged:true}}
if(after.version&&fresh.app_version&&after.version!==fresh.app_version){onVersionChange?.(fresh);return {versionChanged:true}}
await applySnapshot(fresh);return {updated:true};
}catch(error){if(ticket===issued)onError?.(error);return {error}}
finally{pending--}
}
};
}
root.HynakSessionSync={create};
if(typeof module!=='undefined'&&module.exports)module.exports={create};
})(typeof window!=='undefined'?window:globalThis);
