"""Verified prepaid funds, automatic allocation and customer-visible balances."""
from sqlalchemy import select
from .core import *


def funds(db,did):
    result=[];held=0
    opening=total_sum(db,OpeningCredit.amount,OpeningCredit.dealer_id==did)-total_sum(db,Allocation.amount,Allocation.dealer_id==did,Allocation.receipt_id==None)
    if opening>0:result.append([None,opening])
    for r in db.scalars(select(Receipt).where(Receipt.dealer_id==did).order_by(Receipt.date,Receipt.id)).all():
        rest=r.amount-total_sum(db,Allocation.amount,Allocation.receipt_id==r.id)
        if rest<=0:continue
        claim=db.get(Claim,r.claim_id) if r.claim_id else None
        o=db.get(Order,claim.order_id) if claim else None
        # Part-paid cash proof belongs to its pending bank-funded order. It
        # cannot be spent again as a general recharge on a different order.
        if o and not o.confirmed and o.sale_type=='CASH' and o.payment_method=='BANK' and o.status not in ['CANCELED','REJECTED']:
            held+=rest;continue
        result.append([r.id,rest])
    return result,held


def match_credit(db,did,actor=None):
    """Caller must hold the shared transaction write lock."""
    dealer=get(db,Dealer,did)
    if not dealer.auto_apply_credit:return
    from .inventory import refresh_shipping
    from .orders import activate,cash_pending
    pool,_=funds(db,did)
    owner=actor.id if actor else None
    def apply(o,needed):
        nonlocal owner
        for f in pool:
            take=min(needed,f[1])
            if not take:continue
            if owner is None:
                receipt=db.get(Receipt,f[0]) if f[0] else None
                owner=receipt.actor_id if receipt else db.scalar(select(User.id).where(User.role=='admin').order_by(User.id).limit(1))
            db.add(Allocation(receipt_id=f[0],dealer_id=did,order_id=o.id,amount=take,actor_id=owner,idempotency='auto-credit:'+secrets.token_hex(20)))
            f[1]-=take;needed-=take
            db.flush()
            audit(db,actor,'AUTO_CREDIT_ALLOCATE',o.id,f'dealer {did}; receipt {f[0] or "opening"}; {take} cents')
            if not needed:break
        refresh_shipping(db,o)
    eligible=db.scalars(select(Order).join(Receivable,Receivable.order_id==Order.id).where(
        Order.dealer_id==did,Order.confirmed.is_not(None),Order.status.notin_(['CANCELED','REJECTED'])).order_by(Receivable.due,Order.id)).all()
    for o in eligible:
        if not sum(f[1] for f in pool):break
        apply(o,outstanding(db,o.id))
    # Balance-funded cash orders need no second bank transfer. They activate
    # only with the full verified amount and after any price approval.
    pending=db.scalars(select(Order).where(Order.dealer_id==did,Order.sale_type=='CASH',
        Order.payment_method=='BALANCE',Order.confirmed==None,Order.admin_review_pending==False,
        Order.status.in_(['DRAFT','WAITING_FINANCE_REVIEW'])).order_by(Order.due,Order.id)).all()
    for o in pending:
        if not cash_pending(o) or sum(f[1] for f in pool)<o.total:break
        from . import policies
        try:policies.validate_activation(db,o)
        except HTTPException as exc:
            audit(db,actor,'BALANCE_ACTIVATION_DEFERRED',o.id,exc.detail)
            continue
        activate(db,o,finance_verified=True,auto_credit=False)
        apply(o,o.total)
        audit(db,actor,'BALANCE_ORDER_ACTIVATE',o.id,f'verified prepaid funds; {o.total} cents')


def wallet(db,did):
    pool,held=funds(db,did);entries=[]
    opening=total_sum(db,OpeningCredit.amount,OpeningCredit.dealer_id==did)
    if opening:entries.append(dict(key='opening',kind='OPENING',amount=opening,created='',paid_date=None,order_id=None,order_number=None))
    for r in db.scalars(select(Receipt).where(Receipt.dealer_id==did)).all():
        entries.append(dict(key='receipt:'+str(r.id),kind='RECHARGE' if r.is_recharge else 'RECEIPT',amount=r.amount,created=r.created,paid_date=r.date,order_id=None,order_number=None))
    for a in db.scalars(select(Allocation).where(Allocation.dealer_id==did)).all():
        entries.append(dict(key='allocation:'+str(a.id),kind='APPLIED',amount=-a.amount,created=a.created,paid_date=None,order_id=a.order_id,order_number=f'H{a.order_id:06d}'))
    # A receipt is inserted before its allocations. The tie-breaker preserves
    # that order even with timestamps stored to the second.
    entries.sort(key=lambda e:(e['created'],0 if e['amount']>0 else 1,int(e['key'].split(':')[-1]) if ':' in e['key'] else 0))
    running=0
    for e in entries:running+=e['amount'];e['balance']=running
    return dict(dealer_id=did,available=sum(f[1] for f in pool),held=held,total=credit(db,did),
                credited=opening+total_sum(db,Receipt.amount,Receipt.dealer_id==did),applied=total_sum(db,Allocation.amount,Allocation.dealer_id==did),
                auto_apply=get(db,Dealer,did).auto_apply_credit,entries=list(reversed(entries)))


def reject_recharge(db,actor,d):
    roles(actor,'admin','finance');r=get(db,Recharge,d['recharge_id'])
    require(r.status=='PENDING','充值申报已处理',409)
    note=str(d.get('reason','')).strip();require(note,'退回充值申报须填写原因')
    r.status='REJECTED';r.note=note
    audit(db,actor,'RECHARGE_REJECT',r.id,note)
    return {'id':r.id}
