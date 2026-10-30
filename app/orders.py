import secrets
from sqlalchemy import select
from .core import *

def build_lines(db,o,items,promotion,actor,reason=''):
    require(promotion in ['STANDARD','DEMO','TEN_PLUS_ONE'],'促销不可叠加 / Promoção inválida')
    require(isinstance(items,list) and 1<=len(items)<=100,'至少选择一项商品')
    seen=set();total=0
    for item in items:
        p=get(db,Product,item['product_id']);require(p.active,'商品已停售')
        require(p.id not in seen,'相同SKU请合并数量');seen.add(p.id)
        qty=integer(item['quantity'])
        price=(p.price*80+50)//100 if promotion=='DEMO' else p.price
        if item.get('unit_price') not in [None,'']:
            roles(actor,'admin');require(reason.strip(),'人工改价必须填写原因')
            price=money(item['unit_price'])
        db.add(OrderLine(order_id=o.id,product_id=p.id,quantity=qty,product_sku=p.sku,product_name=p.name,standard_price=p.price,unit_price=price,gift=False))
        total+=qty*price
        if promotion=='TEN_PLUS_ONE' and qty>=10:
            db.add(OrderLine(order_id=o.id,product_id=p.id,quantity=qty//10,product_sku=p.sku,product_name=p.name,standard_price=p.price,unit_price=0,gift=True))
    o.total=total+o.freight
    db.flush()
def sales_fields(d, existing=None):
    kind = d.get('sale_type', existing.sale_type if existing else 'WHOLESALE')
    require(kind in ['WHOLESALE', 'CONSIGNMENT', 'CASH'], '请选择寄售或现付 / Tipo de venda inválido')
    if existing is None and kind in ['CONSIGNMENT','CASH']:
        require(d.get('sale_date'), '请填写销售日期 / Informe a data da venda')
    if 'sale_date' in d:require(d['sale_date'],'请填写销售日期')
    sale_date = date_value(d.get('sale_date') or (existing.sale_date if existing else None) or today().isoformat())
    if kind == 'CASH':
        if 'payment_at' in d:require(d['payment_at'],'请填写付款时间')
        paid_at = payment_time(d.get('payment_at') or (existing.payment_at if existing else None))
        due = paid_at[:10]
    else:
        paid_at = None
        if 'due' in d:require(d['due'],'请填写经销商付款到期日')
        due = date_value(d.get('due') or (existing.due if existing else None))
    return dict(sale_type=kind, sale_date=sale_date, payment_at=paid_at, due=due)

def sale_type_access(actor, kind):
    if actor.role=='dealer':
        require(kind=='CASH','经销商只能创建现付订单，须填写付款时间并上传水单 / Revendedor: somente pagamento à vista',403)
    if kind=='CONSIGNMENT':
        require(actor.role=='admin','寄售订单只能由管理员创建 / Somente administrador pode criar consignação',403)

def create_order(db,actor,d,*,cash_proof=False):
    roles(actor,'admin','sales','dealer')
    sale_type_access(actor,d.get('sale_type','WHOLESALE'))
    did=actor.dealer_id if actor.role=='dealer' else integer(d['dealer_id'])
    dealer=dealer_access(db,actor,did)
    require(dealer.sales_id is not None,'请先指定责任销售员')
    fields = sales_fields(d)
    require(fields['sale_type']!='CASH' or cash_proof, '现付订单必须同时上传付款水单 / Comprovante obrigatório')
    o=Order(dealer_id=did,sales_id=dealer.sales_id,creator_id=actor.id,mode='SELF' if actor.role=='dealer' else 'ASSISTED',status='WAITING_INTERNAL_REVIEW' if actor.role=='dealer' else 'DRAFT',**fields,promotion=d.get('promotion','STANDARD'),terms=str(d.get('terms','')),special_7030=bool(d.get('special_7030',False)),freight=0 if actor.role!='admin' else money(d.get('freight','0')))
    db.add(o);db.flush()
    build_lines(db,o,d['items'],o.promotion,actor,d.get('reason',''))
    audit(db,actor,'ORDER_CREATE',o.id,f'{o.mode}; version={o.version}; cents={o.total}; sale_date={o.sale_date}; sale_type={o.sale_type}; payment_at={o.payment_at}; due={o.due}')
    return {'id':o.id}
def activate(db,o):
    require(o.status in ['WAITING_INTERNAL_REVIEW','WAITING_DEALER_CONFIRMATION'],'订单不能生效',409)
    o.status='CONFIRMED';o.confirmed=now()
    if o.sale_type!='CONSIGNMENT':
        db.add(Receivable(order_id=o.id,amount=o.total,due=o.due,source=o.source))
    db.flush();reserve_order(db,o)
def submit_order(db,actor,d):
    o=order_access(db,actor,d['order_id']);roles(actor,'admin','sales')
    require(o.status=='DRAFT','仅草稿可提交',409)
    o.status='WAITING_INTERNAL_REVIEW';audit(db,actor,'ORDER_SUBMIT',o.id,o.version)
    return {'id':o.id}
def review_order(db,actor,d):
    roles(actor,'admin');o=order_access(db,actor,d['order_id'])
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
def revise_order(db,actor,d,*,cash_proof=False):
    roles(actor,'admin');o=order_access(db,actor,d['order_id'])
    require(o.status in ['DRAFT','WAITING_INTERNAL_REVIEW','WAITING_DEALER_CONFIRMATION','EXPIRED'],'生效订单只能人工调整，禁止改价',409)
    reason=str(d.get('reason','')).strip();require(reason,'改价/改条件必须填写原因')
    fields = sales_fields(d, o)
    if fields['sale_type']=='CASH' and not cash_proof:
        claims = db.scalars(select(Claim).where(Claim.order_id==o.id, Claim.status.in_(['PENDING','VERIFIED']))).all()
        require(o.sale_type=='CASH' and fields['payment_at']==o.payment_at and any(c.paid_at==fields['payment_at'] for c in claims), '现付付款时间变更或缺少水单，请重新上传 / Envie o comprovante')
    for token in db.scalars(select(ConfirmationToken).where(ConfirmationToken.order_id==o.id)).all():token.revoked=True
    for line in db.scalars(select(OrderLine).where(OrderLine.order_id==o.id)).all():db.delete(line)
    db.flush();o.version+=1;o.status='WAITING_INTERNAL_REVIEW'
    for key, value in fields.items():setattr(o,key,value)
    o.promotion=d.get('promotion',o.promotion);o.terms=str(d.get('terms',o.terms));o.freight=money(d.get('freight','0'))
    o.special_7030=bool(d.get('special_7030',o.special_7030))
    build_lines(db,o,d['items'],o.promotion,actor,reason)
    audit(db,actor,'ORDER_REVISE',o.id,f'v{o.version}; {reason}; sale_date={o.sale_date}; sale_type={o.sale_type}; payment_at={o.payment_at}; due={o.due}')
    return {'id':o.id}
def issue_token(db,actor,d):
    roles(actor,'admin','sales');o=order_access(db,actor,d['order_id'])
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
    require(not db.scalar(select(Shipment).where(Shipment.order_id==o.id,Shipment.status.in_(['PENDING_PICKUP','DELIVERED']))),'已有出库，请先撤销未提货或实际退回')
    require(not db.scalar(select(Commission).where(Commission.order_id==o.id)),'佣金已审核，须人工复核')
    amount=adjusted_amount(db,o.id)
    if amount:db.add(Adjustment(order_id=o.id,amount=-amount,reason='CANCEL: '+reason,actor_id=actor.id))
    for r in db.scalars(select(Reservation).where(Reservation.order_id==o.id,Reservation.active==True)).all():r.active=False
    for t in db.scalars(select(ConfirmationToken).where(ConfirmationToken.order_id==o.id)).all():t.revoked=True
    o.status='CANCELED';audit(db,actor,'ORDER_CANCEL',o.id,reason)
    return {'id':o.id}
