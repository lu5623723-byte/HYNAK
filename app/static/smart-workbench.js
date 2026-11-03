'use strict';
const workbenchDefaults={urgent_overdue_days:7,pending_days:2,low_stock_units:5,due_soon_days:3,pickup_days:3,reservation_hours:24,policy_days:7};
const workbenchRuleFields={
urgent_overdue_days:[1,90,['逾期达到几天列为高优先级','Dias de atraso para prioridade alta','Overdue days for high priority']],
pending_days:[1,30,['订单/充值创建几天后升级提醒','Dias desde criação para escalar','Days since creation to escalate']],
low_stock_units:[0,1000,['可用库存低于或等于几台提醒','Limite de estoque disponível','Available stock alert threshold']],
due_soon_days:[0,30,['提前几天提醒付款到期','Dias antes do vencimento','Days before payment due']],
pickup_days:[1,30,['出库几天仍待提货提醒','Dias de espera pela retirada','Days awaiting pickup']],
reservation_hours:[1,72,['提前几小时提醒锁货到期','Horas antes da reserva expirar','Hours before reservation expiry']],
policy_days:[1,60,['提前几天提醒政策到期','Dias antes do fim da política','Days before policy expiry']],
};
const workbenchCategories={all:['全部事项','Todos os assuntos','All categories'],orders:['订单审批','Aprovação de pedidos','Order approvals'],finance:['回款与充值','Pagamentos e recargas','Payments and top-ups'],stock:['库存与锁货','Estoque e reservas','Stock and reservations'],credit:['经销商信用','Crédito de clientes','Dealer credit'],delivery:['交付与寄售','Entrega e consignação','Delivery and consignment'],policy:['销售政策','Políticas de vendas','Sales policies'],commissions:['销售佣金','Comissões de vendas','Sales commissions']};
const workbenchPriorities={HIGH:['高优先级','Alta prioridade','High priority'],MEDIUM:['需跟进','Acompanhar','Follow up'],LOW:['提前关注','Atenção preventiva','Plan ahead']};
const workbenchRoleDefaults={
admin:{title:['管理员智能工作台','Central inteligente do administrador','Administrator smart workbench'],categories:['orders','finance','stock','credit','delivery','policy','commissions'],rule_keys:Object.keys(workbenchDefaults),all_label:['全部当前业务','Toda a operação atual','All current operations']},
sales:{title:['销售智能工作台','Central inteligente de vendas','Sales smart workbench'],categories:['orders','finance','stock','credit','delivery','policy','commissions'],rule_keys:['urgent_overdue_days','pending_days','due_soon_days','reservation_hours','policy_days'],all_label:['全部责任业务','Todos os meus clientes e pedidos','All assigned operations']},
finance:{title:['财务智能工作台','Central inteligente financeira','Finance smart workbench'],categories:['finance','credit','commissions'],rule_keys:['urgent_overdue_days','pending_days','due_soon_days'],all_label:['全部财务待办','Todos os assuntos financeiros','All finance operations']},
warehouse:{title:['仓管智能工作台','Central inteligente de estoque','Warehouse smart workbench'],categories:['stock','delivery'],rule_keys:['low_stock_units','pickup_days','reservation_hours'],all_label:['全部仓储交付待办','Todas as tarefas de estoque e entrega','All warehouse and delivery tasks']},
dealer:{title:['经销商智能工作台','Minha central inteligente','Dealer smart workbench'],categories:['orders','finance','credit','delivery','stock'],rule_keys:['urgent_overdue_days','pending_days','due_soon_days','pickup_days','low_stock_units'],all_label:['我的全部业务','Todas as minhas operações','All my operations']}
};
function workbenchProfile(){return workbench.result?.role===D?.user.role?workbench.result.profile:workbenchRoleDefaults[D?.user.role]}
function workbenchRuleEntries(){const keys=workbenchProfile()?.rule_keys||[];return Object.entries(workbenchRuleFields).filter(([key])=>keys.includes(key))}
function workbenchCategoryEntries(){const keys=workbenchProfile()?.categories||[];return Object.entries(workbenchCategories).filter(([key])=>key==='all'||keys.includes(key)).map(([key,label])=>[key,key==='orders'&&is('dealer')?['采购进度','Andamento das compras','Purchase progress']:key==='credit'&&is('dealer')?['我的评级','Minha avaliação','My rating']:key==='stock'&&is('dealer')?['我的寄售库存','Meu estoque consignado','My consignment stock']:label])}
function workbenchMetricCards(){return (workbench.result?.role===D?.user.role?workbench.result.cards:[]).map(c=>`<div><small>${E(L(...c.label))}</small><strong>${c.format==='money'?money(c.value):E(c.value)}</strong>${c.format==='units'?'<span>'+L('台','unidades','units')+'</span>':''}</div>`).join('')}
let workbenchOpenEvidence=new Set();
let workbench={user:null,role:null,result:null,error:'',busy:false,request:0,scope:'all',rules:{...workbenchDefaults},priority:'all',category:'all',query:'',page:1};

function loadWorkbenchPreferences(){
if(workbench.user===D.user.id&&workbench.role===D.user.role)return;
workbenchOpenEvidence=new Set();
workbench={user:D.user.id,role:D.user.role,result:null,error:'',busy:false,request:workbench.request+1,scope:'all',rules:{...workbenchDefaults},priority:'all',category:'all',query:'',page:1};
try{const saved=JSON.parse(localStorage.getItem('hynak_workbench_'+D.user.id));if(!saved)return;
if(['all','selected'].includes(saved.scope))workbench.scope=saved.scope;
if(['all','HIGH','MEDIUM','LOW'].includes(saved.priority))workbench.priority=saved.priority;
if(saved.category==='all'||workbenchRoleDefaults[D.user.role]?.categories.includes(saved.category))workbench.category=saved.category;
for(const [key,[low,high]] of Object.entries(workbenchRuleFields)){const n=saved.rules?.[key];if(Number.isInteger(n)&&n>=low&&n<=high)workbench.rules[key]=n}
}catch{}
}
function saveWorkbenchPreferences(){try{localStorage.setItem('hynak_workbench_'+D.user.id,JSON.stringify({scope:workbench.scope,rules:workbench.rules,priority:workbench.priority,category:workbench.category}))}catch{}}

async function refreshRoleInsights(renderAfter=false){
if(!workbenchRoleDefaults[D?.user.role])return;
loadWorkbenchPreferences();const user=D.user.id,request=++workbench.request;
workbench.busy=true;workbench.error='';
if(renderAfter)renderWorkbench();
const appliedRules=Object.fromEntries(workbenchRuleEntries().map(([key])=>[key,workbench.rules[key]]));
const query=new URLSearchParams({...appliedRules,scope:workbench.scope,...(workbench.scope==='selected'?dashboardFilters:{})});
try{const result=await api('/api/insights?'+query);
if(request===workbench.request&&D?.user.id===user&&result.role===D.user.role){workbench.result=result;workbench.page=1;if(!result.profile.categories.includes(workbench.category))workbench.category='all'}
}catch(e){if(request===workbench.request&&D?.user.id===user)workbench.error=e.message}
finally{if(request===workbench.request){workbench.busy=false;if(renderAfter&&view==='dashboard')renderWorkbench()}}
}

function roleWorkbenchPanel(){
if(!workbenchRoleDefaults[D?.user.role])return '';
const r=workbench.result?.role===D?.user.role?workbench.result:null,m=r?.summary,profile=workbenchProfile(),local=t=>Array.isArray(t)?L(...t):String(t||'');
return `<div id="role-workbench" class="smart-workbench"><section class="panel"><div class="panel-head"><div><p class="eyebrow">HYNAK · ${L('智能经营','GESTÃO INTELIGENTE','SMART OPERATIONS')}</p><h2>${E(local(profile.title))}</h2><p class="sub">${E(local(profile.intro||['正在读取当前角色的业务待办。','Carregando as tarefas deste perfil.','Loading operational tasks for this role.']))}</p></div>${button('wb-refresh',L('重新分析','Analisar novamente','Analyze again'),'','small')}</div>
<div class="panel-body"><div class="workbench-scope"><label for="wb-scope">${L('分析范围','Escopo da análise','Analysis scope')}</label><select id="wb-scope"><option value="all" ${workbench.scope==='all'?'selected':''}>${E(local(profile.all_label))}</option><option value="selected" ${workbench.scope==='selected'?'selected':''}>${L('订单跟随上方已应用筛选','Pedidos conforme filtros aplicados','Orders matching applied dashboard filters')}</option></select><span class="sub">${r?L('分析于 ','Analisado em ','Analyzed at ')+E(paymentLabel(r.checked_at)):''}</span></div>
${r?`<p class="sub"><strong>${L('本次结果范围：','Escopo deste resultado: ','Result scope: ')}</strong>${r.scope==='selected'?L('订单按已应用筛选','Pedidos conforme filtros aplicados','Orders matching applied filters')+' · '+E(r.filters?.start?`${r.filters.start} ～ ${r.filters.end}`:L('全部日期','Todas as datas','All dates')):E(local(profile.all_label))}</p>`:''}<p class="sub">${E(local(profile.scope||['按当前账号权限分析数据。','Análise conforme suas permissões.','Data is analyzed within the current account permissions.']))}</p>
${workbench.error?`<div class="notice warning" role="alert">${L('分析更新失败，请重试。当前保留上次结果：','Falha na atualização; dados anteriores: ','Analysis failed; showing the previous result: ')}${E(workbench.error)}</div>`:''}
${workbench.busy?`<p class="sub" role="status">${L('正在核对最新业务数据…','Conferindo os dados atuais…','Checking current business data…')}</p>`:''}
${m?`<div class="workbench-metrics">${workbenchMetricCards()}</div>`:''}
<details class="workbench-rules" id="workbench-rules"><summary>${L('调整提醒规则','Ajustar regras dos alertas','Adjust alert rules')}</summary><p class="sub">${L('仅调整当前账号的提醒视角，不改变订单审批、价格、付款到期日或业务权限。','Preferências deste usuário; não alteram aprovações, preços, vencimentos ou permissões.','These preferences apply to this account and do not change approvals, prices, due dates, or permissions.')}</p><div class="form-grid">${workbenchRuleEntries().map(([key,[low,high,label]])=>f('wb_'+key,local(label),'number',workbench.rules[key],true,`min="${low}" max="${high}" step="1"`)).join('')}</div><p class="sub" id="workbench-draft-status" role="status"></p><div class="actions">${button('wb-apply',L('应用提醒规则','Aplicar regras','Apply alert rules'),'','primary')}${button('wb-reset',L('恢复默认规则','Restaurar padrões','Restore defaults'))}</div></details>
<div class="workbench-filter"><div class="field"><label for="wb-priority">${L('优先级','Prioridade','Priority')}</label><select id="wb-priority"><option value="all">${L('全部优先级','Todas as prioridades','All priorities')}</option>${Object.entries(workbenchPriorities).map(([key,label])=>`<option value="${key}" ${workbench.priority===key?'selected':''}>${local(label)}</option>`).join('')}</select></div><div class="field"><label for="wb-category">${L('事项类型','Categoria','Category')}</label><select id="wb-category">${workbenchCategoryEntries().map(([key,label])=>`<option value="${key}" ${workbench.category===key?'selected':''}>${local(label)}</option>`).join('')}</select></div><div class="field"><label for="wb-query">${L('查找订单 / 经销商 / SKU','Buscar pedido / cliente / SKU','Find order / dealer / SKU')}</label><input id="wb-query" type="search" value="${E(workbench.query)}" placeholder="${L('输入关键词','Digite uma palavra','Enter a keyword')}"></div></div>
<p class="sub" id="workbench-visible-summary"></p><div id="workbench-alert-list">${workbenchList()}</div>
<p class="sub">${is('warehouse')?L('同一订单可能产生多条交付或库存提醒，台数包含商品与赠品。出库、调拨与实际交付按原授权流程确认。','Um pedido pode gerar vários alertas. As quantidades incluem brindes; expedição, transferência e entrega seguem as autorizações existentes.','One order can generate multiple stock or delivery alerts. Quantities include gifts; dispatch, transfers, and actual delivery retain existing authorization.'):L('同一订单可能触发多条提醒，条数不等于订单笔数。逾期金额按订单去重，未核实水单和充值不计入实收。分析采用可解释规则；每项业务操作仍由相应账号确认。','Um pedido pode gerar vários alertas. O saldo vencido não duplica pedidos; comprovantes e recargas pendentes não são recebimentos. Regras explicáveis; ações exigem a confirmação de cada responsável.','One order can trigger multiple alerts. Overdue balances are deduplicated by order; unverified proofs and top-ups are not receipts. Analysis uses explainable rules, and business actions still require the appropriate account.')}</p></div></section></div>`;
}

function workbenchFiltered(){
const query=workbench.query.trim().toLocaleLowerCase();
return (workbench.result?.role===D?.user.role?workbench.result.alerts:[]).filter(a=>(workbench.priority==='all'||a.priority===workbench.priority)&&(workbench.category==='all'||a.category===workbench.category)&&(!query||[...a.title,...a.reason,...a.advice].join(' ').toLocaleLowerCase().includes(query)));
}
function workbenchActionLabel(action){
if(action.kind==='order')return L('查看订单','Ver pedido','View order')+' #'+action.id;
if(action.kind==='credit')return L('查看评级依据','Ver avaliação','View rating evidence');
if(action.kind==='commissions')return L('查看销售佣金','Ver comissões','View sales commissions');
return L(...({inventory:['查看库存','Ver estoque','View stock'],finance:['前往应收与实收','Ver financeiro','Open finance'],wallet:['查看充值与余额','Ver recargas e saldo','View top-ups and credit'],collections:['查看回款跟进','Ver cobranças','View collections'],consignment:['查看寄售点','Ver consignação','View consignment site'],policies:['查看销售政策','Ver políticas','View sales policies']}[action.kind]||['查看','Ver','View']));
}
function workbenchList(){
if(!workbench.result)return `<p class="sub">${L('点击“重新分析”读取业务待办。','Clique em Analisar novamente.','Select Analyze again to load operational alerts.')}</p>`;
const rows=workbenchFiltered(),pages=Math.max(1,Math.ceil(rows.length/12));workbench.page=Math.min(workbench.page,pages);
const shown=rows.slice((workbench.page-1)*12,workbench.page*12);
return (shown.length?shown.map(a=>`<article class="workbench-alert priority-${a.priority.toLowerCase()}"><div class="workbench-alert-title"><h3>${E(L(...a.title))}</h3><span class="badge ${a.priority==='HIGH'?'red':a.priority==='MEDIUM'?'amber':'green'}">${L(...workbenchPriorities[a.priority])}</span></div><p>${E(L(...a.reason))}</p><div class="workbench-alert-values"><span>${L('负责角色','Responsável','Responsible role')}: ${E(L(...a.owner))}</span>${a.amount!==null?`<strong>${a.facts.declared_only?L('申报金额','Valor declarado','Claimed amount'):L('相关金额','Valor relacionado','Related amount')}: ${money(a.amount)}</strong>`:''}${a.quantity!==null?`<strong>${L('相关数量','Quantidade','Related quantity')}: ${a.quantity} ${L('台','unidades','units')}</strong>`:''}</div><p class="workbench-advice"><strong>${L('建议下一步：','Próximo passo: ','Suggested next step: ')}</strong>${E(L(...a.advice))}</p><details class="workbench-evidence" data-alert-key="${E(a.key)}" ${workbenchOpenEvidence.has(a.key)?'open':''}><summary>${L('查看数据依据','Ver dados de origem','View source evidence')}</summary><dl>${workbenchEvidence(a)}</dl></details><div class="actions">${a.actions.map((action,index)=>button('wb-open',E(workbenchActionLabel(action)),JSON.stringify([a.key,index]),'small')).join('')}</div></article>`).join(''):`<div class="workbench-empty"><strong>${L('当前条件下暂无待办提醒','Nenhum alerta nestes filtros','No alerts match the current filters')}</strong><p>${L('这表示当前规则没有触发提醒，可调整规则或范围后再查看。','Nenhuma regra foi acionada; ajuste as regras ou o escopo.','No configured rule triggered an alert. Adjust the rules or scope to review again.')}</p></div>`)+`<div class="pager"><span>${L('显示','Mostrando','Showing')} ${rows.length?((workbench.page-1)*12+1):0}–${Math.min(workbench.page*12,rows.length)} / ${rows.length} · ${workbench.page}/${pages}</span><div class="actions"><button type="button" data-act="wb-page" data-args="${workbench.page-1}" ${workbench.page===1?'disabled':''}>←</button><button type="button" data-act="wb-page" data-args="${workbench.page+1}" ${workbench.page===pages?'disabled':''}>→</button></div></div>`;
}
function workbenchEvidence(a){
const names={eligibility_changed:['订单结算条件需要重新核对','Condições do pedido devem ser revistas','Order settlement conditions need review'],scoped_remaining:['责任订单待发数量','Demanda pendente dos meus pedidos','Assigned pending units'],scoped_reserved:['责任订单已锁定数量','Reservado para meus pedidos','Assigned reserved units'],scoped_unreserved:['责任订单未锁定需求','Minha demanda sem reserva','Assigned unreserved demand'],remaining_quantity:['待发台数','Unidades a expedir','Pending dispatch units'],gift_quantity:['待发赠品台数','Brindes a expedir','Pending gift units'],expected_unverified:['扣除已核实银行款后的应付金额','Valor restante após créditos verificados','Expected amount after verified receipts'],verified_bank_amount:['本单已核实银行款','Crédito bancário verificado deste pedido','Verified bank receipts for this order'],followup_id:['跟进记录ID','ID do acompanhamento','Follow-up ID'],next_date:['下次跟进日期','Próxima data de acompanhamento','Next follow-up date'],commission_id:['佣金记录ID','ID da comissão','Commission ID'],commission_status:['佣金状态','Status da comissão','Commission status'],order_id:['订单ID','ID do pedido','Order ID'],dealer_id:['经销商ID','ID do cliente','Dealer ID'],number:['订单号','Número do pedido','Order number'],dealer:['经销商','Cliente','Dealer'],due:['付款到期日','Vencimento','Due date'],verified_allocated:['已核实并分配金额','Pagamento verificado alocado','Verified allocated payment'],receivable:['有效应收','Recebível efetivo','Effective receivable'],created:['创建时间','Criado em','Created at'],pending_claim_count:['待核实凭证数','Comprovantes pendentes','Pending proof count'],pending_claim_amount:['待核实申报金额','Valor declarado pendente','Pending claimed amount'],available_credit:['已核实可用余额','Crédito verificado disponível','Verified available credit'],total:['订单总金额','Total do pedido','Order total'],declared_only:['仅为申报，尚未核实','Somente declarado, não verificado','Declared only, not verified'],claim_ids:['对应水单ID','IDs dos comprovantes','Matching claim IDs'],product_id:['商品ID','ID do produto','Product ID'],sku:['SKU','SKU','SKU'],global_remaining:['全局待发数量','Demanda global pendente','Global outstanding dispatch demand'],global_reserved:['需求中已锁定数量','Quantidade reservada','Demand already reserved'],global_unreserved:['全局尚未锁定需求','Demanda global sem reserva','Global unreserved demand'],global_available:['全局未锁定可用库存','Estoque global livre','Global unreserved available stock'],related_order_ids:['所选范围关联订单ID','Pedidos relacionados no escopo','Related order IDs in scope'],warehouse_id:['仓库ID','ID do depósito','Warehouse ID'],expires:['最早锁货到期时间','Primeira expiração da reserva','Earliest reservation expiry'],grade:['自动评级','Nota automática','Automatic grade'],provisional:['样本较少，暂评','Amostra pequena, provisória','Provisional: small sample'],outstanding:['订单欠款','Saldo devedor','Outstanding order balance'],recharge_id:['充值申报ID','ID da recarga','Top-up claim ID'],shipment_id:['出库单ID','ID da expedição','Shipment ID'],missing_serial:['有缺少车辆编号的异常说明','Exceção por falta de identificação','Vehicle-ID exception recorded'],report_id:['寄售报售ID','ID do registro consignado','Consignment report ID'],policy_id:['政策ID','ID da política','Policy ID'],ends:['政策结束日期','Término da política','Policy end date'],needed:['各SKU待发需求（商品ID:数量）','Demanda por SKU (ID:quantidade)','Demand per SKU (product ID:quantity)']};
const monetary=new Set(['verified_allocated','receivable','pending_claim_amount','available_credit','total','outstanding','expected_unverified','verified_bank_amount']);
return Object.entries(a.facts).map(([key,value])=>`<div><dt>${E(names[key]?L(...names[key]):key)}</dt><dd>${monetary.has(key)?money(value):['created','expires'].includes(key)?E(paymentLabel(value)):key==='commission_status'?E(st(value)):E(typeof value==='boolean'?value?L('是','Sim','Yes'):L('否','Não','No'):typeof value==='object'?JSON.stringify(value):value)}</dd></div>`).join('');
}

function renderWorkbenchList(){
if(!$('#workbench-alert-list'))return;
$('#workbench-alert-list').innerHTML=workbenchList();const total=workbenchFiltered().length;
$('#workbench-visible-summary').textContent=L(`筛选后 ${total} 条提醒`,`Após filtros: ${total} alertas`,`${total} alerts after filters`);
}
function renderWorkbench(){const el=$('#role-workbench');if(el){el.outerHTML=roleWorkbenchPanel();renderWorkbenchList()}}

function warehouseDashboardBody(body,result){
body+=roleWorkbenchPanel();
if(!result)return body;
const rows=result.orders.filter(o=>o.source==='CURRENT'&&['CONFIRMED','PARTIALLY_SHIPPED'].includes(o.status));
const ids=new Set(result.orders.filter(o=>!['CANCELED','REJECTED'].includes(o.status)).map(o=>o.id));
const shipments=D.shipments.filter(s=>s.status==='PENDING_PICKUP'&&ids.has(s.order_id));
const pending=o=>o.lines.reduce((sum,l)=>sum+Math.max(0,l.quantity-l.shipped),0);
const period=result.filters.start?`${result.filters.start} ～ ${result.filters.end}`:L('全部日期','Todas as datas','All dates');
body+=`<p class="sub">${E(period)} · ${L('下方订单与出库表按已应用日期筛选，工作台可独立选择职责内全部待办。','As tabelas abaixo seguem as datas aplicadas; a central pode analisar todas as tarefas autorizadas.','The following tables follow applied dates; the workbench can independently analyze all authorized tasks.')}</p>`;
body+=panel(L('所选范围待出库订单','Pedidos selecionados a expedir','Selected orders awaiting dispatch'),simpleTable([
[L('订单 / 经销商','Pedido / cliente','Order / dealer'),o=>`<strong>${E(o.number)}</strong><div class="sub">${E(o.dealer)}</div>`],
[L('待发台数','Unidades pendentes','Pending units'),pending],
[L('其中赠品','Brindes','Including gifts'),o=>o.lines.filter(l=>l.gift).reduce((n,l)=>n+Math.max(0,l.quantity-l.shipped),0)],
[L('状态','Status','Status'),o=>badge(o.status)],['',o=>button('detail',L('查看交付明细','Ver entrega','View fulfillment'),o.id,'small')]],rows));
body+=panel(L('所选范围待提货出库单','Expedições selecionadas aguardando retirada','Selected dispatches awaiting pickup'),simpleTable([
[L('订单','Pedido','Order'),s=>E(D.orders.find(o=>o.id===s.order_id)?.number||s.order_id)],
[L('仓库','Depósito','Warehouse'),s=>E(D.warehouses.find(w=>w.id===s.warehouse_id)?.name||s.warehouse_id)],
[L('状态','Status','Status'),s=>badge(s.status)],
[L('创建时间','Criado em','Created at'),s=>E(paymentLabel(s.created))],['',s=>button('detail',L('查看订单','Ver pedido','View order'),s.order_id,'small')]],shipments));
return body+panel(L('当前库存快览','Estoque atual','Current stock'),simpleTable(inventoryCols(),D.inventory.slice(0,8)));
}

async function workbenchAction(act,arg){
if(!workbenchRoleDefaults[D?.user.role])return;
if(act==='wb-refresh'&&$('#workbench-draft-status')?.textContent)throw Error(L('请先应用已修改的提醒规则','Aplique as regras alteradas primeiro','Apply your edited alert rules first'));
if(act==='wb-page'){workbench.page=Math.max(1,Number(arg)||1);renderWorkbenchList();return}
if(act==='wb-open'){
if(workbench.result?.role!==D.user.role)return;
const [key,index]=JSON.parse(arg),a=workbench.result?.alerts.find(a=>a.key===key)?.actions[index];if(!a)return;
// All destinations are read-only navigation; no automatic approval, payment or stock movement.
if(a.kind==='order'){D=await api('/api/data');detail(a.id);return}
if(a.kind==='credit'){await creditRatingDetail(a.id);return}
if(!['inventory','finance','wallet','collections','consignment','policies','commissions'].includes(a.kind)||!navigationKeys().includes(a.kind))return;
if(a.kind==='inventory'){const w=D.warehouses.find(w=>w.id===a.id);inventorySelection=w?{kind:w.kind,warehouse_id:String(w.id)}:{kind:'',warehouse_id:''}}
if(a.kind==='consignment')consignmentSelection={dealer_id:String(a.id),warehouse_id:String(a.warehouse_id)};
if(a.kind==='wallet')walletDealerId=String(a.id);
view=a.kind;search=a.sku||'';page=1;filterStatus='';await load();return;
}
if(act==='wb-apply'){
const rules={};for(const [key,[min,max,label]] of workbenchRuleEntries()){const raw=$('#f-wb_'+key).value,n=Number(raw);if(!/^\d+$/.test(raw)||!Number.isInteger(n)||n<min||n>max)throw Error(L(...label)+`: ${min}–${max}`);rules[key]=n}
workbench.rules={...workbench.rules,...rules};
}else if(act==='wb-reset')workbench.rules={...workbenchDefaults};
saveWorkbenchPreferences();await refreshRoleInsights(true);
}

document.addEventListener('change',async event=>{
if(!event.target.closest('#role-workbench'))return;
if(event.target.id==='wb-scope'){if($('#workbench-draft-status')?.textContent){event.target.value=workbench.scope;toast(L('请先应用已修改的提醒规则','Aplique as regras alteradas primeiro','Apply your edited alert rules first'),true);return}workbench.scope=event.target.value;saveWorkbenchPreferences();await refreshRoleInsights(true);return}
if(['wb-priority','wb-category'].includes(event.target.id)){workbench[event.target.id==='wb-priority'?'priority':'category']=event.target.value;workbench.page=1;saveWorkbenchPreferences();renderWorkbenchList()}
});
document.addEventListener('input',event=>{
if(event.target.id==='wb-query'){workbench.query=event.target.value;workbench.page=1;renderWorkbenchList();return}
if(event.target.closest('#workbench-rules'))$('#workbench-draft-status').textContent=L('规则已修改，请点击“应用提醒规则”。','Regras alteradas. Clique em Aplicar regras.','Rules changed. Select Apply alert rules.');
});

document.addEventListener('toggle',event=>{if(!event.target.matches?.('.workbench-evidence'))return;const key=event.target.dataset.alertKey;if(event.target.open)workbenchOpenEvidence.add(key);else workbenchOpenEvidence.delete(key)},true);
