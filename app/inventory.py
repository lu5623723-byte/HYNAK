from sqlalchemy import select, func
from .core import *
from .orders import activate

def stock_move(db,actor,d):
    roles(actor,'admin','warehouse')
    wid=integer(d['warehouse_id']);pid=integer(d['product_id']);qty=integer(d['quantity']);kind=d['kind'];reason=str(d.get('reason','')).strip()
    require(reason,'必须填写原因 / Motivo obrigatório')
    w=get(db,Warehouse,wid)
    if kind in ['OPENING','RECEIVE','ADJUST']:
        if kind in ['OPENING','ADJUST']:roles(actor,'admin')
        require(w.kind!='CONSIGNMENT','寄售位置请通过调拨发出或退回')
        delta=-qty if d.get('negative') else qty
        require(delta>0 or kind=='ADJUST','入库数量须为正')
        if delta<0:require(available(db,wid,pid)>=qty or actor.role=='admin' and d.get('allow_negative'),'库存被其他订单锁定')
        stock_entry(db,actor,wid,pid,delta,kind,reason,allow_negative=bool(d.get('allow_negative')))
    elif kind in ['TRANSFER','CONSIGN_SEND','CONSIGN_RETURN']:
        target=get(db,Warehouse,d['target_id']);require(target.id!=wid,'不能调拨到同一仓库')
        if kind=='TRANSFER':require(w.kind!='CONSIGNMENT' and target.kind!='CONSIGNMENT','寄售须选寄售发出/退回')
        if kind=='CONSIGN_SEND':require(w.kind!='CONSIGNMENT' and target.kind=='CONSIGNMENT','寄售地点不正确')
        if kind=='CONSIGN_RETURN':require(w.kind=='CONSIGNMENT' and target.kind!='CONSIGNMENT','退回地点不正确')
        require(available(db,wid,pid)>=qty or actor.role=='admin' and d.get('allow_negative'),'可用库存不足 / Estoque indisponível')
        batch=secrets_token()
        stock_entry(db,actor,wid,pid,-qty,kind,reason,batch=batch,allow_negative=bool(d.get('allow_negative')))
        stock_entry(db,actor,target.id,pid,qty,kind,reason,batch=batch)
    else:fail('库存动作无效')
    audit(db,actor,'STOCK_'+kind,wid,f'{pid}: {qty}; {reason}')
    return {'ok':True}
def secrets_token():
    import secrets
    return secrets.token_hex(12)
def shipped_qty(db,line_id,delivered=False):
    states=['DELIVERED'] if delivered else ['PENDING_PICKUP','DELIVERED']
    return db.scalar(select(func.coalesce(func.sum(ShipmentLine.quantity),0)).join(Shipment,Shipment.id==ShipmentLine.shipment_id).where(ShipmentLine.line_id==line_id,Shipment.status.in_(states))) or 0
def refresh_shipping(db,o):
    lines=db.scalars(select(OrderLine).where(OrderLine.order_id==o.id)).all()
    done=all(shipped_qty(db,l.id)>=l.quantity for l in lines)
    any_shipped=any(shipped_qty(db,l.id)>0 for l in lines)
    o.status='SHIPPED' if done else ('PARTIALLY_SHIPPED' if any_shipped else 'CONFIRMED')
    if done and all(shipped_qty(db,l.id,True)>=l.quantity for l in lines) and outstanding(db,o.id)==0:o.status='CLOSED'
def ship_order(db,actor,d):
    roles(actor,'admin','warehouse');o=order_access(db,actor,d['order_id'])
    require(o.source=='CURRENT' and o.status in ['CONFIRMED','PARTIALLY_SHIPPED'],'订单尚未生效或已全发',409)
    w=get(db,Warehouse,d['warehouse_id']);require(w.kind!='CONSIGNMENT','批发出库不能使用寄售仓')
    destination=None
    if o.sale_type=='CONSIGNMENT':
        destination=get(db,Warehouse,d.get('consignment_location_id'))
        require(destination.kind=='CONSIGNMENT' and destination.dealer_id==o.dealer_id,'请选择该经销商自己的寄售地点',403)
    lines=db.scalars(select(OrderLine).where(OrderLine.order_id==o.id)).all()
    requested=d.get('lines') or [{'line_id':l.id,'quantity':l.quantity-shipped_qty(db,l.id)} for l in lines if l.quantity>shipped_qty(db,l.id)]
    qtys={};remaining={l.id:l.quantity-shipped_qty(db,l.id) for l in lines}
    for row in requested:
        lid=integer(row['line_id']);require(lid in remaining and lid not in qtys,'出库明细无效')
        qtys[lid]=integer(row['quantity']);require(qtys[lid]<=remaining[lid],'超出待发数量')
    require(qtys,'无待发数量')
    require(o.partial_allowed or all(qtys.get(lid,0)==q for lid,q in remaining.items()),'默认整单出货；部分出货须管理员授权')
    serials=str(d.get('serials','')).strip();exception=str(d.get('exception','')).strip()
    require(serials or exception,'请输入SN/车架号，缺号须填写异常说明')
    s=Shipment(order_id=o.id,warehouse_id=w.id,consignment_location_id=destination.id if destination else None,serials=serials,exception=exception,idempotency=str(d['_operation_key']))
    db.add(s);db.flush()
    reason=str(d.get('reason','')).strip();require(reason,'必须填写出库原因')
    for l in lines:
        q=qtys.get(l.id,0)
        if not q:continue
        if l.gift and o.special_7030:require(outstanding(db,o.id)==0,'70/30赠品需全额到账后出库')
        require(available(db,w.id,l.product_id,o.id)>=q or actor.role=='admin' and d.get('allow_negative'),'可用库存不足 / Estoque insuficiente')
        stock_entry(db,actor,w.id,l.product_id,-q,'CONSIGN_SEND' if destination else 'SHIP',reason,o.id,batch='shipment:'+str(s.id),allow_negative=bool(d.get('allow_negative')))
        if destination:
            stock_entry(db,actor,destination.id,l.product_id,q,'CONSIGN_SEND',reason,o.id,batch='shipment:'+str(s.id))
        db.add(ShipmentLine(shipment_id=s.id,line_id=l.id,quantity=q))
        rest=q
        for r in db.scalars(select(Reservation).where(Reservation.order_id==o.id,Reservation.line_id==l.id,Reservation.active==True).order_by(Reservation.warehouse_id!=w.id)).all():
            used=min(rest,r.quantity);r.quantity-=used;rest-=used
            if r.quantity==0:r.active=False
            if rest==0:break
    db.flush();refresh_shipping(db,o)
    audit(db,actor,'SHIPMENT_CREATE',s.id,f'order {o.id}; {reason}; SN exception={exception}')
    return {'id':s.id}
def shipment_action(db,actor,d):
    roles(actor,'admin','warehouse');s=get(db,Shipment,d['shipment_id']);o=order_access(db,actor,s.order_id)
    action=d['action'];reason=str(d.get('reason','')).strip()
    if action=='DELIVER':
        require(s.status=='PENDING_PICKUP','仅待提货可交付',409);s.status='DELIVERED';s.delivered=now()
    elif action in ['CANCEL','RETURN']:
        require(reason,'撤销/退回须填写原因')
        if action=='CANCEL':require(s.status=='PENDING_PICKUP','已交付只能实际退回',409)
        else:
            roles(actor,'admin');require(s.status=='DELIVERED' and d.get('actual_return') is True,'必须确认车辆已实际退回',409)
            c=db.scalar(select(Commission).where(Commission.order_id==o.id));require(c is None or c.status=='REVIEW_REQUIRED','佣金已审核，须先登记佣金负数调整/人工复核')
        for sl in db.scalars(select(ShipmentLine).where(ShipmentLine.shipment_id==s.id)).all():
            l=get(db,OrderLine,sl.line_id)
            if s.consignment_location_id:
                require(balance(db,s.consignment_location_id,l.product_id)>=sl.quantity,'寄售数量不足；已售车辆不能通过整笔撤销/退回重复回补')
                stock_entry(db,actor,s.consignment_location_id,l.product_id,-sl.quantity,'CONSIGN_RETURN',reason,o.id)
            stock_entry(db,actor,s.warehouse_id,l.product_id,sl.quantity,action,reason,o.id)
        s.status='CANCELED' if action=='CANCEL' else 'RETURNED'
    else:fail('交付动作无效')
    db.flush();refresh_shipping(db,o);audit(db,actor,'SHIPMENT_'+action,s.id,reason)
    return {'ok':True}
def report_consignment(db,actor,d):
    roles(actor,'admin','sales','dealer');w=get(db,Warehouse,d['warehouse_id'])
    require(w.kind=='CONSIGNMENT','不是寄售位置')
    dealer_access(db,actor,w.dealer_id);qty=integer(d['quantity']);pid=integer(d['product_id']);get(db,Product,pid)
    require(balance(db,w.id,pid)>=qty,'寄售库存不足')
    r=ConsignmentReport(warehouse_id=w.id,product_id=pid,quantity=qty,dealer_id=w.dealer_id,idempotency=d['_operation_key'])
    db.add(r);db.flush();audit(db,actor,'CONSIGNMENT_REPORT',r.id,qty)
    return {'id':r.id}
def confirm_consignment(db,actor,d):
    roles(actor,'admin');r=get(db,ConsignmentReport,d['report_id'])
    if r.status=='CONFIRMED':return {'id':r.order_id,'repeated':True}
    require(balance(db,r.warehouse_id,r.product_id)>=r.quantity,'寄售库存不足')
    dealer=get(db,Dealer,r.dealer_id);require(dealer.sales_id,'请先指定销售')
    p=get(db,Product,r.product_id);price=money(d.get('unit_price',str(Decimal(p.price)/100)))
    reason=str(d.get('reason','')).strip()
    require(price==p.price or reason,'特殊改价需原因')
    o=Order(dealer_id=r.dealer_id,sales_id=dealer.sales_id,creator_id=actor.id,mode='CONSIGNMENT',sale_type='CONSIGNMENT',sale_date=today().isoformat(),status='WAITING_INTERNAL_REVIEW',due=date_value(d['due']),promotion='STANDARD',terms='Confirmed consignment sale',total=price*r.quantity)
    db.add(o);db.flush()
    l=OrderLine(order_id=o.id,product_id=p.id,quantity=r.quantity,product_sku=p.sku,product_name=p.name,standard_price=p.price,unit_price=price,gift=False);db.add(l);db.flush()
    # Consignment sale consumes stock in-place; it does not reserve warehouse stock.
    o.status='CLOSED' if o.total==0 else 'SHIPPED';o.confirmed=now()
    db.add(Receivable(order_id=o.id,amount=o.total,due=o.due))
    stock_entry(db,actor,r.warehouse_id,p.id,-r.quantity,'CONSIGN_SOLD',reason or '经销商售出确认',o.id)
    s=Shipment(order_id=o.id,warehouse_id=r.warehouse_id,status='DELIVERED',delivered=now(),exception='Consignment report',idempotency='consignment:'+str(r.id));db.add(s);db.flush()
    db.add(ShipmentLine(shipment_id=s.id,line_id=l.id,quantity=r.quantity))
    r.status='CONFIRMED';r.order_id=o.id;audit(db,actor,'CONSIGNMENT_CONFIRM',r.id,f'order {o.id}')
    return {'id':o.id}
