/* Shared read-only scope selection. Data remains permission-scoped by /api/data. */
(function(root){
'use strict';
function select(data,selection){
const list=key=>Array.isArray(data[key])?data[key]:[],role=data.user?.role;
const dealers=list('dealers').filter(d=>role!=='dealer'||d.id===data.user.dealer_id);
const dealerIds=new Set(dealers.map(d=>d.id));
const warehouses=list('warehouses').filter(w=>w.kind!=='CONSIGNMENT'||dealerIds.has(w.dealer_id));
const consignment=selection.mode==='consignment';
const dealer=consignment?dealers.find(d=>d.id===Number(selection.dealer_id)):null;
const options=consignment?warehouses.filter(w=>w.kind==='CONSIGNMENT'&&dealer&&w.dealer_id===dealer.id):
(role==='dealer'?[]:warehouses.filter(w=>['PRIMARY','SECONDARY'].includes(selection.kind)&&w.kind===selection.kind));
const warehouse=options.find(w=>w.id===Number(selection.warehouse_id))||null;
const result={ready:Boolean(warehouse),dealer,dealers,warehouses:options,warehouse,inventory:[],shipments:[],reports:[],ledger:[]};
if(!warehouse||!['inventory','consignment'].includes(selection.mode)){result.ready=false;return result}
const orders=new Map(list('orders').map(o=>[o.id,o]));
result.inventory=list('inventory').filter(r=>r.warehouse_id===warehouse.id);
result.shipments=list('shipments').filter(s=>consignment?
(s.consignment_location_id===warehouse.id&&orders.get(s.order_id)?.dealer_id===dealer.id):s.warehouse_id===warehouse.id);
result.reports=consignment?list('reports').filter(r=>r.warehouse_id===warehouse.id&&r.dealer_id===dealer.id):[];
result.ledger=list('ledger').filter(r=>r.warehouse_id===warehouse.id);
return result;
}
root.HynakInventoryScope={select};
if(typeof module!=='undefined'&&module.exports)module.exports={select};
})(typeof window!=='undefined'?window:globalThis);
