'use strict';
const rankingChartColors=['#176e52','#3268a8','#b26a20','#8655a2','#c24d61','#16838a','#6f7831','#6768bf','#975c3d','#526b7a'];
const rankingRowId=(kind,row)=>kind==='models'?row.product_id:row.dealer_id;
const rankingValue=(value,metric)=>metric==='amount'?money(value):new Intl.NumberFormat('pt-BR').format(value)+' '+(metric==='quantity'?L('台','un.','units'):L('笔','pedidos','orders'));
const rankingIntervalName=interval=>({day:L('按天','Diário','Daily'),week:L('按周','Semanal','Weekly'),month:L('按月','Mensal','Monthly'),year:L('按年','Anual','Yearly')})[interval];
function rankingBucket(date,interval,yearStep=1){
    const d=new Date(date+'T00:00:00Z');
    if(interval==='week')d.setUTCDate(d.getUTCDate()-(d.getUTCDay()+6)%7);
    if(interval==='month')d.setUTCDate(1);
    if(interval==='year'){d.setUTCDate(1);d.setUTCMonth(0);d.setUTCFullYear(1+Math.floor((d.getUTCFullYear()-1)/yearStep)*yearStep)}
    return d.toISOString().slice(0,10);
}
function rankingBuckets(start,end,requested='auto'){
    if(!start||!end)return {interval:'day',dates:[]};
    const first=new Date(start+'T00:00:00Z'),last=new Date(end+'T00:00:00Z'),days=(last-first)/86400000+1;
    if(!Number.isFinite(days)||days<1)return {interval:'day',dates:[]};
    const intervals=['day','week','month','year'];
    let interval=requested==='auto'?(days<=62?'day':days<=366?'week':days<=3653?'month':'year'):requested;
    if(!intervals.includes(interval))interval='day';
    // Keep long ranges usable without discarding sales or returning thousands
    // of invisible points. The chart always labels the actual interval used.
    for(;;){
        const yearStep=interval==='year'?Math.max(1,Math.ceil((last.getUTCFullYear()-first.getUTCFullYear()+2)/399)):1;
        const dates=[],cursor=new Date(rankingBucket(start,interval,yearStep)+'T00:00:00Z');
        while(cursor<=last&&dates.length<=400){
            dates.push(cursor.toISOString().slice(0,10));
            if(interval==='day'||interval==='week')cursor.setUTCDate(cursor.getUTCDate()+(interval==='day'?1:7));
            else if(interval==='month')cursor.setUTCMonth(cursor.getUTCMonth()+1);
            else cursor.setUTCFullYear(cursor.getUTCFullYear()+yearStep);
        }
        if(dates.length<=400||interval==='year')return {interval,dates,yearStep};
        interval=intervals[intervals.indexOf(interval)+1];
    }
}
function rankingSeries(points,dates,interval,metric,yearStep=1){
    const sums=new Map();
    for(const p of points){const key=rankingBucket(p.date,interval,yearStep);sums.set(key,(sums.get(key)||0)+p[metric])}
    return dates.map(date=>sums.get(date)||0);
}
function rankingBarChart(kind,rows,options,metricLabel){
    const max=Math.max(0,...rows.map(row=>row[options.sort]));
    return `<div class="ranking-bar-chart" role="group" aria-label="${E(metricLabel)} ${L('柱状图','Gráfico de barras','Bar chart')}"><div class="ranking-chart-caption">${E(metricLabel)} · ${L('同一比例比较','Escala comum','Common scale')}</div><div class="ranking-bars">${rows.map((r,i)=>`<div class="ranking-bar-item"><div class="ranking-bar-label"><span class="ranking-place ${i<3?'ranking-leader':''}">${i+1}</span><div><strong>${E(r.name)}</strong><small>${E(kind==='models'?r.sku:r.code)}</small></div><b>${E(rankingValue(r[options.sort],options.sort))}</b></div><svg class="ranking-bar-svg" viewBox="0 0 500 14" preserveAspectRatio="none" aria-hidden="true"><rect x="0" y="0" width="500" height="14" rx="5" fill="#eef2e9"/><rect x="0" y="0" width="${max?500*r[options.sort]/max:0}" height="14" rx="5" fill="${rankingChartColors[i%rankingChartColors.length]}"/></svg></div>`).join('')}</div><div class="ranking-chart-scale"><span>0</span><span>${E(rankingValue(max,options.sort))}</span></div></div>`;
}
function rankingVisibleRows(kind,rows,options){
    return options.visibleSeries===null?rows.slice(0,5):rows.filter(r=>options.visibleSeries.has(rankingRowId(kind,r)));
}
function rankingTrendChart(kind,rows,options,metricLabel,trends){
    const {interval,dates,yearStep}=rankingBuckets(trends.start,trends.end,options.interval);
    const intervalTitle=yearStep>1?L('每'+yearStep+'年','A cada '+yearStep+' anos','Every '+yearStep+' years'):rankingIntervalName(interval);
    if(!dates.length)return `<div class="empty"><strong>${L('订单缺少所选日期，无法生成趋势','Pedidos sem a data selecionada','Orders are missing the selected date')}</strong><span>${L('请切换订单日期口径查看。','Selecione outro critério de data.','Choose another order date basis.')}</span></div>`;
    const visible=rankingVisibleRows(kind,rows,options),ids=new Set(visible.map(r=>rankingRowId(kind,r)));
    const raw=new Map((trends[kind]||[]).map(s=>[s.id,s.points]));
    const series=visible.map(r=>({row:r,values:rankingSeries(raw.get(rankingRowId(kind,r))||[],dates,interval,options.sort,yearStep),color:rankingChartColors[rows.indexOf(r)%rankingChartColors.length]}));
    const legend=`<div class="ranking-chart-legend" role="group" aria-label="${L('选择趋势线','Selecionar séries','Choose trend lines')}">${rows.map((r,i)=>`<button type="button" class="ranking-legend-button ${ids.has(rankingRowId(kind,r))?'selected':''}" data-act="ranking-series" data-args="${kind}:${rankingRowId(kind,r)}" aria-pressed="${ids.has(rankingRowId(kind,r))}"><svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><rect width="12" height="12" rx="3" fill="${rankingChartColors[i%rankingChartColors.length]}"/></svg><span>${E(r.name)}</span></button>`).join('')}</div>`;
    let chart=`<div class="empty"><strong>${L('请选择需要查看的趋势线','Selecione uma série','Choose a trend line')}</strong></div>`;
    if(series.length){
        const max=Math.max(1,...series.flatMap(s=>s.values)),power=10**Math.floor(Math.log10(max/4)),unit=Math.max(1,[1,2,5,10].find(n=>n*power>=max/4)*power),top=Math.ceil(max/unit)*unit;
        const W=680,H=300,left=options.sort==='amount'?112:52,right=20,upper=24,bottom=52,pw=W-left-right,ph=H-upper-bottom;
        const x=i=>left+(dates.length===1?pw/2:i*pw/(dates.length-1)),y=value=>upper+ph-value/top*ph;
        const ticks=Array.from({length:Math.round(top/unit)+1},(_,i)=>i*unit);
        const grid=ticks.map(value=>`<g><line x1="${left}" x2="${W-right}" y1="${y(value)}" y2="${y(value)}" stroke="#e1e7df"/><text x="${left-9}" y="${y(value)+4}" text-anchor="end" class="ranking-axis-label">${E(options.sort==='amount'?money(value):String(value))}</text></g>`).join('');
        const tickIds=[...new Set(Array.from({length:Math.min(5,dates.length)},(_,i)=>Math.round(i*(dates.length-1)/Math.max(1,Math.min(5,dates.length)-1))))];
        const axis=tickIds.map(i=>`<text x="${x(i)}" y="${H-24}" text-anchor="${dates.length===1?'middle':i===0?'start':i===dates.length-1?'end':'middle'}" class="ranking-axis-label">${E(interval==='year'?dates[i].slice(0,4):interval==='month'?dates[i].slice(0,7):dates[i])}</text>`).join('');
        const lines=series.map(s=>`<g><polyline points="${s.values.map((v,i)=>x(i)+','+y(v)).join(' ')}" fill="none" stroke="${s.color}" stroke-width="2.5" stroke-linejoin="round"/>${s.values.map((value,i)=>`<circle class="ranking-chart-point" cx="${x(i)}" cy="${y(value)}" r="${dates.length<=62?3.5:2}" fill="${s.color}" ${value||dates.length===1?'tabindex="0"':''}><title>${E(s.row.name)} · ${E(dates[i])} · ${E(rankingValue(value,options.sort))}</title></circle>`).join('')}</g>`).join('');
        chart=`<div class="ranking-trend-scroll"><svg class="ranking-trend-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${E(metricLabel)} ${intervalTitle} ${L('趋势图','Tendência','Trend chart')}"><title>${E(metricLabel)} · ${intervalTitle} · ${E(trends.start)} ～ ${E(trends.end)}</title>${grid}${axis}${lines}</svg></div>`;
    }
    return `<div class="ranking-trend-chart"><div class="ranking-chart-caption">${E(metricLabel)} · ${intervalTitle}${options.interval!=='auto'&&options.interval!==interval?' · '+L('已按跨度调整汇总周期','Intervalo ajustado ao período','Interval adjusted to the date range'):''}</div><p class="ranking-chart-help">${L('默认显示前5项，点击图例选择，最多10条曲线；悬停数据点查看数值。','As 5 primeiras séries são exibidas. Clique na legenda (máx. 10); passe o mouse nos pontos.','First 5 series shown. Use the legend to select up to 10; hover over points for values.')}</p>${legend}${chart}<p class="ranking-chart-help">${L('空缺周期补零；首尾周期只计所选日期范围内的数据。','Períodos sem vendas ficam em zero; períodos parciais respeitam as datas selecionadas.','Periods with no sales are zero; partial periods respect the selected date range.')}${dates.length===1?' '+L('当前范围只有一个周期，显示单个数据点。','Apenas um período: exibido como ponto único.','Only one period is available, shown as a single point.'):''}${trends.undated_order_count?' '+E(trends.undated_order_count)+' '+L('笔订单缺少所选日期，趋势未包含。','pedidos sem data foram excluídos da tendência.','orders have no selected date and are excluded from the trend.'):''}</p></div>`;
}
