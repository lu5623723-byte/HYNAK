import hashlib, hmac, secrets
from datetime import datetime, timezone, date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select, func
from .models import *

def fail(message, status=400):
    raise HTTPException(status, message)
def require(condition, message, status=400):
    if not condition: fail(message, status)
def get(db, cls, identity):
    try: identity=int(identity)
    except (TypeError,ValueError): fail('记录标识无效 / Identificador inválido')
    obj=db.get(cls, identity)
    require(obj is not None, '记录不存在 / Registro não encontrado',404)
    return obj
def integer(value, minimum=1, maximum=1000000):
    try:
        require(not isinstance(value,bool) and str(value).strip()==str(int(value)), '请输入整数 / Número inteiro necessário')
        n=int(value)
    except (ValueError,TypeError): fail('请输入整数 / Número inteiro necessário')
    require(minimum<=n<=maximum, '数量超出范围 / Quantidade inválida')
    return n
def money(value, allow_negative=False):
    try:
        require(not isinstance(value,(float,bool)), '金额请使用小数字符串 / Use valor decimal em texto')
        d=Decimal(str(value))
        require(d.is_finite() and abs(d)<=Decimal('999999999.99') and (d>=0 or allow_negative), '金额无效 / Valor inválido')
        require(d==d.quantize(Decimal('.01')), '金额最多两位小数 / Máximo duas casas decimais')
        return int(d*100)
    except (InvalidOperation,ValueError): fail('金额无效 / Valor inválido')
def today(): return datetime.now(ZoneInfo("America/Sao_Paulo")).date()
def date_value(value):
    try: return date.fromisoformat(str(value)).isoformat()
    except (ValueError,TypeError): fail('日期格式 YYYY-MM-DD / Data inválida')
def payment_time(value):
    try:
        require(isinstance(value, str) and 'T' in value, '请填写付款时间 / Informe a data e hora do pagamento')
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo('America/Sao_Paulo'))
        return parsed.astimezone(ZoneInfo('America/Sao_Paulo')).isoformat(timespec='seconds')
    except (ValueError, TypeError):
        fail('付款时间格式无效 / Data e hora inválidas')
def future(hours):
    return (datetime.now(timezone.utc)+timedelta(hours=hours)).isoformat(timespec='seconds')
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def hash_password(password):
    require(isinstance(password,str) and 10<=len(password)<=200,'密码至少10位 / Senha: mínimo 10 caracteres')
    salt=secrets.token_hex(16)
    return salt+':'+hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),310000).hex()
def check_password(password, encoded):
    salt, expected=encoded.split(':')
    return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),310000).hex(),expected)
def audit(db, actor, action, entity, detail):
    db.add(Audit(actor_id=actor.id if actor else None,action=action,entity=str(entity),detail=str(detail)))
def roles(actor,*allowed): require(actor.role in allowed,'无权限 / Sem permissão',403)
def dealer_access(db, actor, dealer_id):
    d=get(db,Dealer,dealer_id)
    if actor.role=='dealer': require(actor.dealer_id==d.id,'无权限 / Sem permissão',403)
    elif actor.role=='sales': require(d.sales_id==actor.id,'无权限 / Sem permissão',403)
    return d
def order_access(db,actor,order_id):
    o=get(db,Order,order_id)
    dealer_access(db,actor,o.dealer_id)
    return o
def total_sum(db, column, *conditions): return db.scalar(select(func.coalesce(func.sum(column),0)).where(*conditions)) or 0
def allocated(db, oid): return total_sum(db,Allocation.amount,Allocation.order_id==oid)
def outstanding(db, oid):
    r=db.scalar(select(Receivable).where(Receivable.order_id==oid))
    if not r:return 0
    return max(0,r.amount+total_sum(db,Adjustment.amount,Adjustment.order_id==oid)-allocated(db,oid))
def adjusted_amount(db,oid):
    r=db.scalar(select(Receivable).where(Receivable.order_id==oid))
    return (r.amount if r else 0)+total_sum(db,Adjustment.amount,Adjustment.order_id==oid)
def balance(db,wid,pid): return total_sum(db,StockLedger.delta,StockLedger.warehouse_id==wid,StockLedger.product_id==pid)
def reserved(db,wid,pid,exclude_order=None):
    q=select(func.coalesce(func.sum(Reservation.quantity),0)).join(OrderLine,OrderLine.id==Reservation.line_id).where(Reservation.warehouse_id==wid,Reservation.active==True,OrderLine.product_id==pid)
    if exclude_order: q=q.where(Reservation.order_id!=exclude_order)
    return db.scalar(q) or 0
def available(db,wid,pid,exclude_order=None):return balance(db,wid,pid)-reserved(db,wid,pid,exclude_order)
def credit(db,did):
    return total_sum(db,OpeningCredit.amount,OpeningCredit.dealer_id==did)+total_sum(db,Receipt.amount,Receipt.dealer_id==did)-total_sum(db,Allocation.amount,Allocation.dealer_id==did)
def stock_entry(db,actor,wid,pid,delta,kind,reason,order_id=None,batch=None,allow_negative=False):
    w=get(db,Warehouse,wid);get(db,Product,pid)
    require(delta!=0,'数量不能为零')
    if delta<0 and balance(db,wid,pid)+delta<0:
        require(actor.role=='admin' and allow_negative and reason.strip(),'库存不足；负库存仅管理员填写原因后允许 / Estoque insuficiente')
    db.add(StockLedger(warehouse_id=wid,product_id=pid,delta=delta,kind=kind,order_id=order_id,actor_id=actor.id,reason=reason,batch=batch))
    db.flush()
def expire_reservations(db):
    for r in db.scalars(select(Reservation).where(Reservation.active==True,Reservation.expires<now())).all():
        if allocated(db,r.order_id)==0:
            r.active=False
            audit(db,None,'RESERVATION_EXPIRED',r.order_id,'72h without verified allocated receipt')
    for t in db.scalars(select(ConfirmationToken).where(ConfirmationToken.revoked==False,ConfirmationToken.action==None,ConfirmationToken.expires<now())).all():
        t.revoked=True
        o=get(db,Order,t.order_id)
        if o.version==t.version and o.status=='WAITING_DEALER_CONFIRMATION': o.status='EXPIRED'
    db.flush()
def reserve_order(db,o):
    warehouses=db.scalars(select(Warehouse).where(Warehouse.kind!='CONSIGNMENT').order_by(Warehouse.id)).all()
    for line in db.scalars(select(OrderLine).where(OrderLine.order_id==o.id)).all():
        remaining=line.quantity
        shipped=db.scalar(select(func.coalesce(func.sum(ShipmentLine.quantity),0)).join(Shipment,Shipment.id==ShipmentLine.shipment_id).where(ShipmentLine.line_id==line.id,Shipment.status.in_(['PENDING_PICKUP','DELIVERED']))) or 0
        remaining-=shipped
        for w in warehouses:
            take=min(remaining,max(0,available(db,w.id,line.product_id)))
            if take>0:
                db.add(Reservation(order_id=o.id,line_id=line.id,warehouse_id=w.id,quantity=take,expires=future(72)))
                db.flush();remaining-=take
            if remaining<=0:break

def commission_eligible(db,o):
    if o.sale_type=='CONSIGNMENT' and o.mode!='CONSIGNMENT':return False
    if o.source!='CURRENT' or not o.sales_id or o.status not in ['SHIPPED','CLOSED'] or outstanding(db,o.id)>0:return False
    # Need actual verified allocations for full vehicle sales, not a writeoff.
    base=total_sum(db,OrderLine.quantity*OrderLine.unit_price,OrderLine.order_id==o.id,OrderLine.gift==False)
    if allocated(db,o.id)<base:return False
    for line in db.scalars(select(OrderLine).where(OrderLine.order_id==o.id)).all():
        delivered=db.scalar(select(func.coalesce(func.sum(ShipmentLine.quantity),0)).join(Shipment,Shipment.id==ShipmentLine.shipment_id).where(ShipmentLine.line_id==line.id,Shipment.status=='DELIVERED')) or 0
        if delivered<line.quantity:return False
    return True
