from sqlalchemy import select
from .core import *
from .inventory import shipped_qty
from .orders import cash_pending
from .policies import available_policy
from .balances import wallet
from .dealer_credit import ratings as dealer_ratings
from .policy_assistant import draft_row, ai_status

def plain(obj,exclude=()):return {c.name:getattr(obj,c.name) for c in obj.__table__.columns if c.name not in exclude}

def purchase_order(o):
    return o.source=='CURRENT' and (o.sale_type!='CONSIGNMENT' or o.mode=='CONSIGNMENT') and bool(o.confirmed) and o.status in ['CONFIRMED','PARTIALLY_SHIPPED','SHIPPED','CLOSED']

def confirmed_day(o):
    return datetime.fromisoformat(o.confirmed).astimezone(ZoneInfo('America/Sao_Paulo')).date().isoformat() if o.confirmed else None

def payment_histories(db,oids):
    """One row per bank receipt/order, even when allocated in several steps.

    Bank dates are actual receipt dates, never allocation timestamps. No bank
    accounts or private bank references are exposed to sales/dealer viewers.
    """
    grouped={}
    for a,r in db.execute(select(Allocation,Receipt).outerjoin(Receipt,Receipt.id==Allocation.receipt_id).where(Allocation.order_id.in_(oids))).all():
        key=(a.order_id,a.receipt_id)
        row=grouped.setdefault(key,dict(receipt_id=a.receipt_id,amount=0,paid_date=r.date if r else None,allocated_at=a.created,source='BANK' if r else 'OPENING_CREDIT'))
        row['amount']+=a.amount
        row['allocated_at']=min(row['allocated_at'],a.created)
    result={}
    for (oid,_),row in grouped.items():result.setdefault(oid,[]).append(row)
    for rows in result.values():
        rows.sort(key=lambda r:(r['paid_date'] or '',r['receipt_id'] or 0))
        sequence=0
        for row in rows:
            if row['source']=='BANK':sequence+=1
            row['sequence']=sequence if row['source']=='BANK' else None
    return result

def purchase_summary(db,actor,start,end):
    roles(actor,'dealer')
    start=date_value(start);end=date_value(end)
    require(start<=end,'开始日期不能晚于结束日期 / Intervalo de datas inválido')
    orders=db.scalars(select(Order).where(Order.dealer_id==actor.dealer_id)).all()
    selected=[]
    for o in orders:
        if not purchase_order(o):continue
        at=confirmed_day(o)
        if at and start<=at<=end:selected.append(o)
    return dict(start=start,end=end,basis='confirmed',order_count=len(selected),total_amount=sum(o.total for o in selected))
def snapshot(db,actor):
    dealers=db.scalars(select(Dealer).order_by(Dealer.id)).all()
    if actor.role=='dealer':dealers=[d for d in dealers if d.id==actor.dealer_id]
    if actor.role=='sales':dealers=[d for d in dealers if d.sales_id==actor.id]
    dids={d.id for d in dealers}
    orders=db.scalars(select(Order).where(Order.dealer_id.in_(dids)).order_by(Order.id.desc())).all()
    oids={o.id for o in orders};dealer_map={d.id:d for d in dealers}
    histories=payment_histories(db,oids) if actor.role in ['admin','finance','sales','dealer'] else {}
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
        row=plain(o,excluded);
        if cash_pending(o):row['status']='WAITING_FINANCE_REVIEW'
        r=db.scalar(select(Receivable).where(Receivable.order_id==o.id));due=r.due if r else o.due;unpaid=outstanding(db,o.id)
        row.update(number=f'H{o.id:06d}',dealer=dealer_map[o.dealer_id].name,sales=user_map[o.sales_id].name if o.sales_id in user_map else '',lines=line_map.get(o.id,[]),receivable=adjusted_amount(db,o.id),received=allocated(db,o.id),outstanding=unpaid,due=due,overdue_days=max(0,(today()-date.fromisoformat(due)).days) if unpaid else 0,payment_status='VERIFYING' if cash_pending(o) else 'OVERDUE' if unpaid and due<today_iso else 'PAID' if r and unpaid==0 else 'PARTIAL' if allocated(db,o.id)>0 else 'UNPAID',reserved=total_sum(db,Reservation.quantity,Reservation.order_id==o.id,Reservation.active==True),commission_eligible=commission_eligible(db,o))
        if actor.role=='dealer':row.pop('commission_eligible',None)
        history=histories.get(o.id,[])
        row.update(payment_history=history,payment_count=sum(h['source']=='BANK' for h in history),
                   last_payment_date=max((h['paid_date'] for h in history if h['paid_date']),default=None),
                   consignment_full_settlement=o.sale_type=='CONSIGNMENT' and o.mode!='CONSIGNMENT' and r is not None)
        order_rows.append(row)
    receipts=db.scalars(select(Receipt).where(Receipt.dealer_id.in_(dids)).order_by(Receipt.id.desc())).all() if actor.role!='warehouse' else []
    claims=db.scalars(select(Claim).where(Claim.order_id.in_(oids)).order_by(Claim.id.desc())).all() if actor.role!='warehouse' else []
    commissions=db.scalars(select(Commission).where(Commission.order_id.in_(oids))).all() if actor.role in ['admin','finance','sales'] else []
    dealer_rows=[]
    credit_ratings=dealer_ratings(db,dids) if actor.role in ['admin','finance','sales','dealer'] else {}
    for d in dealers:
        ds=[o for o in order_rows if o['dealer_id']==d.id]
        row=plain(d);row.update(sales=user_map[d.sales_id].name if d.sales_id in user_map else '',receivable=sum(o['receivable'] for o in ds),received=sum(o['received'] for o in ds),outstanding=sum(o['outstanding'] for o in ds),credit=credit(db,d.id))
        if d.id in credit_ratings:row['credit_rating']=credit_ratings[d.id]
        dealer_rows.append(row)
    followups=db.scalars(select(Followup).where(Followup.dealer_id.in_(dids)).order_by(Followup.id.desc())).all() if actor.role in ['admin','finance','sales'] else []
    metrics={'waiting':sum(o.status in ['DRAFT','WAITING_INTERNAL_REVIEW','WAITING_DEALER_CONFIRMATION','WAITING_FINANCE_REVIEW'] for o in orders),'today_sales':sum(o.total for o in orders if o.source=='CURRENT' and (o.sale_type!='CONSIGNMENT' or o.mode=='CONSIGNMENT') and o.status not in ['CANCELED','REJECTED'] and o.confirmed and datetime.fromisoformat(o.confirmed).astimezone(ZoneInfo('America/Sao_Paulo')).date().isoformat()==today_iso),'month_sales':sum(o.total for o in orders if o.source=='CURRENT' and (o.sale_type!='CONSIGNMENT' or o.mode=='CONSIGNMENT') and o.status not in ['CANCELED','REJECTED'] and o.confirmed and datetime.fromisoformat(o.confirmed).astimezone(ZoneInfo('America/Sao_Paulo')).strftime('%Y-%m')==today_iso[:7]),'receivable':sum(o['receivable'] for o in order_rows),'verified':sum(r.amount for r in receipts),'allocated':sum(o['received'] for o in order_rows),'outstanding':sum(o['outstanding'] for o in order_rows),'overdue':sum(o['outstanding'] for o in order_rows if o['overdue_days']>0),'claims_pending':sum(c.amount for c in claims if c.status=='PENDING'),'credit':sum(d['credit'] for d in dealer_rows)}
    month_orders=[o for o in orders if purchase_order(o) and o.confirmed and confirmed_day(o)[:7]==today_iso[:7]]
    metrics.update(month_order_count=len(month_orders),month_sales=sum(o.total for o in month_orders),month=today_iso[:7])
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
    supply_warehouses=db.scalars(select(Warehouse).where(Warehouse.kind!='CONSIGNMENT')).all()
    product_rows=[]
    for p in products:
        sellable=sum(max(0,available(db,w.id,p.id)) for w in supply_warehouses)
        product_rows.append(dict(plain(p),in_stock=sellable>0,available_quantity=sellable))
    receipt_rows=[]
    for r in receipts:
        row=plain(r,() if actor.role in ['admin','finance'] else ('account_id','reference','actor_id','claim_id'))
        row['unallocated']=r.amount-total_sum(db,Allocation.amount,Allocation.receipt_id==r.id);receipt_rows.append(row)
    policy_rows=[plain(p,() if actor.role=='admin' else ('creator_id',)) for p in db.scalars(select(SalesPolicy).order_by(SalesPolicy.created.desc(),SalesPolicy.id.desc())).all() if actor.role=='admin' or (actor.role=='sales' and available_policy(p))] if actor.role in ['admin','sales'] else []
    wallet_rows=[wallet(db,d.id) for d in dealers] if actor.role in ['admin','finance','sales','dealer'] else []
    recharge_rows=[plain(r,('attachment','media_type')) for r in db.scalars(select(Recharge).where(Recharge.dealer_id.in_(dids)).order_by(Recharge.id.desc())).all()] if actor.role in ['admin','finance','sales','dealer'] else []
    metrics['available_credit']=sum(w['available'] for w in wallet_rows)
    return {'policy_drafts':[draft_row(d) for d in db.scalars(select(PolicyDraft).order_by(PolicyDraft.updated.desc(),PolicyDraft.id.desc()).limit(100)).all()] if actor.role=='admin' else [],'policy_ai':ai_status() if actor.role=='admin' else None,'wallets':wallet_rows,'recharges':recharge_rows,'policies':policy_rows,'user':plain(actor,('password',)),'metrics':metrics,'dealers':dealer_rows,'products':product_rows,'stock_checked_at':now(),'warehouses':[plain(w) for w in warehouses],'inventory':inventory,'orders':order_rows,'claims':[plain(c,('attachment','media_type')) for c in claims],'receipts':receipt_rows,'allocations':[plain(a) for a in db.scalars(select(Allocation).where(Allocation.order_id.in_(oids))).all()] if actor.role!='warehouse' else [],'shipments':[plain(s,('idempotency',)) for s in db.scalars(select(Shipment).where(Shipment.order_id.in_(oids)).order_by(Shipment.id.desc())).all()],'reports':[plain(r,('idempotency',)) for r in db.scalars(select(ConsignmentReport).where(ConsignmentReport.dealer_id.in_(dids))).all()],'followups':[plain(f) for f in followups],'commissions':[dict(plain(c),adjustments=total_sum(db,CommissionAdjustment.amount,CommissionAdjustment.commission_id==c.id)) for c in commissions],'sales_summary':sales_summary,'commission_months':list(monthly.values()),'users':[plain(u,('password',)) for u in users] if actor.role=='admin' else [{'id':u.id,'name':u.name,'role':u.role} for u in users if u.role=='sales' and (actor.role!='sales' or u.id==actor.id) and (actor.role!='dealer' or u.id in {d.sales_id for d in dealers})],'bank_accounts':[plain(a) for a in db.scalars(select(BankAccount)).all()] if actor.role in ['admin','finance'] else [],'ledger':[plain(l) for l in db.scalars(select(StockLedger).order_by(StockLedger.id.desc()).limit(2000)).all() if l.warehouse_id in {w.id for w in warehouses}] if actor.role!='dealer' else [],'audit':[plain(a) for a in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(300)).all()] if actor.role=='admin' else [],'imports':[plain(b,('payload','original')) for b in db.scalars(select(ImportBatch).order_by(ImportBatch.id.desc())).all()] if actor.role=='admin' else []}
