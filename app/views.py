from sqlalchemy import select
from .core import *
from .inventory import shipped_qty

def plain(obj,exclude=()):return {c.name:getattr(obj,c.name) for c in obj.__table__.columns if c.name not in exclude}
def snapshot(db,actor):
    dealers=db.scalars(select(Dealer).order_by(Dealer.id)).all()
    if actor.role=='dealer':dealers=[d for d in dealers if d.id==actor.dealer_id]
    if actor.role=='sales':dealers=[d for d in dealers if d.sales_id==actor.id]
    dids={d.id for d in dealers}
    orders=db.scalars(select(Order).where(Order.dealer_id.in_(dids)).order_by(Order.id.desc())).all()
    oids={o.id for o in orders};dealer_map={d.id:d for d in dealers}
    users=db.scalars(select(User)).all();user_map={u.id:u for u in users}
    products=db.scalars(select(Product).order_by(Product.id)).all();product_map={p.id:p for p in products}
    warehouses=db.scalars(select(Warehouse)).all()
    if actor.role=='dealer':warehouses=[w for w in warehouses if w.kind=='CONSIGNMENT' and w.dealer_id==actor.dealer_id]
    elif actor.role=='sales':warehouses=[w for w in warehouses if w.kind!='CONSIGNMENT' or w.dealer_id in dids]
    inventory=[]
    for w in warehouses:
        for p in products:
            qty=balance(db,w.id,p.id);res=reserved(db,w.id,p.id)
            if qty or res or db.scalar(select(StockLedger.id).where(StockLedger.warehouse_id==w.id,StockLedger.product_id==p.id).limit(1)):inventory.append({'warehouse_id':w.id,'warehouse':w.name,'kind':w.kind,'dealer_id':w.dealer_id,'product_id':p.id,'sku':p.sku,'name':p.name,'quantity':qty,'reserved':res,'available':qty-res})
    lines=db.scalars(select(OrderLine).where(OrderLine.order_id.in_(oids))).all()
    line_map={}
    for l in lines:
        row=plain(l);row['reserved']=total_sum(db,Reservation.quantity,Reservation.line_id==l.id,Reservation.active==True);row.update(sku=l.product_sku,name=l.product_name,shipped=shipped_qty(db,l.id),delivered=shipped_qty(db,l.id,True))
        line_map.setdefault(l.order_id,[]).append(row)
    order_rows=[];today_iso=today().isoformat()
    for o in orders:
        excluded=('idempotency','commission_bps') if actor.role=='dealer' else ('idempotency',)
        row=plain(o,excluded);r=db.scalar(select(Receivable).where(Receivable.order_id==o.id));due=r.due if r else o.due;unpaid=outstanding(db,o.id)
        row.update(number=f'H{o.id:06d}',dealer=dealer_map[o.dealer_id].name,sales=user_map[o.sales_id].name if o.sales_id in user_map else '',lines=line_map.get(o.id,[]),receivable=adjusted_amount(db,o.id),received=allocated(db,o.id),outstanding=unpaid,due=due,overdue_days=max(0,(today()-date.fromisoformat(due)).days) if unpaid else 0,payment_status='OVERDUE' if unpaid and due<today_iso else 'PAID' if r and unpaid==0 else 'PARTIAL' if allocated(db,o.id)>0 else 'UNPAID',reserved=total_sum(db,Reservation.quantity,Reservation.order_id==o.id,Reservation.active==True),commission_eligible=commission_eligible(db,o))
        if actor.role=='dealer':row.pop('commission_eligible',None)
        order_rows.append(row)
    receipts=db.scalars(select(Receipt).where(Receipt.dealer_id.in_(dids)).order_by(Receipt.id.desc())).all() if actor.role!='warehouse' else []
    claims=db.scalars(select(Claim).where(Claim.order_id.in_(oids)).order_by(Claim.id.desc())).all() if actor.role!='warehouse' else []
    commissions=db.scalars(select(Commission).where(Commission.order_id.in_(oids))).all() if actor.role in ['admin','finance','sales'] else []
    dealer_rows=[]
    for d in dealers:
        ds=[o for o in order_rows if o['dealer_id']==d.id]
        row=plain(d);row.update(sales=user_map[d.sales_id].name if d.sales_id in user_map else '',receivable=sum(o['receivable'] for o in ds),received=sum(o['received'] for o in ds),outstanding=sum(o['outstanding'] for o in ds),credit=credit(db,d.id))
        dealer_rows.append(row)
    followups=db.scalars(select(Followup).where(Followup.dealer_id.in_(dids)).order_by(Followup.id.desc())).all() if actor.role in ['admin','finance','sales'] else []
    metrics={'waiting':sum(o.status in ['DRAFT','WAITING_INTERNAL_REVIEW','WAITING_DEALER_CONFIRMATION'] for o in orders),'today_sales':sum(o.total for o in orders if o.source=='CURRENT' and (o.sale_type!='CONSIGNMENT' or o.mode=='CONSIGNMENT') and o.status not in ['CANCELED','REJECTED'] and o.confirmed and datetime.fromisoformat(o.confirmed).astimezone(ZoneInfo('America/Sao_Paulo')).date().isoformat()==today_iso),'month_sales':sum(o.total for o in orders if o.source=='CURRENT' and (o.sale_type!='CONSIGNMENT' or o.mode=='CONSIGNMENT') and o.status not in ['CANCELED','REJECTED'] and o.confirmed and datetime.fromisoformat(o.confirmed).astimezone(ZoneInfo('America/Sao_Paulo')).strftime('%Y-%m')==today_iso[:7]),'receivable':sum(o['receivable'] for o in order_rows),'verified':sum(r.amount for r in receipts),'allocated':sum(o['received'] for o in order_rows),'outstanding':sum(o['outstanding'] for o in order_rows),'overdue':sum(o['outstanding'] for o in order_rows if o['overdue_days']>0),'claims_pending':sum(c.amount for c in claims if c.status=='PENDING'),'credit':sum(d['credit'] for d in dealer_rows)}
    sales_summary=[]
    if actor.role in ['admin','finance','sales']:
        for u in users:
            if u.role!='sales' or actor.role=='sales' and u.id!=actor.id:continue
            ds=[d for d in dealer_rows if d['sales_id']==u.id]
            sales_summary.append({'sales_id':u.id,'name':u.name,**{k:sum(d[k] for d in ds) for k in ['receivable','received','outstanding']}})
    monthly={}
    for c in commissions:
        key=(c.approved_at[:7],c.sales_id)
        r=monthly.setdefault(key,{'month':key[0],'sales_id':c.sales_id,'sales':user_map[c.sales_id].name,'base':0,'approved':0,'paid':0,'adjustments':0})
        r['base']+=c.base;r['approved']+=c.amount;r['paid']+=c.paid_amount or 0
        r['adjustments']+=total_sum(db,CommissionAdjustment.amount,CommissionAdjustment.commission_id==c.id)
    receipt_rows=[]
    for r in receipts:
        row=plain(r,() if actor.role in ['admin','finance'] else ('account_id','reference','actor_id','claim_id'))
        row['unallocated']=r.amount-total_sum(db,Allocation.amount,Allocation.receipt_id==r.id);receipt_rows.append(row)
    return {'user':plain(actor,('password',)),'metrics':metrics,'dealers':dealer_rows,'products':[dict(plain(p),in_stock=any(balance(db,w.id,p.id)-reserved(db,w.id,p.id)>0 for w in db.scalars(select(Warehouse).where(Warehouse.kind!='CONSIGNMENT')).all())) for p in products],'warehouses':[plain(w) for w in warehouses],'inventory':inventory,'orders':order_rows,'claims':[plain(c,('attachment','media_type')) for c in claims],'receipts':receipt_rows,'allocations':[plain(a) for a in db.scalars(select(Allocation).where(Allocation.order_id.in_(oids))).all()] if actor.role!='warehouse' else [],'shipments':[plain(s,('idempotency',)) for s in db.scalars(select(Shipment).where(Shipment.order_id.in_(oids)).order_by(Shipment.id.desc())).all()],'reports':[plain(r,('idempotency',)) for r in db.scalars(select(ConsignmentReport).where(ConsignmentReport.dealer_id.in_(dids))).all()],'followups':[plain(f) for f in followups],'commissions':[dict(plain(c),adjustments=total_sum(db,CommissionAdjustment.amount,CommissionAdjustment.commission_id==c.id)) for c in commissions],'sales_summary':sales_summary,'commission_months':list(monthly.values()),'users':[plain(u,('password',)) for u in users] if actor.role=='admin' else [{'id':u.id,'name':u.name,'role':u.role} for u in users if u.role=='sales' and (actor.role!='sales' or u.id==actor.id) and (actor.role!='dealer' or u.id in {d.sales_id for d in dealers})],'bank_accounts':[plain(a) for a in db.scalars(select(BankAccount)).all()] if actor.role in ['admin','finance'] else [],'ledger':[plain(l) for l in db.scalars(select(StockLedger).order_by(StockLedger.id.desc()).limit(2000)).all() if l.warehouse_id in {w.id for w in warehouses}] if actor.role!='dealer' else [],'audit':[plain(a) for a in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(300)).all()] if actor.role=='admin' else [],'imports':[plain(b,('payload','original')) for b in db.scalars(select(ImportBatch).order_by(ImportBatch.id.desc())).all()] if actor.role=='admin' else []}
