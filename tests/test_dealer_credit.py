"""Dealer ratings must follow settled bank ledgers and preserve role scope."""
from datetime import date
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import Session
from app.models import Order,OrderLine,Receivable,Receipt,Allocation,OpeningCredit,Claim,Adjustment
from app.dealer_credit import ratings,month_shift
from app import dealer_credit

ASOF=date(2026,11,2)


def contract(e,*,did=None,due='2026-10-20',at='2026-10-01T12:00:00+00:00',amount=100000,qty=20,status='CONFIRMED',source='CURRENT',kind='WHOLESALE',receivable=True):
    with Session() as db:
        o=Order(dealer_id=did or e.did,sales_id=e.sid,creator_id=1,mode='ASSISTED',sale_type=kind,
            status=status,source=source,confirmed=at,due=due,total=amount,sale_date='2026-10-01')
        db.add(o);db.flush()
        db.add(OrderLine(order_id=o.id,product_id=e.pid,quantity=qty,product_sku='R6',product_name='R6 mini',standard_price=5000,unit_price=5000,gift=False))
        if receivable:db.add(Receivable(order_id=o.id,amount=amount,due=due,source=source))
        db.commit();return o.id


def payment(e,oid,amount=100000,day='2026-10-19',*,opening=False,allocate=True):
    with Session() as db:
        o=db.get(Order,oid);rid=None
        if opening:db.add(OpeningCredit(dealer_id=o.dealer_id,amount=amount,source='verified test opening'))
        else:
            r=Receipt(dealer_id=o.dealer_id,account_id=e.aid,amount=amount,date=day,
                reference=f'RATING-TEST-{oid}-{amount}-{day}',actor_id=1,created='2026-11-02T12:00:00+00:00')
            db.add(r);db.flush();rid=r.id
        if allocate:db.add(Allocation(dealer_id=o.dealer_id,order_id=oid,receipt_id=rid,amount=amount,actor_id=1,idempotency=f'rating-test-{oid}-{rid}-{day}-{amount}',created='2026-11-02T12:00:00+00:00'))
        db.commit();return rid


def rating(e,did=None,as_of=ASOF):
    with Session() as db:return ratings(db,[did or e.did],as_of=as_of,details=True)[did or e.did]


def test_new_dealer_has_no_invented_grade_or_manual_override(env):
    e=env;c=rating(e)
    assert c['grade']=='N' and c['score'] is None and c['components']['timeliness'] is None
    assert c['mature_order_count']==0 and c['current_month_quantity']==0
    e.act(e.admin,'dealer.save',dict(id=e.did,code='D1',name='Dealer 1',sales_id=e.sid,credit_rating='A'),expected=400)
    assert rating(e)==c


def test_punctual_full_payments_with_volume_get_a_and_mature_sample(env):
    e=env
    for m in ['08','09','10']:
        oid=contract(e,at=f'2026-{m}-01T12:00:00+00:00',due=f'2026-{m}-20')
        payment(e,oid,day=f'2026-{m}-19')
    c=rating(e)
    assert c['grade']=='A' and c['score']==100 and not c['provisional']
    assert c['on_time_rate']==c['amount_fulfillment_rate']==100
    assert c['monthly_average']==20 and c['current_month_quantity']==0
    assert c['monthly'][-2]['quantity']==20 and c['timing_sample_count']==3


@pytest.mark.parametrize('delay,expected',[(0,100),(1,80),(7,80),(8,50),(30,50),(31,0)])
def test_late_verified_receipts_use_actual_bank_dates(env,delay,expected):
    from datetime import timedelta
    e=env;due=date(2026,9,1);oid=contract(e,due=due.isoformat())
    payment(e,oid,day=(due+timedelta(days=delay)).isoformat())
    c=rating(e)
    assert c['components']['timeliness']==expected
    assert c['amount_fulfillment_rate']==100 and c['shortfall']==0
    assert c['average_late_payment_days']==delay and c['provisional']


def test_installments_accumulate_and_on_time_fraction_is_amount_weighted(env):
    e=env;oid=contract(e)
    payment(e,oid,40000,'2026-10-10');payment(e,oid,60000,'2026-10-25')
    c=rating(e)
    assert c['mature_order_count']==1 and c['completed_order_count']==1
    assert c['on_time_rate']==40 and c['components']['timeliness']==88
    assert c['amount_fulfillment_rate']==100 and c['orders'][0]['settled_date']=='2026-10-25'


def test_unverified_proofs_and_unallocated_credit_do_not_improve_rating(env):
    e=env;oid=contract(e);payment(e,oid,50000,'2026-10-10')
    before=rating(e)
    with Session() as db:
        db.add(Claim(order_id=oid,amount=50000,paid_date='2026-10-15',reference='TEST_ONLY',attachment='nonexistent-test.png',media_type='image/png',status='PENDING'));db.commit()
    payment(e,oid,50000,'2026-10-16',allocate=False)
    assert rating(e)==before
    assert before['on_time_rate']==before['amount_fulfillment_rate']==50
    assert before['shortfall']==50000


def test_future_due_and_unfinished_due_today_do_not_penalize(env):
    e=env;future=contract(e,due='2026-12-01');today=contract(e,due='2026-11-02')
    c=rating(e);assert c['grade']=='N' and c['mature_order_count']==0
    payment(e,today,day='2026-11-02')
    c=rating(e);assert c['mature_order_count']==1 and c['amount_fulfillment_rate']==100
    assert c['orders'][0]['order_id']==today and c['on_time_rate']==100


def test_old_unpaid_debt_does_not_age_out_or_get_hidden_by_volume(env):
    e=env
    for m in ['08','09','10']:
        oid=contract(e,at=f'2026-{m}-01T12:00:00+00:00',due=f'2026-{m}-20',qty=100)
        payment(e,oid,day=f'2026-{m}-19')
    old=contract(e,due='2025-01-01',amount=1);settled_old=contract(e,due='2025-01-02')
    payment(e,settled_old,day='2025-01-02')
    c=rating(e);assert c['score']>99 and c['grade']=='D' and c['grade_cap']=='D'
    assert c['overdue_amount']==1 and c['max_overdue_days']>30
    assert old in [r['order_id'] for r in c['orders']] and settled_old not in [r['order_id'] for r in c['orders']]


def test_unknown_opening_dates_do_not_invent_payment_punctuality(env):
    e=env;oid=contract(e);payment(e,oid,opening=True)
    c=rating(e);assert c['grade']=='N' and c['score'] is None and c['amount_fulfillment_rate']==100
    assert c['unknown_date_orders']==1 and c['orders'][0]['on_time_amount'] is None
    assert 'UNKNOWN_OPENING_DATES' in c['flags']


def test_eight_day_unpaid_debt_caps_otherwise_high_score_at_c(env):
    e=env
    for m in ['08','09','10']:
        oid=contract(e,at=f'2026-{m}-01T12:00:00+00:00',due=f'2026-{m}-20',qty=20)
        payment(e,oid,day=f'2026-{m}-19')
    contract(e,due='2026-10-25',amount=1)
    c=rating(e)
    assert c['score']>99 and c['grade']=='C' and c['grade_cap']=='C'
    assert c['max_overdue_days']==8


def test_unpaid_due_today_becomes_assessable_after_day_end(env):
    e=env;contract(e,due='2026-11-02')
    assert rating(e)['grade']=='N'
    tomorrow=rating(e,as_of=date(2026,11,3))
    assert tomorrow['mature_order_count']==1 and tomorrow['max_overdue_days']==1
    assert tomorrow['amount_fulfillment_rate']==0 and tomorrow['grade']=='D'


def test_volume_uses_complete_months_and_excludes_gifts_drafts_and_opening(env):
    e=env;a=contract(e,qty=6,at='2026-10-01T12:00:00+00:00')
    b=contract(e,qty=20,at='2026-11-01T12:00:00+00:00',kind='CONSIGNMENT')
    contract(e,qty=999,status='CANCELED');contract(e,qty=999,status='REJECTED')
    contract(e,qty=999,status='DRAFT');contract(e,qty=999,source='OPENING')
    contract(e,qty=999,kind='CONSIGNMENT',receivable=False)
    with Session() as db:
        db.add(OrderLine(order_id=a,product_id=e.pid,quantity=999,product_sku='R6-GIFT',product_name='Gift',standard_price=5000,unit_price=0,gift=True));db.commit()
    c=rating(e);assert c['monthly_average']==2 and c['components']['volume']==10
    assert c['current_month_quantity']==20 and sum(m['quantity'] for m in c['monthly'])==26
    # Current consignment order already owns its receivable: no extra report order.
    assert c['monthly'][-1]['quantity']==20


def test_volume_month_boundary_uses_sao_paulo_and_keeps_empty_months(env):
    e=env;contract(e,qty=9,at='2026-11-01T02:59:59+00:00')
    contract(e,qty=1,at='2026-11-01T03:00:00+00:00')
    c=rating(e);assert c['monthly_average']==3 and c['current_month_quantity']==1
    assert [m['quantity'] for m in c['monthly']]==[0,0,0,0,9,1]
    assert month_shift(date(2026,1,2),-1)==date(2025,12,1)


def test_future_bank_dates_do_not_reduce_shortfall_and_excess_is_capped(env):
    e=env;oid=contract(e);payment(e,oid,day='2026-12-01')
    assert rating(e)['shortfall']==100000
    payment(e,oid,200000,'2026-10-19')
    c=rating(e);assert c['amount_fulfillment_rate']==100 and c['on_time_rate']==100
    assert c['verified_paid']==100000 and c['orders'][0]['outstanding']==0


def test_approved_receivable_adjustment_is_visible_in_calculation(env):
    e=env;oid=contract(e);payment(e,oid,50000)
    with Session() as db:
        db.add(Adjustment(order_id=oid,amount=-50000,reason='Approved test adjustment',actor_id=1,created='2026-11-02T12:00:00+00:00'));db.commit()
    c=rating(e);assert c['agreed_amount']==50000 and c['amount_fulfillment_rate']==100
    assert c['orders'][0]['adjustment']==-50000


def test_api_auto_updates_and_scopes_all_summary_and_order_details(env,monkeypatch):
    e=env;monkeypatch.setattr(dealer_credit,'today',lambda:ASOF)
    first=contract(e);other=contract(e,did=e.did2)
    url=f'/api/dealers/{e.did}/credit-rating'
    before=e.admin.get(url).json();assert before['amount_fulfillment_rate']==0
    payment(e,first)
    for client in [e.admin,e.finance,e.sales,e.dealer]:
        r=client.get(url);assert r.status_code==200,r.text;c=r.json()
        assert c['amount_fulfillment_rate']==100 and [o['order_id'] for o in c['orders']]==[first]
        assert all('reference' not in o and 'account_id' not in o for o in c['orders'])
        summary=next(d for d in e.data(client)['dealers'] if d['id']==e.did)['credit_rating']
        assert summary['score']==c['score'] and 'orders' not in summary
    assert e.dealer2.get(url).status_code==403
    assert e.sales.get(f'/api/dealers/{e.did2}/credit-rating').status_code==403
    assert e.warehouse.get(url).status_code==403
    assert all('credit_rating' not in d for d in e.data(e.warehouse)['dealers'])
    with TestClient(app) as anonymous:assert anonymous.get(url).status_code==401
    assert e.admin.get('/api/dealers/99999/credit-rating').status_code==404
