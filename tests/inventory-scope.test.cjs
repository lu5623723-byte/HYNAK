const test=require('node:test');
const assert=require('node:assert/strict');
const {select}=require('../app/static/inventory-scope.js');
const data={user:{role:'admin'},dealers:[{id:1,name:'Dealer A'},{id:2,name:'Dealer B'}],warehouses:[
{id:11,kind:'PRIMARY'},{id:12,kind:'PRIMARY'},{id:21,kind:'SECONDARY'},
{id:31,kind:'CONSIGNMENT',dealer_id:1},{id:32,kind:'CONSIGNMENT',dealer_id:1},{id:33,kind:'CONSIGNMENT',dealer_id:2}],
inventory:[{warehouse_id:11,quantity:100},{warehouse_id:12,quantity:200},{warehouse_id:21,quantity:300},{warehouse_id:31,quantity:4},{warehouse_id:32,quantity:5},{warehouse_id:33,quantity:6}],
orders:[{id:101,dealer_id:1},{id:102,dealer_id:2}],shipments:[
{id:1,order_id:101,warehouse_id:11,consignment_location_id:31},
{id:2,order_id:101,warehouse_id:12,consignment_location_id:32},
{id:3,order_id:102,warehouse_id:21,consignment_location_id:33},
{id:4,order_id:102,warehouse_id:11,consignment_location_id:null},
{id:5,order_id:102,warehouse_id:21,consignment_location_id:31}],
reports:[{id:1,warehouse_id:31,dealer_id:1},{id:2,warehouse_id:32,dealer_id:1},{id:3,warehouse_id:33,dealer_id:2},{id:4,warehouse_id:31,dealer_id:2}],
ledger:[{id:1,warehouse_id:11},{id:2,warehouse_id:12},{id:3,warehouse_id:21},{id:4,warehouse_id:31},{id:5,warehouse_id:32},{id:6,warehouse_id:33}]};
const ids=rows=>rows.map(r=>r.id);
function noRecords(scope){assert.equal(scope.ready,false);for(const key of ['inventory','shipments','reports','ledger'])assert.deepEqual(scope[key],[])}
test('inventory requires both a level and a matching warehouse',()=>{
noRecords(select(data,{mode:'inventory'}));noRecords(select(data,{mode:'inventory',kind:'PRIMARY'}));
noRecords(select(data,{mode:'inventory',warehouse_id:11}));noRecords(select(data,{mode:'inventory',kind:'PRIMARY',warehouse_id:21}));
});
test('primary warehouse stock, dispatch and ledger share one scope',()=>{
const s=select(data,{mode:'inventory',kind:'PRIMARY',warehouse_id:'11'});
assert.equal(s.ready,true);assert.deepEqual(ids(s.warehouses),[11,12]);assert.deepEqual(s.inventory,[data.inventory[0]]);
assert.deepEqual(ids(s.shipments),[1,4]);assert.deepEqual(s.reports,[]);assert.deepEqual(ids(s.ledger),[1]);
});
test('secondary selection cannot retain the previous primary warehouse',()=>{
noRecords(select(data,{mode:'inventory',kind:'SECONDARY',warehouse_id:11}));
const s=select(data,{mode:'inventory',kind:'SECONDARY',warehouse_id:21});
assert.deepEqual(ids(s.warehouses),[21]);assert.deepEqual(s.inventory,[data.inventory[2]]);assert.deepEqual(ids(s.ledger),[3]);
});
test('consignment requires a dealer and one of that dealers locations',()=>{
noRecords(select(data,{mode:'consignment'}));noRecords(select(data,{mode:'consignment',warehouse_id:31}));
noRecords(select(data,{mode:'consignment',dealer_id:1}));noRecords(select(data,{mode:'consignment',dealer_id:2,warehouse_id:31}));
});
test('consignment scopes stock, reports, deliveries and ledger without mixing dealers',()=>{
const s=select(data,{mode:'consignment',dealer_id:'1',warehouse_id:'31'});
assert.equal(s.ready,true);assert.equal(s.dealer.id,1);assert.deepEqual(ids(s.warehouses),[31,32]);
assert.deepEqual(s.inventory,[data.inventory[3]]);assert.deepEqual(ids(s.shipments),[1]);assert.deepEqual(ids(s.reports),[1]);assert.deepEqual(ids(s.ledger),[4]);
});
test('another location of the same dealer has independent records',()=>{
const s=select(data,{mode:'consignment',dealer_id:1,warehouse_id:32});
assert.deepEqual(s.inventory,[data.inventory[4]]);assert.deepEqual(ids(s.shipments),[2]);assert.deepEqual(ids(s.reports),[2]);assert.deepEqual(ids(s.ledger),[5]);
});
test('dealer account has only its own consignment choices and no warehouse stock entry',()=>{
const d={...data,user:{role:'dealer',dealer_id:1}};
const s=select(d,{mode:'consignment',dealer_id:1,warehouse_id:31});assert.deepEqual(ids(s.dealers),[1]);
noRecords(select(d,{mode:'consignment',dealer_id:2,warehouse_id:33}));noRecords(select(d,{mode:'inventory',kind:'PRIMARY',warehouse_id:11}));
});
test('stale, deleted or disallowed IDs never fall back to showing every record',()=>{
noRecords(select(data,{mode:'inventory',kind:'CONSIGNMENT',warehouse_id:31}));
noRecords(select(data,{mode:'consignment',dealer_id:999,warehouse_id:31}));
noRecords(select(data,{mode:'inventory',kind:'PRIMARY',warehouse_id:999}));
noRecords(select({...data,warehouses:data.warehouses.filter(w=>w.id!==31)},{mode:'consignment',dealer_id:1,warehouse_id:31}));
});
test('scoping does not change balances or mutate the source snapshot',()=>{
const before=JSON.stringify(data);for(const warehouse_id of [31,32,33])select(data,{mode:'consignment',dealer_id:warehouse_id===33?2:1,warehouse_id});
assert.equal(JSON.stringify(data),before);
});
