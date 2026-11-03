const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const context=vm.createContext({Intl,Date,Set,Map,Math,Number,L:zh=>zh,
    E:value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
    money:value=>new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(value/100)});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/static/ranking-charts.js'),'utf8'),context);
const run=code=>JSON.parse(JSON.stringify(vm.runInContext(code,context)));

// Inclusive calendar periods, gaps at zero and exact money in integer cents.
assert.deepEqual(run("rankingBuckets('2026-10-30','2026-11-02','day').dates"),['2026-10-30','2026-10-31','2026-11-01','2026-11-02']);
assert.deepEqual(run("rankingSeries([{date:'2026-10-30',amount:12345},{date:'2026-11-02',amount:20000}],rankingBuckets('2026-10-30','2026-11-02','day').dates,'day','amount')"),[12345,0,0,20000]);
// Weeks start on Monday, including a partial first and final week.
assert.equal(run("rankingBucket('2026-11-01','week')"),'2026-10-26');
assert.equal(run("rankingBucket('2026-11-02','week')"),'2026-11-02');
assert.deepEqual(run("rankingSeries([{date:'2026-11-01',quantity:5},{date:'2026-11-02',quantity:7}],rankingBuckets('2026-11-01','2026-11-02','week').dates,'week','quantity')"),[5,7]);
assert.deepEqual(run("rankingSeries([{date:'2026-10-31',order_count:2},{date:'2026-11-01',order_count:3}],rankingBuckets('2026-10-15','2026-11-02','month').dates,'month','order_count')"),[2,3]);
// A wide range coarsens the intervals while retaining all supplied data.
assert.equal(run("(()=>{const b=rankingBuckets('0001-01-01','9999-12-31','day');return b.dates.length<=400})()"),true);
assert.equal(run("(()=>{const b=rankingBuckets('0001-01-01','9999-12-31','day');return rankingSeries([{date:'0001-01-01',quantity:2},{date:'9999-12-31',quantity:3}],b.dates,b.interval,'quantity',b.yearStep).reduce((a,b)=>a+b,0)})()"),5);
// A single period and zero values still render a finite, readable chart.
const single=run("rankingTrendChart('models',[{product_id:1,name:'A < B',sku:'A',quantity:1}],{sort:'quantity',interval:'day',visibleSeries:null},'销量',{start:'2026-11-02',end:'2026-11-02',models:[{id:1,points:[{date:'2026-11-02',quantity:1}]}],undated_order_count:0})");
assert.ok(single.includes('<circle')&&!single.includes('NaN')&&!single.includes('Infinity'));
assert.ok(single.includes('A &lt; B')&&single.includes('单个数据点'));
const zero=run("rankingBarChart('models',[{product_id:1,name:'Zero',sku:'Z',amount:0}],{sort:'amount'},'金额')");
assert.ok(zero.includes('width="0"')&&!zero.includes('NaN'));
console.log('Dashboard chart checks passed: dates, gaps, weeks, months, long ranges, single point, escaping and zero values.');
