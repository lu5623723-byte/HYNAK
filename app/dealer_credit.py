"""Read-only dealer ratings from scoped orders and verified payment ledgers."""
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .core import today
from .models import Order, OrderLine, Receivable, Adjustment, Allocation, Receipt

VERSION='HYNAK-CREDIT-v1'
WEIGHTS={'timeliness':50,'amount':30,'volume':20}
ACTIVE={'CONFIRMED','PARTIALLY_SHIPPED','SHIPPED','CLOSED'}
MONTHLY_TARGET=20


def month_shift(day,offset):
    n=day.year*12+day.month-1+offset
    return date(n//12,n%12+1,1)


def rounded(value):return float(Decimal(str(value)).quantize(Decimal('0.1'),rounding=ROUND_HALF_UP))
def percent(a,b):return rounded(Decimal(a)*100/Decimal(b)) if b else None


def local_day(value):
    dt=datetime.fromisoformat(value)
    if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo('America/Sao_Paulo')).date()


def ratings(db,dealer_ids,*,as_of=None,details=False):
    """No score writes/overrides. Inputs must already be permission scoped."""
    as_of=as_of or today();dids=set(dealer_ids)
    if not dids:return {}
    window_start=month_shift(as_of,-5)
    months=[month_shift(as_of,i).strftime('%Y-%m') for i in range(-5,1)]
    volume_months=months[-4:-1]
    orders=db.execute(select(Order,Receivable).join(Receivable,Receivable.order_id==Order.id).where(
        Order.dealer_id.in_(dids),Order.source=='CURRENT',Receivable.source=='CURRENT',
        Order.status.in_(ACTIVE),Order.confirmed.is_not(None))).all()
    orders=[(o,r) for o,r in orders if local_day(o.confirmed)<=as_of]
    oids={o.id for o,_ in orders};units=defaultdict(int)
    for line in db.scalars(select(OrderLine).where(OrderLine.order_id.in_(oids),OrderLine.gift==False,OrderLine.unit_price>0)).all():
        units[line.order_id]+=line.quantity
    adjustments=defaultdict(int)
    for a in db.scalars(select(Adjustment).where(Adjustment.order_id.in_(oids))).all():
        if local_day(a.created)<=as_of:adjustments[a.order_id]+=a.amount
    payments=defaultdict(list)
    for a,r in db.execute(select(Allocation,Receipt).outerjoin(Receipt,Receipt.id==Allocation.receipt_id).where(Allocation.order_id.in_(oids))).all():
        if a.amount<=0 or local_day(a.created)>as_of:continue
        if r is not None and (r.dealer_id!=a.dealer_id or r.date>as_of.isoformat()):continue
        payments[a.order_id].append((r.date if r else None,a.amount))
    grouped=defaultdict(list)
    for o,r in orders:grouped[o.dealer_id].append((o,r))
    result={}
    for did in dids:
        monthly={m:0 for m in months};rows=[]
        total_due=paid_total=ontime_total=timing_due=timing_points=0
        completed=timing_samples=unknown_dates=overdue_amount=overdue_orders=max_overdue=0
        late_cents=late_days_cents=0;last_payment=None
        for o,r in grouped[did]:
            at=local_day(o.confirmed).strftime('%Y-%m')
            if at in monthly:monthly[at]+=units[o.id]
            target=max(0,r.amount+adjustments[o.id])
            if not target:continue
            known=sorted((d,amount) for d,amount in payments[o.id] if d)
            opening=sum(amount for d,amount in payments[o.id] if not d)
            paid=min(target,opening+sum(amount for _,amount in known));balance=target-paid
            due=date.fromisoformat(r.due)
            # An incomplete same-day order has until day end to pay.
            if due>as_of or (due==as_of and balance):continue
            # Settled older history ages out; old unpaid debts never disappear.
            if due<window_start and not balance:continue
            total_due+=target;paid_total+=paid;completed+=int(balance==0)
            ontime=points=late=days_weight=applied=0;settled=None
            for paid_date,amount in known:
                take=min(amount,target-applied)
                if not take:break
                delay=max(0,(date.fromisoformat(paid_date)-due).days)
                ontime+=take if delay==0 else 0
                points+=take*(100 if delay==0 else 80 if delay<=7 else 50 if delay<=30 else 0)
                if delay:late+=take;days_weight+=take*delay
                applied+=take;last_payment=max(last_payment or paid_date,paid_date)
                if applied>=target:settled=paid_date
            if opening:unknown_dates+=1
            else:
                timing_samples+=1;timing_due+=target;timing_points+=points;ontime_total+=ontime
                late_cents+=late;late_days_cents+=days_weight
            overdue_days=max(0,(as_of-due).days) if balance else 0
            if balance and overdue_days:
                overdue_amount+=balance;overdue_orders+=1;max_overdue=max(max_overdue,overdue_days)
            rows.append(dict(order_id=o.id,number=f'H{o.id:06d}',due=r.due,agreed_amount=target,
                verified_paid=paid,outstanding=balance,on_time_amount=ontime if not opening else None,
                settled_date=settled if not opening else None,overdue_days=overdue_days,
                timing_known=not bool(opening),adjustment=adjustments[o.id]))
        avg=Decimal(sum(monthly[m] for m in volume_months))/3
        timing_score=rounded(Decimal(timing_points)/timing_due) if timing_due else None
        amount_score=percent(paid_total,total_due)
        volume_score=rounded(min(Decimal(100),avg*100/MONTHLY_TARGET))
        components={'timeliness':timing_score,'amount':amount_score,'volume':volume_score}
        # Absent dated payment history is neither good nor bad credit.
        score=None;grade='N';cap=None
        if timing_score is not None and amount_score is not None:
            score=rounded(sum(Decimal(str(components[k]))*w for k,w in WEIGHTS.items())/100)
            grade='A' if score>=90 else 'B' if score>=75 else 'C' if score>=60 else 'D'
            if max_overdue>30:cap='D'
            elif max_overdue>7:cap='C'
            if cap and 'ABCD'.index(grade)<'ABCD'.index(cap):grade=cap
        flags=[]
        if not rows:flags.append('NO_DUE_HISTORY')
        if timing_samples<3:flags.append('SMALL_SAMPLE')
        if unknown_dates:flags.append('UNKNOWN_OPENING_DATES')
        if max_overdue>30:flags.append('OVERDUE_OVER_30')
        elif overdue_orders:flags.append('OVERDUE')
        r=dict(version=VERSION,as_of=as_of.isoformat(),window_start=window_start.isoformat(),
            grade=grade,score=score,provisional=timing_samples<3,grade_cap=cap,weights=WEIGHTS,
            components=components,mature_order_count=len(rows),timing_sample_count=timing_samples,
            completed_order_count=completed,unknown_date_orders=unknown_dates,
            agreed_amount=total_due,verified_paid=paid_total,shortfall=total_due-paid_total,
            on_time_rate=percent(ontime_total,timing_due),amount_fulfillment_rate=amount_score,
            average_late_payment_days=rounded(Decimal(late_days_cents)/late_cents) if late_cents else 0,
            overdue_amount=overdue_amount,overdue_order_count=overdue_orders,max_overdue_days=max_overdue,
            last_payment_date=last_payment,monthly_target=MONTHLY_TARGET,
            monthly_average=rounded(avg),volume_months=volume_months,
            current_month_quantity=monthly[months[-1]],
            monthly=[dict(month=m,quantity=monthly[m],complete=m!=months[-1]) for m in months],flags=flags)
        if details:r['orders']=sorted(rows,key=lambda x:(x['due'],x['order_id']),reverse=True)
        result[did]=r
    return result
