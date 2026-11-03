import secrets
from sqlalchemy import select
from .core import *
from . import policies

CASH_PENDING={'DRAFT','WAITING_INTERNAL_REVIEW','WAITING_DEALER_CONFIRMATION','WAITING_FINANCE_REVIEW','EXPIRED'}

def cash_pending(o):return o.sale_type=='CASH' and not o.admin_review_pending and not o.confirmed and o.status in CASH_PENDING

def build_lines(db,o,items,promotion,actor,reason='',demo_ids=()):
    require(promotion in ['STANDARD','DEMO','TEN_PLUS_ONE'],'促销不可叠加 / Promoção inválida')
    require(isinstance(items,list) and 1<=len(items)<=100,'至少选择一项商品')
    seen=set();total=0
    for item in items:
        p=get(db,Product,item['product_id']);require(p.active,'商品已停售')
        require(p.id not in seen,'相同SKU请合并数量');seen.add(p.id)
        qty=integer(item['quantity'])
        demo=promotion=='DEMO' or p.id in demo_ids
        require(not demo or promotion!='TEN_PLUS_ONE','Demo八折不能与买10送1叠加')
        price=(p.price*80+50)//100 if demo else p.price
        if item.get('unit_price') not in [None,'']:
            roles(actor,'admin','sales')
            if actor.role=='admin':require(reason.strip(),'人工改价必须填写原因')
            price=money(item['unit_price'])
        db.add(OrderLine(order_id=o.id,product_id=p.id,quantity=qty,product_sku=p.sku,product_name=p.name,standard_price=p.price,unit_price=price,gift=False,demo_applied=demo))
        total+=qty*price
        if promotion=='TEN_PLUS_ONE' and qty>=10:
            db.add(OrderLine(order_id=o.id,product_id=p.id,quantity=qty//10,product_sku=p.sku,product_name=p.name,standard_price=p.price,unit_price=0,gift=True))
    o.total=total+o.freight
    db.flush()
def sales_fields(d, existing=None):
    kind = d.get('sale_type', existing.sale_type if existing else 'WHOLESALE')
    require(kind in ['WHOLESALE', 'CONSIGNMENT', 'CASH', 'OTHER'], '请选择有效销售类型 / Tipo de venda inválido')
    if existing is None and kind in ['CONSIGNMENT','CASH','OTHER']:
        require(d.get('sale_date'), '请填写销售日期 / Informe a data da venda')
    if 'sale_date' in d:require(d['sale_date'],'请填写销售日期')
    sale_date = date_value(d.get('sale_date') or (existing.sale_date if existing else None) or today().isoformat())
    method=d.get('payment_method',existing.payment_method if existing else 'BANK')
    require(method in ['BANK','BALANCE'],'请选择银行转账或账户余额付款')
    require(kind=='CASH' or method=='BANK','账户余额付款选项仅用于现付订单')
    if kind=='CASH' and method=='BALANCE':
        paid_at=None;due=sale_date
    elif kind == 'CASH':
        if 'payment_at' in d:require(d['payment_at'],'请填写付款时间')
        paid_at = payment_time(d.get('payment_at') or (existing.payment_at if existing else None))
        due = paid_at[:10]
    else:
        paid_at = None
        if 'due' in d:require(d['due'],'请填写经销商付款到期日')
        due = date_value(d.get('due') or (existing.due if existing else None))
    return dict(sale_type=kind, sale_date=sale_date, payment_at=paid_at, payment_method=method, due=due)

def sale_type_access(actor, kind):
    if actor.role=='dealer':
        require(kind=='CASH','经销商只能创建现付订单，须填写付款时间并上传水单 / Revendedor: somente pagamento à vista',403)
    if actor.role=='sales':
        require(kind in ['CASH','CONSIGNMENT','OTHER'],'销售只能选择现付、寄售或其他',403)
    if kind=='CONSIGNMENT':
        require(actor.role in ['admin','sales'],'寄售订单只能由管理员或销售创建',403)

def purchase_options_access(actor, d):
    if actor.role=='sales':
        require((d.get('sale_type')=='OTHER' and d.get('policy_id')) or (not d.get('policy_id') and d.get('promotion','STANDARD')=='STANDARD'),'销售只能在其他类型勾选管理员发布的方案',403)
    if actor.role=='dealer':
        require(not d.get('policy_id') and d.get('promotion','STANDARD')=='STANDARD','经销商不能自行选择促销政策',403)
        require(not d.get('special_7030',False),'付款条款只能由管理员设置 / Condições definidas pelo administrador',403)

def create_order(db,actor,d,*,cash_proof=False):
    roles(actor,'admin','sales','dealer')
    require(not d.get('payments'),'多笔付款记录请通过订单表单同时上传对应水单')
    sale_type_access(actor,d.get('sale_type','WHOLESALE'))
    purchase_options_access(actor,d)
    did=actor.dealer_id if actor.role=='dealer' else integer(d['dealer_id'])
    dealer=dealer_access(db,actor,did)
    require(dealer.sales_id is not None,'请先指定责任销售员')
    fields = sales_fields(d)
    promotion,policy=policies.resolve_policy(db,actor,d)
    require(promotion!='DEMO' or fields['sale_type']=='WHOLESALE','请先按正常价格建立订单，再由销售或管理员在订单详情中勾选Demo',409)
    require(not any(i.get('demo_applied') for i in d['items']),'请先正常下单，再设置Demo计划',403)
    require(fields['sale_type']!='OTHER' or str(d.get('terms','')).strip(),'其他类型订单必须填写备注说明')
    require(fields['sale_type']!='CASH' or fields['payment_method']=='BALANCE' or cash_proof, '现付订单必须同时上传付款水单 / Comprovante obrigatório')
    o=Order(dealer_id=did,sales_id=dealer.sales_id,creator_id=actor.id,mode='SELF' if actor.role=='dealer' else 'ASSISTED',status='DRAFT',**fields,promotion=promotion,policy_id=policy.id if policy else None,policy_name=policy.name if policy else None,terms=str(d.get('terms','')),special_7030=bool(d.get('special_7030',False)),freight=0 if actor.role!='admin' else money(d.get('freight','0')))
    db.add(o);db.flush()
    build_lines(db,o,d['items'],o.promotion,actor,d.get('reason',''))
    policies.check_demo(db,o)
    custom=actor.role=='sales' and policies.price_exception(db,o)
    o.admin_review_pending=custom or (actor.role=='sales' and fields['sale_type']=='CONSIGNMENT')
    o.status='WAITING_INTERNAL_REVIEW' if o.admin_review_pending or fields['sale_type']=='OTHER' else 'WAITING_FINANCE_REVIEW' if fields['sale_type']=='CASH' else 'DRAFT'
    if fields['sale_type']=='OTHER' and not o.admin_review_pending:
        require(o.total>0,'订单金额须大于零')
        activate(db,o)
        audit(db,actor,'POLICY_ORDER_ACTIVATE',o.id,f'{o.policy_name or "STANDARD"}; compliant pricing')
    if fields['payment_method']=='BALANCE':
        dealer.auto_apply_credit=True
        from .balances import match_credit
        match_credit(db,did,actor)
    audit(db,actor,'ORDER_CREATE',o.id,f'{o.mode}; version={o.version}; cents={o.total}; sale_date={o.sale_date}; sale_type={o.sale_type}; payment_at={o.payment_at}; due={o.due}')
    return {'id':o.id}
def activate(db,o,*,finance_verified=False,auto_credit=True):
    require(not o.admin_review_pending,'订单须先通过管理员审核',403)
    if o.sale_type=='CASH':require(finance_verified and cash_pending(o),'现付订单须由财务核实足额到账后生效',403)
    else:require(o.status in ['WAITING_INTERNAL_REVIEW','WAITING_DEALER_CONFIRMATION'],'订单不能生效',409)
    policies.validate_activation(db,o)
    o.status='CONFIRMED';o.confirmed=now()
    db.add(Receivable(order_id=o.id,amount=o.total,due=o.due,source=o.source))
    db.flush();reserve_order(db,o)
    if auto_credit and o.sale_type!='CASH':
        from .balances import match_credit
        match_credit(db,o.dealer_id)
def submit_order(db,actor,d):
    o=order_access(db,actor,d['order_id']);roles(actor,'admin','sales')
    if o.admin_review_pending:
        require(o.status=='WAITING_INTERNAL_REVIEW','订单不在管理员审核中',409)
        return {'id':o.id,'status':o.status}
    if cash_pending(o):
        o.status='WAITING_FINANCE_REVIEW'
        return {'id':o.id,'status':o.status}
    require(o.status=='DRAFT','仅草稿可提交',409)
    o.status='WAITING_INTERNAL_REVIEW';audit(db,actor,'ORDER_SUBMIT',o.id,o.version)
    return {'id':o.id}
def review_order(db,actor,d):
    roles(actor,'admin');o=order_access(db,actor,d['order_id'])
    if o.admin_review_pending:return review_sales_order(db,actor,o,d)
    require(o.sale_type!='CASH','现付订单请交由财务核实银行到账，不能通过价格审核生效',403)
    require(o.status=='WAITING_INTERNAL_REVIEW','订单不在审核中',409)
    if d.get('reject'):
        require(str(d.get('reason','')).strip(),'拒绝请说明原因');o.status='REJECTED'
    else:
        o.due=o.payment_at[:10] if o.sale_type=='CASH' else date_value(d.get('due',o.due));o.terms=str(d.get('terms',o.terms))
        o.sales_id=integer(d.get('sales_id') or o.sales_id)
        require(get(db,User,o.sales_id).role=='sales','责任人必须是销售人员')
        o.partial_allowed=bool(d.get('partial_allowed',False))
        if d.get('commission_bps') not in [None,'']:
            require(str(d.get('reason','')).strip(),'佣金例外需要原因')
            o.commission_bps=integer(d['commission_bps'],0,10000)
        if o.mode=='SELF': activate(db,o)
        else:o.status='WAITING_DEALER_CONFIRMATION'
    audit(db,actor,'ORDER_REVIEW',o.id,f'{o.status}; v{o.version}; {d.get("reason","")}')
    return {'id':o.id,'status':o.status}

def review_sales_order(db,actor,o,d):
    require(o.status=='WAITING_INTERNAL_REVIEW' and not o.confirmed,'订单不在管理员审核中',409)
    reason=str(d.get('reason','')).strip()
    if d.get('reject'):
        require(reason,'拒绝订单须填写审核原因');o.status='REJECTED'
    else:
        o.terms=str(d.get('terms',o.terms));o.due=date_value(d.get('due',o.due))
        require(o.sale_date and o.due,'销售日期和付款到期日不完整')
        date_value(o.sale_date);date_value(o.due)
        require(o.sale_type!='OTHER' or o.terms.strip(),'其他类型订单必须填写备注说明')
        require(o.total>0 and db.scalar(select(OrderLine.id).where(OrderLine.order_id==o.id).limit(1)),'订单商品或金额不完整')
        o.partial_allowed=bool(d.get('partial_allowed',False))
        o.admin_review_pending=False
        if o.sale_type=='CASH':
            o.status='WAITING_FINANCE_REVIEW'
            if o.payment_method=='BALANCE':
                from .balances import match_credit
                match_credit(db,o.dealer_id,actor)
        else:activate(db,o)
    audit(db,actor,'ADMIN_ORDER_REVIEW',o.id,f'{o.status}; v{o.version}; {reason}')
    return {'id':o.id,'status':o.status}
def revise_order(db,actor,d,*,cash_proof=False):
    roles(actor,'admin');o=order_access(db,actor,d['order_id'])
    require(not d.get('payments'),'多笔付款记录请通过订单表单同时上传对应水单')
    require(o.status in CASH_PENDING,'生效订单只能人工调整，禁止改价',409)
    require(not db.scalar(select(Receipt.id).join(Claim,Receipt.claim_id==Claim.id).where(Claim.order_id==o.id).limit(1)),'已核实到账的订单须先由财务人工复核，不能直接修订',409)
    reason=str(d.get('reason','')).strip();require(reason,'改价/改条件必须填写原因')
    fields = sales_fields(d, o)
    promotion,policy=policies.resolve_policy(db,actor,d,o)
    demo_ids=policies.demo_product_ids(db,o)
    admin_pending=o.admin_review_pending
    if admin_pending:require(fields['sale_type']==o.sale_type,'待管理员审核订单不能通过切换类型绕过审核',403)
    require(fields['sale_type']!='OTHER' or str(d.get('terms',o.terms)).strip(),'其他类型订单必须填写备注说明')
    if o.sale_type=='CASH':require(fields['sale_type']=='CASH','现付订单不能改成其他类型绕过财务审核',403)
    if fields['sale_type']=='CASH' and fields['payment_method']=='BANK' and not cash_proof:
        claims = db.scalars(select(Claim).where(Claim.order_id==o.id, Claim.status.in_(['PENDING','VERIFIED']))).all()
        require(o.sale_type=='CASH' and fields['payment_at']==o.payment_at and any(c.paid_at==fields['payment_at'] for c in claims), '现付付款时间变更或缺少水单，请重新上传 / Envie o comprovante')
    for token in db.scalars(select(ConfirmationToken).where(ConfirmationToken.order_id==o.id)).all():token.revoked=True
    for line in db.scalars(select(OrderLine).where(OrderLine.order_id==o.id)).all():db.delete(line)
    db.flush();o.version+=1;o.status='WAITING_INTERNAL_REVIEW'
    for key, value in fields.items():setattr(o,key,value)
    o.admin_review_pending=admin_pending or o.sale_type=='OTHER'
    if o.sale_type=='CASH' and not o.admin_review_pending:o.status='WAITING_FINANCE_REVIEW'
    o.promotion=promotion;o.policy_id=policy.id if policy else None;o.policy_name=policy.name if policy else None
    o.terms=str(d.get('terms',o.terms));o.freight=money(d.get('freight','0'))
    o.special_7030=bool(d.get('special_7030',o.special_7030))
    build_lines(db,o,d['items'],o.promotion,actor,reason,demo_ids=demo_ids)
    policies.check_demo(db,o,include_pending=True)
    if o.payment_method=='BALANCE':
        get(db,Dealer,o.dealer_id).auto_apply_credit=True
        from .balances import match_credit
        match_credit(db,o.dealer_id,actor)
    audit(db,actor,'ORDER_REVISE',o.id,f'v{o.version}; {reason}; sale_date={o.sale_date}; sale_type={o.sale_type}; payment_at={o.payment_at}; due={o.due}')
    return {'id':o.id}
def set_demo(db,actor,d):
    """Select Demo per model before activation, under the shared write lock."""
    roles(actor,'admin','sales');o=order_access(db,actor,d['order_id'])
    require(not o.confirmed and o.status in CASH_PENDING and o.source=='CURRENT','仅未生效订单可设置Demo；已生效订单不能直接改价',409)
    require(o.sale_type!='CONSIGNMENT','寄售发货不适用采购Demo计划',409)
    require(integer(d['version'])==o.version,'订单已更新，请刷新后重新勾选Demo',409)
    require(not db.scalar(select(Receipt.id).join(Claim,Receipt.claim_id==Claim.id).where(Claim.order_id==o.id).limit(1)),'已有银行核实到账，请先由财务复核，不能直接修改Demo价格',409)
    raw=d.get('product_ids')
    require(isinstance(raw,list) and len(raw)<=100,'请选择有效的Demo车型')
    ids={integer(pid) for pid in raw}
    require(len(ids)==len(raw),'Demo车型不能重复')
    lines=db.scalars(select(OrderLine).where(OrderLine.order_id==o.id,OrderLine.gift==False)).all()
    require(bool(lines) and ids<={l.product_id for l in lines},'只能勾选此订单内的车型')
    require(not ids or (o.promotion!='TEN_PLUS_ONE' and not db.scalar(select(OrderLine.id).where(OrderLine.order_id==o.id,OrderLine.gift==True).limit(1))),'Demo八折不能与买10送1叠加，请先由管理员修订订单方案',409)
    policies.check_demo(db,o,ids,include_pending=True)
    previous=policies.demo_product_ids(db,o)
    if previous==ids:return {'id':o.id,'version':o.version,'total':o.total}
    changes=[]
    for l in lines:
        if l.product_id in previous or l.product_id in ids:
            old=l.unit_price;l.demo_applied=l.product_id in ids
            l.unit_price=(l.standard_price*80+50)//100 if l.demo_applied else l.standard_price
            changes.append(f'{l.product_sku}: {old}->{l.unit_price}; Demo={l.demo_applied}')
    if o.promotion=='DEMO':o.promotion='STANDARD'
    if o.policy_id and get(db,SalesPolicy,o.policy_id).kind=='DEMO':o.policy_id=None;o.policy_name=None
    db.flush()
    o.total=sum(l.quantity*l.unit_price for l in lines)+o.freight
    require(o.total>0,'订单金额须大于零')
    o.version+=1
    for token in db.scalars(select(ConfirmationToken).where(ConfirmationToken.order_id==o.id)).all():token.revoked=True
    # A defined Demo discount needs no extra approval. Unrelated custom prices
    # still need administrator review; cash always needs bank verification.
    exception=policies.price_exception(db,o)
    o.admin_review_pending=exception and (actor.role=='sales' or o.admin_review_pending)
    o.status='WAITING_INTERNAL_REVIEW' if o.admin_review_pending or o.sale_type!='CASH' else 'WAITING_FINANCE_REVIEW'
    if o.sale_type=='OTHER' and not o.admin_review_pending:activate(db,o)
    if o.sale_type=='CASH' and o.payment_method=='BALANCE':
        from .balances import match_credit
        match_credit(db,o.dealer_id,actor)
    audit(db,actor,'ORDER_DEMO_SET',o.id,f'v{o.version}; '+ '; '.join(changes))
    return {'id':o.id,'version':o.version,'total':o.total}
def issue_token(db,actor,d):
    roles(actor,'admin','sales');o=order_access(db,actor,d['order_id'])
    require(o.sale_type!='CASH','现付订单由财务核实到账后生效，无需确认链接',403)
    require(o.mode=='ASSISTED' and o.status=='WAITING_DEALER_CONFIRMATION','须先内部审核报价',409)
    for t in db.scalars(select(ConfirmationToken).where(ConfirmationToken.order_id==o.id)).all():t.revoked=True
    raw=secrets.token_urlsafe(32)
    db.add(ConfirmationToken(hash=digest(raw),order_id=o.id,version=o.version,expires=future(integer(d.get('days',7),1,30)*24)))
    audit(db,actor,'TOKEN_ISSUE',o.id,f'v{o.version}')
    return {'token':raw,'path':'/confirm/'+raw}
def revoke_token(db,actor,d):
    roles(actor,'admin','sales');o=order_access(db,actor,d['order_id'])
    for t in db.scalars(select(ConfirmationToken).where(ConfirmationToken.order_id==o.id)).all():t.revoked=True
    audit(db,actor,'TOKEN_REVOKE',o.id,'revoked')
    return {'ok':True}
def token_order(db,raw):
    t=db.get(ConfirmationToken,digest(raw));require(t is not None,'链接无效 / Link inválido',404)
    require(not t.revoked and t.expires>=now(),'链接已撤销或过期 / Link expirado',410)
    o=get(db,Order,t.order_id)
    require(t.version==o.version,'报价版本已变更 / Cotação alterada',410)
    return t,o
def confirm_order(db,raw,action,ip='',device=''):
    require(action in ['CONFIRM','REJECT'],'请选择确认或拒绝')
    t,o=token_order(db,raw)
    if t.action:
        require(t.action==action,'订单已处理 / Pedido já processado',409)
        return {'id':o.id,'status':o.status,'repeated':True}
    require(o.status=='WAITING_DEALER_CONFIRMATION','订单已处理',409)
    if action=='CONFIRM':activate(db,o)
    else:o.status='REJECTED'
    t.action=action;t.acted_at=now();t.ip=ip[:100];t.device=device[:300]
    audit(db,None,'DEALER_'+action,o.id,f'v{t.version}; {t.acted_at}')
    return {'id':o.id,'status':o.status}
def reallocate(db,actor,d):
    roles(actor,'admin');o=order_access(db,actor,d['order_id'])
    require(o.status in ['CONFIRMED','PARTIALLY_SHIPPED'],'订单状态不允许锁货')
    for r in db.scalars(select(Reservation).where(Reservation.order_id==o.id,Reservation.active==True)).all():r.active=False
    db.flush();reserve_order(db,o);audit(db,actor,'STOCK_RERESERVE',o.id,d.get('reason','manual allocation'))
    return {'ok':True}

def cancel_order(db,actor,d):
    roles(actor,'admin');o=order_access(db,actor,d['order_id']);reason=str(d.get('reason','')).strip();require(reason,'撤销必须说明原因')
    require(o.status not in ['CANCELED','REJECTED'],'订单已终止',409)
    require(allocated(db,o.id)==0,'有已分配实收，须先人工处理退款，不能直接撤销')
    if o.sale_type=='CASH':require(not db.scalar(select(Receipt.id).join(Claim,Receipt.claim_id==Claim.id).where(Claim.order_id==o.id).limit(1)),'已有银行核实到账，须先由财务人工处理退款，不能直接撤销',409)
    require(not db.scalar(select(Shipment).where(Shipment.order_id==o.id,Shipment.status.in_(['PENDING_PICKUP','DELIVERED']))),'已有出库，请先撤销未提货或实际退回')
    require(not db.scalar(select(Commission).where(Commission.order_id==o.id)),'佣金已审核，须人工复核')
    amount=adjusted_amount(db,o.id)
    if amount:db.add(Adjustment(order_id=o.id,amount=-amount,reason='CANCEL: '+reason,actor_id=actor.id))
    for r in db.scalars(select(Reservation).where(Reservation.order_id==o.id,Reservation.active==True)).all():r.active=False
    for t in db.scalars(select(ConfirmationToken).where(ConfirmationToken.order_id==o.id)).all():t.revoked=True
    o.status='CANCELED';audit(db,actor,'ORDER_CANCEL',o.id,reason)
    return {'id':o.id}
