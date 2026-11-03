'use strict';
let policyAssistantState=null,policySampleSequence=0;

function policyAssistantDrafts(){
const drafts=(D.policy_drafts||[]).filter(d=>d.status==='DRAFT');
return panel(L('待确认的政策草稿','Rascunhos para revisão','Policy drafts to review'),`<div class="panel-body"><p class="sub">${L('草稿仅管理员可见；完成规则和库存校验后，才能确认发布。','Somente administradores veem rascunhos. Valide regras e estoque antes de publicar.','Drafts are admin-only. Validate rules and stock before confirming publication.')}</p>${drafts.length?drafts.map(d=>`<article class="policy-card"><div class="policy-card-heading"><h3>${E(d.proposal.name)}</h3>${button('policyassistant',L('继续配置 / 校验','Continuar / validar','Continue / validate'),d.id,'small')}</div><p class="sub">${d.source==='OPENAI'?'AI · '+E(d.model):L('手动配置','Configuração manual','Manual configuration')} · v${d.version}</p><p class="wrap-text">${E(d.idea)}</p></article>`).join(''):empty()}</div>`);
}

function policyAssistantForm(id){
if(!is('admin'))return;
const draft=(D.policy_drafts||[]).find(d=>d.id===Number(id));
policyAssistantState={draft:draft||null,proposal:draft?.proposal||null,preview:null,reviewInput:null,busy:false};
const status=D.policy_ai||{};
modal(L('AI 销售政策助手','Assistente de políticas com IA','AI sales policy assistant'),`
<div id="policy-assistant"><div class="notice">${L('描述想法 → 生成草稿 → 校验规则与库存 → 管理员确认发布','Descreva → gere rascunho → valide regras e estoque → confirme a publicação','Describe → draft → validate rules and stock → administrator confirms publication')}</div>
<div class="section-label">${L('1. 描述政策想法','1. Descreva sua ideia','1. Describe your policy idea')}</div>
${ta('pa_idea',L('政策想法','Ideia da política','Policy idea'),draft?.idea||'')}
<p class="sub">${L('例如：11月所有在售车型，同一车型每采购10台送1台，不叠加其他优惠。请写明活动日期和适用条件。','Ex.: em novembro, compre 10 do mesmo modelo e ganhe 1, sem acumular. Informe datas e condições.','Example: in November, buy 10 of the same model and get 1 free, without stacking. Include dates and eligibility.')}</p>
<p class="sub">${E(status.message||'')} ${status.configured?' · '+E(status.model):''}</p>
<p class="sub">${L('AI生成会将本栏想法发送至OpenAI；不自动发送经销商资料、银行流水或库存明细。','A geração envia esta ideia à OpenAI; não envia dados de clientes, bancos ou estoque.','Generation sends this idea to OpenAI; no automatic customer, banking, or stock details are sent.')}</p>
<div class="actions"><button type="button" class="primary" data-act="pa-generate" ${status.configured?'':'disabled'}>${L('AI 生成政策草稿','Gerar rascunho com IA','Generate AI policy draft')}</button>${button('pa-manual',L('手动配置并校验','Configurar e validar manualmente','Configure and validate manually'))}</div>
<div id="pa-progress" class="sub" role="status" aria-live="polite"></div>
<div id="policy-assistant-editor">${draft?policyAssistantEditor(draft.proposal):''}</div></div>`,null);
}

function policySampleRow(){
const id=++policySampleSequence;
return `<div class="pa-sample-row" data-row="${id}"><div class="field"><label for="pa-product-${id}">${L('测试SKU','SKU de teste','Test SKU')}</label><select id="pa-product-${id}" class="pa-product">${D.products.filter(p=>p.active).map(p=>`<option value="${p.id}">${E(p.sku)} · ${E(p.name)}</option>`).join('')}</select></div><div class="field"><label for="pa-qty-${id}">${L('测算采购数量','Quantidade de teste','Test purchase quantity')}</label><input id="pa-qty-${id}" class="pa-quantity" type="number" min="1" max="1000000" step="1" value="10"></div>${button('pa-remove',L('删除此测试SKU','Remover SKU de teste','Remove test SKU'),id,'small')}</div>`;
}

function policyAssistantEditor(p){
return `<div class="section-label">${L('2. 核对可执行规则','2. Revise as regras','2. Review executable rules')}</div>
<p class="sub">${L('自动执行以所选规则为准。当前仅支持所有经销商、全部在售车型；每种政策不可叠加。','A regra selecionada controla a execução. Todos os clientes e modelos ativos, sem acumular políticas.','The selected rule controls execution. All dealers and active models; policies cannot be stacked.')}</p>
${p.unresolved.length?`<div class="notice warning"><strong>${L('以下条件尚未解决，不能发布','Condições pendentes; publicação bloqueada','Unresolved conditions block publication')}</strong><ul>${p.unresolved.map(x=>`<li>${E(x)}</li>`).join('')}</ul><p>${L('请修改上方想法后重新AI生成；或重新手动定义一份受支持的方案。','Revise a ideia e gere novamente, ou defina uma nova política manual.','Revise the idea and regenerate, or manually define a new supported policy.')}</p></div>`:''}
<div class="form-grid">${f('pa_name',L('方案名称','Nome da política','Policy name'),'text',p.name,true,'maxlength="200"')}${sel('pa_kind',L('执行规则','Regra executável','Executable rule'),[['STANDARD',promotionName('STANDARD')],['TEN_PLUS_ONE',promotionName('TEN_PLUS_ONE')],...(!['STANDARD','TEN_PLUS_ONE'].includes(p.kind)?[[p.kind,p.kind==='DEMO'?L('Demo：沿用订单详情流程','Demo: detalhes do pedido','Demo: order-detail workflow'):L('尚未支持的规则','Regra não suportada','Unsupported rule')]]:[])],p.kind)}${f('pa_starts',L('生效开始日期','Início','Starts'),'date',p.starts)}${f('pa_ends',L('结束日期（可留空）','Término (opcional)','Ends (optional)'),'date',p.ends,false)}</div>
${ta('pa_description',L('政策说明','Instruções','Policy instructions'),p.description)}
<div class="section-label">${L('3. 测算订单与库存联动','3. Simule pedido e estoque','3. Simulate an order and stock')}</div>
<p class="sub">${L('测试数量仅用于测算。不会创建真实订单、占用库存或触发发货。','A simulação não cria pedidos, reservas ou expedições.','Simulation does not create orders, reserve stock, or dispatch goods.')}</p>
${sel('pa_warehouse',L('库存测算范围','Local para simulação','Stock simulation scope'),[['',L('全部一级 / 二级仓（实际出库仍逐仓校验）','Todos os depósitos primários/secundários','All primary/secondary warehouses')],...D.warehouses.filter(w=>w.kind!=='CONSIGNMENT').map(w=>[w.id,warehouseLabel(w)])],'')}
<div id="pa-sample-lines">${policySampleRow()}</div><div class="actions">${button('pa-add',L('＋ 增加测试SKU','＋ Adicionar SKU de teste','＋ Add test SKU'))}${button('pa-preview',L('保存草稿并校验','Salvar e validar','Save draft and validate'),'','primary')}</div>
<div id="pa-review" aria-live="polite"></div>
<div class="section-label">${L('4. 管理员确认','4. Confirmação do administrador','4. Administrator confirmation')}</div>
<label class="policy-choice"><input id="pa-confirm" type="checkbox" disabled><span>${L('我已核对规则、有效期、库存测算和执行流程，同意发布此政策。','Revisei regras, datas, estoque e fluxo; confirmo a publicação.','I reviewed the rules, dates, stock simulation, and workflow and approve publication.')}</span></label>
<button type="button" id="pa-publish" class="primary" data-act="pa-publish" disabled>${L('确认并发布销售政策','Confirmar e publicar','Confirm and publish policy')}</button>`;
}

function invalidatePolicyReview(){
if(!policyAssistantState||policyAssistantState.busy)return;
policyAssistantState.preview=null;policyAssistantState.reviewInput=null;
const check=$('#pa-confirm');if(check){check.checked=false;check.disabled=true}
if($('#pa-publish'))$('#pa-publish').disabled=true;
if($('#pa-review'))$('#pa-review').innerHTML=`<p class="sub">${L('内容已修改，请重新校验。','Alterado. Valide novamente.','Changed. Validate again.')}</p>`;
}

function policyReviewHtml(r){
return `<section class="pa-review-result"><h3>${r.publishable?L('规则可执行，请核对后发布','Regras executáveis; revise antes de publicar','Executable rules; review before publishing'):L('存在待解决条件，暂不能发布','Condições pendentes; publicação bloqueada','Unresolved conditions block publication')}</h3>
<p class="sub">${L('校验时间','Verificado em','Checked at')}: ${E(paymentLabel(r.checked_at))} · ${E(promotionName(r.rule))}</p>
${r.blockers.length?`<div class="notice warning"><ul>${r.blockers.map(x=>`<li>${E(x)}</li>`).join('')}</ul></div>`:''}
${simpleTable([['SKU',x=>E(x.sku)],[L('采购 / 赠品 / 总需求','Compra / brinde / total','Purchase / gift / total'),x=>`${x.quantity} / ${x.gift} / ${x.needed}`],[L('可用 / 缺货','Disponível / falta','Available / shortage'),x=>`${x.available} / ${x.shortage}`],[L('商品金额','Valor','Product amount'),x=>money(x.amount)]],r.lines)}
<div class="totals"><span>${L('测算商品总额（不含运费）','Total simulado (sem frete)','Simulated total (excluding freight)')}</span><strong>${money(r.total)}</strong></div>
<details><summary>${L('查看各仓库的库存依据','Estoque por depósito','Warehouse stock details')}</summary>${r.lines.map(x=>`<p><strong>${E(x.sku)}</strong></p>${simpleTable([[L('仓库','Depósito','Warehouse'),w=>E(w.warehouse)],[L('在库','Em estoque','On hand'),w=>w.on_hand],[L('已锁定','Reservado','Reserved'),w=>w.reserved],[L('可用','Disponível','Available'),w=>w.available]],x.warehouses)}`).join('')}</details>
<ul class="pa-warnings">${r.warnings.map(x=>`<li>${E(x)}</li>`).join('')}</ul>
<h3>${L('发布后的联动流程','Fluxo após publicação','Workflow after publication')}</h3><ol>${r.steps.map(x=>`<li>${E(x)}</li>`).join('')}</ol></section>`;
}

async function policyAssistantAction(act,arg){
if(!is('admin')||!$('#policy-assistant')||!policyAssistantState||policyAssistantState.busy)return;
const state=policyAssistantState,progress=$('#pa-progress'),error=$('#dialog-form .form-error');
error.textContent='';
if(act==='pa-add'){$('#pa-sample-lines').insertAdjacentHTML('beforeend',policySampleRow());invalidatePolicyReview();return}
if(act==='pa-remove'){const row=$(`#pa-sample-lines [data-row="${Number(arg)}"]`);if(row)row.remove();invalidatePolicyReview();return}
if(act==='pa-manual'){
state.draft=null;state.proposal={name:'',kind:'STANDARD',starts:day(),ends:null,description:'',scope:'ALL',unresolved:[]};
state.preview=null;state.reviewInput=null;$('#policy-assistant-editor').innerHTML=policyAssistantEditor(state.proposal);
progress.textContent=L('当前为手动配置，不是AI生成结果。','Configuração manual; não foi gerada por IA.','Manual configuration; this is not an AI-generated result.');return;
}
const controls=[...document.querySelectorAll('#policy-assistant input,#policy-assistant textarea,#policy-assistant select,#policy-assistant button')],disabled=controls.map(el=>el.disabled);
state.busy=true;controls.forEach(el=>el.disabled=true);
try{
if(act==='pa-generate'){
progress.textContent=L('AI正在整理想法，仅生成草稿…','Gerando rascunho…','AI is drafting your idea…');
const draft=await api('/api/policies/assistant/generate',{idea:$('#f-pa_idea').value},'POST',crypto.randomUUID());
if(policyAssistantState!==state||!$('#policy-assistant'))return;
state.draft=draft;state.proposal=draft.proposal;state.preview=null;state.reviewInput=null;
$('#policy-assistant-editor').innerHTML=policyAssistantEditor(draft.proposal);
progress.textContent=L('AI草稿已保存，尚未发布。请核对规则并校验。','Rascunho salvo, ainda não publicado. Revise e valide.','AI draft saved, not published. Review and validate.');
}else if(act==='pa-preview'){
requirePolicyEditor();progress.textContent=L('正在保存并校验最新库存…','Salvando e validando estoque…','Saving and checking current stock…');
const proposal={...state.proposal,name:$('#f-pa_name').value,kind:$('#f-pa_kind').value,starts:$('#f-pa_starts').value,ends:$('#f-pa_ends').value||null,description:$('#f-pa_description').value};
const sample=[...document.querySelectorAll('.pa-sample-row')].map(row=>({product_id:Number(row.querySelector('.pa-product').value),quantity:Number(row.querySelector('.pa-quantity').value)})),warehouse_id=$('#f-pa_warehouse').value?Number($('#f-pa_warehouse').value):null;
const draft=await api('/api/action/policy-draft.save',{id:state.draft?.id,version:state.draft?.version,idea:$('#f-pa_idea').value,proposal});
if(policyAssistantState!==state||!$('#policy-assistant'))return;
state.draft=draft;state.proposal=draft.proposal;
const input={id:draft.id,version:draft.version,warehouse_id,sample};
const preview=await api('/api/action/policy-draft.preview',input);
if(policyAssistantState!==state||!$('#policy-assistant'))return;
state.preview=preview;state.reviewInput=input;$('#pa-review').innerHTML=policyReviewHtml(preview);
progress.textContent=L('校验完成；没有创建订单或改变库存。','Validação concluída, sem alterar pedidos ou estoque.','Validation complete; no orders or stock were changed.');
}else if(act==='pa-publish'){
if(!state.preview?.publishable||!state.reviewInput||!$('#pa-confirm')?.checked)throw Error(L('请先校验并勾选确认','Valide e confirme primeiro','Validate and confirm first'));
await api('/api/action/policy-draft.publish',{...state.reviewInput,review_token:state.preview.review_token,confirmed:true});
if(policyAssistantState===state){$('#modal').close();policyAssistantState=null;await load();toast(L('政策已发布，销售端将同步更新','Política publicada; vendas será atualizada','Policy published; sales will sync automatically'))}
}
}catch(e){
if(policyAssistantState===state&&$('#policy-assistant')){error.textContent=e.message;progress.textContent='';if(act==='pa-publish'){state.preview=null;state.reviewInput=null}}
}finally{
state.busy=false;
if(policyAssistantState===state&&$('#policy-assistant')){
controls.forEach((el,i)=>{if(el.isConnected)el.disabled=disabled[i]});
const confirmation=$('#pa-confirm');if(confirmation){confirmation.disabled=!state.preview?.publishable;if(!state.preview?.publishable)confirmation.checked=false}
if($('#pa-publish'))$('#pa-publish').disabled=!state.preview?.publishable||!confirmation?.checked;
}
}
}

function requirePolicyEditor(){if(!$('#f-pa_name'))throw Error(L('请先生成或手动配置草稿','Crie um rascunho primeiro','Create a draft first'))}
for(const type of ['input','change'])document.addEventListener(type,event=>{
if(!event.target.closest('#policy-assistant'))return;
if(event.target.id==='pa-confirm'){$('#pa-publish').disabled=!policyAssistantState?.preview?.publishable||!event.target.checked;return}
invalidatePolicyReview();
});
