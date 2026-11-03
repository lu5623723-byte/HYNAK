"""Read-only, explainable operational alerts. No approval or ledger writes."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .core import integer, roles, now
from .models import Reservation
from .views import snapshot

VERSION='HYNAK-INSIGHTS-v1'
ACTIVE={'CONFIRMED','PARTIALLY_SHIPPED','SHIPPED','CLOSED'}
OPEN={'CONFIRMED','PARTIALLY_SHIPPED'}
SETTINGS={
    'urgent_overdue_days':(7,1,90),'pending_days':(2,1,30),'low_stock_units':(5,0,1000),
    'due_soon_days':(3,0,30),'pickup_days':(3,1,30),'reservation_hours':(24,1,72),'policy_days':(7,1,60),
}


def settings(values=None):
    values=values or {}
    return {key:integer(values.get(key,default),low,high) for key,(default,low,high) in SETTINGS.items()}


def msg(zh,pt,en):return [zh,pt,en]
def instant(value):
    if not value:return None
    parsed=datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def analyze(data,reservations=(),*,at=None,options=None,order_ids=None,dealer_ids=None):
    at=instant(at or now());day=at.astimezone(ZoneInfo('America/Sao_Paulo')).date()
    rules=settings(options);alerts=[]
    orders={o['id']:o for o in data['orders']};dealers={d['id']:d for d in data['dealers']}
    products={p['id']:p for p in data['products']};warehouses={w['id']:w for w in data['warehouses']}
    visible_orders=set(orders) if order_ids is None else set(order_ids)
    visible_dealers=set(dealers) if dealer_ids is None else set(dealer_ids)
    eligible={oid:o for oid,o in orders.items() if o['status'] not in {'CANCELED','REJECTED'}}
    wallets={w['dealer_id']:w for w in data['wallets']}
    pending_claims=defaultdict(list)
    for claim in data['claims']:
        if claim['status']=='PENDING':pending_claims[claim['order_id']].append(claim)
    def age(value):
        dt=instant(value)
        return max(0,(day-dt.astimezone(ZoneInfo('America/Sao_Paulo')).date()).days) if dt else 0
    def action(kind,identity=None,**kw):return dict(kind=kind,id=identity,**kw)
    def add(code,entity,category,priority,title,reason,advice,owner,*,amount=None,quantity=None,days=0,actions=(),**facts):
        alerts.append(dict(key=f'{code}:{entity}',code=code,category=category,priority=priority,title=title,reason=reason,
                           advice=advice,owner=owner,amount=amount,quantity=quantity,days=days,actions=list(actions),facts=facts))
    for oid,o in eligible.items():
        if oid not in visible_orders:continue
        ref=f'{o["number"]} · {o["dealer"]}';base=dict(order_id=oid,dealer_id=o['dealer_id'],number=o['number'],dealer=o['dealer'])
        due=datetime.fromisoformat(o['due']).date();days=(day-due).days
        if o['outstanding']>0 and o['status'] in ACTIVE:
            if days>0:
                add('OVERDUE',oid,'finance','HIGH' if days>=rules['urgent_overdue_days'] else 'MEDIUM',
                    msg('订单款项已逾期','Pagamento vencido','Order payment overdue'),
                    msg(f'{ref}，付款到期日 {o["due"]}，已逾期 {days} 天。',f'{ref}; vencimento {o["due"]}, atraso {days} dias.',f'{ref}; due {o["due"]}, {days} days overdue.'),
                    msg('查看已核实付款和剩余应收，由责任销售跟进回款并记录下一次跟进日期。','Confira pagamentos verificados e registre a próxima cobrança.','Review verified payments and record the next collection follow-up.'),
                    msg('销售 / 财务','Vendas / financeiro','Sales / finance'),amount=o['outstanding'],days=days,actions=[action('order',oid),action('collections',o['dealer_id'])],due=o['due'],verified_allocated=o['received'],receivable=o['receivable'],**base)
            elif -days<=rules['due_soon_days']:
                add('DUE_SOON',oid,'finance','MEDIUM' if days==0 else 'LOW',msg('订单款项即将到期','Pagamento próximo do vencimento','Payment due soon'),
                    msg(f'{ref}，约定到期日 {o["due"]}。',f'{ref}; vencimento {o["due"]}.',f'{ref}; due {o["due"]}.'),
                    msg('提前确认付款安排；到账后由财务或管理员按原权限核实并抵扣。','Confirme a programação do pagamento e verifique o recebimento.','Confirm the payment plan and verify actual receipt using existing permissions.'),
                    msg('销售 / 财务','Vendas / financeiro','Sales / finance'),amount=o['outstanding'],actions=[action('order',oid)],due=o['due'],**base)
        if o['source']!='CURRENT':continue
        pending_age=age(o['created']);priority='HIGH' if pending_age>=rules['pending_days'] else 'MEDIUM'
        created_day=instant(o['created']).astimezone(ZoneInfo('America/Sao_Paulo')).date().isoformat()
        if o['status']=='WAITING_INTERNAL_REVIEW':
            add('ADMIN_REVIEW',oid,'orders',priority,msg('订单待管理员审核','Pedido aguardando administrador','Order awaiting administrator review'),
                msg(f'{ref}，订单已创建 {pending_age} 天，尚未完成管理员审核。',f'{ref}; criado há {pending_age} dias, aprovação pendente.',f'{ref}; created {pending_age} days ago, administrator approval pending.'),
                msg('核对销售类型、政策、价格和备注，在订单详情中处理审批。','Revise tipo, política, preço e notas nos detalhes do pedido.','Review sale type, policy, pricing, and notes in order details.'),
                msg('管理员','Administrador','Administrator'),amount=o['total'],days=pending_age,actions=[action('order',oid)],created=o['created'],**base)
        elif o['status']=='WAITING_FINANCE_REVIEW':
            if o.get('payment_method')=='BALANCE':
                free=wallets.get(o['dealer_id'],{}).get('available',0)
                add('BALANCE_ORDER',oid,'finance',priority,msg('余额付款订单待生效','Pedido com saldo pendente','Balance-funded order pending activation'),
                    msg(f'{ref}；已核实可用余额需覆盖整单金额。',f'{ref}; o saldo verificado deve cobrir o pedido completo.',f'{ref}; verified available credit must cover the full order.'),
                    msg('核对可用余额、优先抵扣的旧欠款及政策有效期；新充值仍需人工核实。','Confira saldo disponível, dívidas anteriores e validade da política.','Review available credit, older debts with priority, and policy validity; new top-ups require verification.'),
                    msg('财务 / 管理员','Financeiro / administrador','Finance / administrator'),amount=max(0,o['total']-free),days=pending_age,actions=[action('order',oid),action('wallet',o['dealer_id'])],available_credit=free,total=o['total'],**base)
            else:
                claims=pending_claims.get(oid,[])
                add('CASH_REVIEW',oid,'finance',priority,msg('现付订单待财务核实','Pedido à vista aguardando verificação','Cash order awaiting finance verification'),
                    msg(f'{ref}，有 {len(claims)} 笔待核实水单；订单创建于 {created_day}。',f'{ref}; {len(claims)} comprovantes pendentes; criado em {created_day}.',f'{ref}; {len(claims)} pending proofs; created {created_day}.'),
                    msg('由财务核对银行实际到账、金额和日期；截图申报不等于实收，足额核实后按原流程生效。','O financeiro confere crédito bancário, valor e data; comprovante não é recebimento.','Finance must verify actual bank receipt, amount, and date. A submitted proof is not verified cash.'),
                    msg('财务','Financeiro','Finance'),amount=o['total'],days=pending_age,actions=[action('order',oid),action('finance')],pending_claim_count=len(claims),pending_claim_amount=sum(c['amount'] for c in claims),**base)
        elif o['status'] in {'DRAFT','WAITING_DEALER_CONFIRMATION','EXPIRED'} and pending_age>=rules['pending_days']:
            state={'DRAFT':msg('草稿','Rascunho','Draft'),'WAITING_DEALER_CONFIRMATION':msg('待客户确认','Aguardando cliente','Awaiting dealer confirmation'),'EXPIRED':msg('报价已过期','Cotação expirada','Quote expired')}[o['status']]
            add('ORDER_STALLED',oid,'orders','LOW',msg('订单流程待跟进','Pedido precisa de acompanhamento','Order workflow needs follow-up'),
                msg(f'{ref}，当前为{state[0]}，订单已创建 {pending_age} 天。',f'{ref}; {state[1]}, criado há {pending_age} dias.',f'{ref}; {state[2]}, created {pending_age} days ago.'),
                msg('核对资料、付款安排或客户确认情况；需要修订时使用原订单流程。','Confira documentos, pagamento e confirmação do cliente.','Review documents, payment arrangements, or dealer confirmation using the existing workflow.'),
                msg('销售 / 管理员','Vendas / administrador','Sales / administrator'),amount=o['total'],days=pending_age,actions=[action('order',oid)],created=o['created'],**base)
        if o['status'] in ACTIVE and pending_claims.get(oid):
            claims=pending_claims[oid]
            add('PAYMENT_PROOFS',oid,'finance','MEDIUM',msg('生效订单有付款凭证待核实','Comprovantes pendentes em pedido ativo','Active order has unverified payment proofs'),
                msg(f'{ref}，共 {len(claims)} 笔待核实申报。',f'{ref}; {len(claims)} comprovantes pendentes.',f'{ref}; {len(claims)} pending payment claims.'),
                msg('逐笔核对对应日期、水单和实际到账，核实后再抵扣本单余额。','Confira datas, comprovantes e crédito bancário antes de alocar.','Check each date, matching proof, and bank receipt before allocating payment.'),
                msg('财务 / 管理员','Financeiro / administrador','Finance / administrator'),amount=sum(c['amount'] for c in claims),actions=[action('order',oid)],declared_only=True,claim_ids=[c['id'] for c in claims],**base)

    supply={w['id']:w for w in data['warehouses'] if w['kind'] in {'PRIMARY','SECONDARY'}}
    stock={(i['warehouse_id'],i['product_id']):i for i in data['inventory'] if i['warehouse_id'] in supply}
    free={pid:sum(max(0,i['available']) for i in stock.values() if i['product_id']==pid) for pid in products}
    demand=defaultdict(lambda:dict(remaining=0,unreserved=0,reserved=0,orders=set(),gifts=0))
    own_res=defaultdict(int);expiring=defaultdict(list)
    for r in reservations:
        o=eligible.get(r['order_id']);expiry=instant(r['expires'])
        if not r['active'] or not o or o['status'] not in OPEN:continue
        own_res[(r['order_id'],r['warehouse_id'],r['product_id'])]+=r['quantity']
        if o['received']==0 and expiry and at<expiry<=at+timedelta(hours=rules['reservation_hours']) and o['id'] in visible_orders:expiring[o['id']].append(r)
    for o in eligible.values():
        if o['source']!='CURRENT' or o['status'] not in OPEN:continue
        required=defaultdict(int)
        for line in o['lines']:
            remaining=max(0,line['quantity']-line['shipped'])
            if not remaining:continue
            pid=line['product_id'];required[pid]+=remaining;r=demand[pid]
            r['remaining']+=remaining;r['reserved']+=min(remaining,line['reserved']);r['unreserved']+=max(0,remaining-line['reserved']);r['orders'].add(o['id']);r['gifts']+=remaining if line['gift'] else 0
        if o['id'] not in visible_orders or not required:continue
        capacities={wid:{pid:max(0,stock.get((wid,pid),{}).get('available',0)+own_res[(o['id'],wid,pid)]) for pid in required} for wid in supply}
        pooled=all(sum(c[pid] for c in capacities.values())>=qty for pid,qty in required.items())
        fits=any(all(c[pid]>=qty for pid,qty in required.items()) for c in capacities.values())
        if pooled and not fits:
            add('TRANSFER_NEEDED',o['id'],'stock','MEDIUM',msg('整单库存分散，需核对调拨','Estoque do pedido distribuído entre depósitos','Order stock is split across warehouses'),
                msg(f'{o["number"]} 各SKU总量可覆盖需求，但目前没有单个仓库可完成整单。',f'{o["number"]}: saldo total suficiente, mas nenhum depósito atende o pedido completo.',f'{o["number"]}: combined stock covers demand, but no single warehouse can fulfill the full order.'),
                msg('由仓管选择出库仓，核对可调数量后办理调拨；部分出库仍按原管理员授权处理。','Defina o depósito e confira transferências; envio parcial segue a autorização existente.','Choose a dispatch warehouse and verify a transfer. Partial shipments retain existing authorization requirements.'),
                msg('仓管','Estoque','Warehouse'),quantity=sum(required.values()),actions=[action('order',o['id']),action('inventory')],order_id=o['id'],needed=dict(required))
    shortage_products=set()
    for pid,r in demand.items():
        shortage=max(0,r['unreserved']-free.get(pid,0));related=sorted(r['orders']&visible_orders)
        if not shortage or not related:continue
        shortage_products.add(pid);p=products[pid]
        add('STOCK_SHORTAGE',pid,'stock','HIGH',msg('生效订单商品或赠品缺货','Falta de produtos ou brindes para pedidos ativos','Active orders have a product or gift shortage'),
            msg(f'{p["sku"]}：全局未锁定需求 {r["unreserved"]} 台，可用 {free[pid]} 台，缺 {shortage} 台；待发含赠品 {r["gifts"]} 台。',f'{p["sku"]}: demanda sem reserva {r["unreserved"]}, livre {free[pid]}, falta {shortage}, brindes pendentes {r["gifts"]}.',f'{p["sku"]}: unreserved demand {r["unreserved"]}, free {free[pid]}, shortage {shortage}; pending gifts {r["gifts"]}.'),
            msg('核对一级和二级仓的可用库存及已锁货，优先安排补货；赠品也需要实际库存。','Confira estoque e reservas nos depósitos; brindes também exigem estoque real.','Review primary/secondary stock and reservations and arrange replenishment; gifts also need physical inventory.'),
            msg('仓管 / 管理员','Estoque / administrador','Warehouse / administrator'),quantity=shortage,actions=[*[action('order',oid) for oid in related[:3]],action('inventory')],product_id=pid,sku=p['sku'],global_remaining=r['remaining'],global_reserved=r['reserved'],global_unreserved=r['unreserved'],global_available=free[pid],related_order_ids=related)
    for pid,p in products.items():
        if not p['active'] or pid in shortage_products or free.get(pid,0)>rules['low_stock_units']:continue
        add('LOW_STOCK',pid,'stock','MEDIUM' if free.get(pid,0)==0 else 'LOW',msg('在售车型可用库存偏低','Estoque disponível baixo','Low available stock for an active model'),
            msg(f'{p["sku"]} 可用 {free.get(pid,0)} 台，提醒阈值 {rules["low_stock_units"]} 台。',f'{p["sku"]}: disponível {free.get(pid,0)}, limite {rules["low_stock_units"]}.',f'{p["sku"]}: available {free.get(pid,0)}, alert threshold {rules["low_stock_units"]}.'),
            msg('结合近期订单需求核对补货计划；寄售点库存不作为仓库可售库存计算。','Revise reposição; estoque em consignação não conta como estoque de venda do depósito.','Review replenishment against order demand; consignment-site stock is excluded from warehouse availability.'),
            msg('仓管 / 管理员','Estoque / administrador','Warehouse / administrator'),quantity=free.get(pid,0),actions=[action('inventory')],product_id=pid,sku=p['sku'])
    for i in stock.values():
        if i['quantity']>=0:continue
        add('NEGATIVE_STOCK',f'{i["warehouse_id"]}-{i["product_id"]}','stock','HIGH',msg('仓库账面库存为负数','Saldo físico negativo','Negative warehouse on-hand balance'),
            msg(f'{i["warehouse"]} · {i["sku"]}，账面库存 {i["quantity"]} 台。',f'{i["warehouse"]} · {i["sku"]}: saldo {i["quantity"]}.',f'{i["warehouse"]} · {i["sku"]}: on-hand balance {i["quantity"]}.'),
            msg('核对实物、出入库流水及管理员例外原因，按原权限处理盘点差异。','Confira estoque físico, lançamentos e justificativas de exceção.','Reconcile physical stock, ledger entries, and administrator exception reasons.'),
            msg('仓管 / 管理员','Estoque / administrador','Warehouse / administrator'),quantity=i['quantity'],actions=[action('inventory',i['warehouse_id'],sku=i['sku'])],warehouse_id=i['warehouse_id'],product_id=i['product_id'])
    for oid,rows in expiring.items():
        o=orders[oid];expiry=min(r['expires'] for r in rows)
        add('RESERVATION_EXPIRING',oid,'stock','MEDIUM',msg('无已抵扣实收的锁货即将到期','Reserva próxima de expirar sem pagamento alocado','Stock reservation expires soon without allocated receipts'),
            msg(f'{o["number"]} 最早锁货到期 {expiry}，本单尚无已核实并分配的实收。',f'{o["number"]}: primeira expiração {expiry}, sem recebimento verificado alocado.',f'{o["number"]}: earliest expiry {expiry}; no verified allocated receipt for this order.'),
            msg('核对实际付款和抵扣情况；截图或未分配余额不视作本单已收款。','Confira pagamento e alocação; comprovantes e saldo não alocado não contam.','Check actual payment and allocation; a proof or unallocated credit is not payment of this order.'),
            msg('销售 / 财务','Vendas / financeiro','Sales / finance'),quantity=sum(r['quantity'] for r in rows),actions=[action('order',oid)],order_id=oid,expires=expiry)

    for d in data['dealers']:
        if d['id'] not in visible_dealers:continue
        c=d.get('credit_rating',{})
        if c.get('grade') in {'C','D'}:
            add('CREDIT_RISK',d['id'],'credit','HIGH' if c['grade']=='D' else 'MEDIUM',msg('经销商信用指标需关注','Indicadores de crédito requerem atenção','Dealer credit indicators need attention'),
                msg(f'{d["name"]} 自动评级 {c["grade"]}'+('（样本较少，暂评）' if c.get('provisional') else '')+f'，当前最长未付逾期 {c["max_overdue_days"]} 天。',f'{d["name"]}: nota {c["grade"]}, atraso máximo {c["max_overdue_days"]} dias'+(' (amostra pequena)' if c.get('provisional') else '')+'.',f'{d["name"]}: grade {c["grade"]}, maximum unpaid overdue {c["max_overdue_days"]} days'+(' (provisional: small sample)' if c.get('provisional') else '')+'.'),
                msg('查看付款及时度、金额履约和采购量的具体依据，核对逾期原因并安排跟进。','Revise pontualidade, valores e volume e planeje acompanhamento.','Review payment timing, amount fulfillment, and purchase volume evidence and arrange follow-up.'),
                msg('销售 / 管理员','Vendas / administrador','Sales / administrator'),amount=c.get('overdue_amount',0),days=c.get('max_overdue_days',0),actions=[action('credit',d['id']),action('collections',d['id'])],dealer_id=d['id'],grade=c['grade'],provisional=c.get('provisional',False))
        w=wallets.get(d['id'],{})
        if w.get('available',0)>0 and d['outstanding']>0:
            add('UNALLOCATED_CREDIT',d['id'],'finance','MEDIUM',msg('有可用余额及未结订单，需核对抵扣','Crédito disponível e pedidos em aberto','Available credit and outstanding orders need allocation review'),
                msg(f'{d["name"]} 同时有已核实可用余额和订单欠款。',f'{d["name"]} tem crédito verificado disponível e dívida em pedidos.',f'{d["name"]} has verified available credit and outstanding order debt.'),
                msg('核对自动抵扣设置、订单到期顺序与资金归属，再按原流程分配实收。','Confira alocação automática, vencimentos e origem dos recursos.','Review automatic allocation settings, due-date order, and fund attribution before allocating receipts.'),
                msg('财务 / 管理员','Financeiro / administrador','Finance / administrator'),amount=min(w['available'],d['outstanding']),actions=[action('wallet',d['id']),action('finance')],dealer_id=d['id'],available_credit=w['available'],outstanding=d['outstanding'])
    for r in data['recharges']:
        if r['status']!='PENDING' or r['dealer_id'] not in visible_dealers:continue
        d=dealers[r['dealer_id']];days=age(r['created'])
        add('RECHARGE_REVIEW',r['id'],'finance','HIGH' if days>=rules['pending_days'] else 'MEDIUM',msg('经销商充值待核实','Recarga aguardando verificação','Dealer top-up awaiting verification'),
            msg(f'{d["name"]}，申报付款日期 {r["paid_date"]}，充值尚未核实。',f'{d["name"]}: data declarada {r["paid_date"]}, recarga não verificada.',f'{d["name"]}: claimed payment date {r["paid_date"]}; top-up is unverified.'),
            msg('由财务或管理员核对银行到账；核实后才可计入余额和自动抵扣。','Financeiro ou administrador verifica o banco antes de creditar o saldo.','Finance or an administrator must verify bank receipt before crediting the wallet and allocating funds.'),
            msg('财务 / 管理员','Financeiro / administrador','Finance / administrator'),amount=r['amount'],days=days,actions=[action('wallet',r['dealer_id'])],recharge_id=r['id'],dealer_id=r['dealer_id'],declared_only=True)
    for s in data['shipments']:
        if s['status']!='PENDING_PICKUP' or s['order_id'] not in visible_orders or s['order_id'] not in eligible:continue
        days=age(s['created']);o=orders[s['order_id']]
        missing_sn=not str(s.get('serials','')).strip() and bool(s.get('exception'))
        if days<rules['pickup_days'] and not missing_sn:continue
        add('DELIVERY_FOLLOWUP',s['id'],'delivery','MEDIUM',msg('出库交付待跟进','Expedição precisa de acompanhamento','Dispatch or delivery needs follow-up'),
            msg(f'{o["number"]} · 出库单 #{s["id"]}，已创建 {days} 天，仍待提货。'+(' 已记录缺少车辆编号的异常说明。' if missing_sn else ''),f'{o["number"]} · expedição #{s["id"]}, criada há {days} dias e aguardando retirada.'+(' Há uma exceção de identificação do veículo.' if missing_sn else ''),f'{o["number"]} · dispatch #{s["id"]}, created {days} days ago, awaiting pickup.'+(' A missing vehicle-ID exception is recorded.' if missing_sn else '')),
            msg('由仓管核实提货、车辆编号及异常说明，实际交付后再更新状态。','O estoque confirma retirada, identificação e exceções antes de marcar entregue.','Warehouse staff must verify pickup, vehicle IDs, and exceptions before marking actual delivery.'),
            msg('仓管','Estoque','Warehouse'),days=days,actions=[action('order',s['order_id'])],shipment_id=s['id'],order_id=s['order_id'],missing_serial=missing_sn)
    for r in data['reports']:
        if r['status']!='PENDING' or r['dealer_id'] not in visible_dealers:continue
        if order_ids is not None and r.get('source_order_id') not in visible_orders:continue
        w=warehouses.get(r['warehouse_id']);d=dealers[r['dealer_id']]
        add('CONSIGNMENT_REPORT',r['id'],'delivery','MEDIUM',msg('寄售报售待核对','Venda consignada aguardando conferência','Consignment sale report awaiting review'),
            msg(f'{d["name"]} · {w["name"] if w else r["warehouse_id"]}，有寄售报售记录待处理。',f'{d["name"]}: registro de venda consignada pendente.',f'{d["name"]}: a consignment sale report is pending.'),
            msg('核对原寄售订单、车型和数量，按原流程确认；已按整单计应收的寄售不得重复计应收。','Confira pedido, modelo e quantidade; não duplique o recebível da consignação.', 'Review source order, model, and quantity without duplicating the full consignment receivable.'),
            msg('仓管 / 管理员','Estoque / administrador','Warehouse / administrator'),quantity=r['quantity'],actions=[action('consignment',r['dealer_id'],warehouse_id=r['warehouse_id'])],report_id=r['id'],dealer_id=r['dealer_id'])
    for p in data['policies']:
        if not p['active'] or not p.get('ends'):continue
        remaining=(datetime.fromisoformat(p['ends']).date()-day).days
        if not 0<=remaining<=rules['policy_days']:continue
        add('POLICY_ENDING',p['id'],'policy','LOW',msg('销售政策即将到期','Política de vendas próxima do término','Sales policy expires soon'),
            msg(f'{p["name"]}，结束日期 {p["ends"]}。',f'{p["name"]}: término {p["ends"]}.',f'{p["name"]}: ends {p["ends"]}.'),
            msg('核对下月政策和待处理报价，管理员确认后再发布新政策；历史订单保留原规则。','Prepare a próxima política e revise cotações; pedidos históricos mantêm suas regras.','Review upcoming policies and pending quotes; historical orders retain their existing rules.'),
            msg('管理员','Administrador','Administrator'),actions=[action('policies')],policy_id=p['id'],ends=p['ends'])
    alerts.sort(key=lambda a:({'HIGH':0,'MEDIUM':1,'LOW':2}[a['priority']],-a['days'],-(a['amount'] or 0),a['key']))
    counts=Counter(a['priority'] for a in alerts);categories=Counter(a['category'] for a in alerts)
    return dict(version=VERSION,checked_at=at.isoformat(timespec='seconds'),as_of=day.isoformat(),rules=rules,alerts=alerts,
                summary=dict(total=len(alerts),high=counts['HIGH'],medium=counts['MEDIUM'],low=counts['LOW'],
                             categories=dict(categories),overdue_amount=sum(a['amount'] for a in alerts if a['code']=='OVERDUE'),
                             shortage_units=sum(a['quantity'] for a in alerts if a['code']=='STOCK_SHORTAGE'),
                             recharge_claimed=sum(a['amount'] for a in alerts if a['code']=='RECHARGE_REVIEW')))


def summary(db,actor,options=None,cohort=None):
    roles(actor,'admin');data=snapshot(db,actor)
    lines={line['id']:line for o in data['orders'] for line in o['lines']}
    rows=[dict(order_id=r.order_id,warehouse_id=r.warehouse_id,product_id=lines[r.line_id]['product_id'],
               quantity=r.quantity,expires=r.expires,active=r.active) for r in db.scalars(select(Reservation).where(Reservation.active==True)).all() if r.line_id in lines]
    return analyze(data,rows,options=options,order_ids={o['id'] for o in cohort['orders']} if cohort is not None else None,
                   dealer_ids={d['id'] for d in cohort['dealers']} if cohort is not None else None)
