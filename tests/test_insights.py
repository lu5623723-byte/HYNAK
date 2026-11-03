"""Operational alerts use verified facts and never execute business actions."""
from copy import deepcopy
from datetime import timedelta
import pytest
from fastapi import HTTPException
from app.insights import analyze, settings
from app.core import today

AT='2026-11-02T15:00:00+00:00'


def sample():
    return dict(orders=[],dealers=[dict(id=1,name='Dealer A',outstanding=0,credit_rating={})],
                products=[dict(id=1,sku='R6',name='R6 mini',active=True)],
                warehouses=[dict(id=1,name='W1',kind='PRIMARY'),dict(id=2,name='W2',kind='SECONDARY'),dict(id=3,name='C1',kind='CONSIGNMENT')],
                inventory=[dict(warehouse_id=1,product_id=1,warehouse='W1',sku='R6',quantity=30,reserved=0,available=30)],
                wallets=[dict(dealer_id=1,available=0,held=0)],claims=[],recharges=[],shipments=[],reports=[],policies=[])


def order(oid=1,**kw):
    return dict(id=oid,number=f'H{oid:06d}',dealer_id=1,dealer='Dealer A',source='CURRENT',
                status='CONFIRMED',confirmed='2026-11-01T15:00:00+00:00',created='2026-10-30T15:00:00+00:00',
                due='2026-10-31',outstanding=580000,received=0,receivable=580000,total=580000,payment_method='BANK',
                lines=[dict(id=oid,product_id=1,quantity=1,gift=False,shipped=0,reserved=0)])|kw


def codes(result,code):return [a for a in result['alerts'] if a['code']==code]
def report(data,reservations=(),**kw):return analyze(data,reservations,at=AT,**kw)


@pytest.mark.parametrize('role',['sales','finance','warehouse','dealer'])
def test_workbench_api_is_admin_only(env,role):
    assert getattr(env,role).get('/api/admin/insights').status_code==403


def test_overdue_uses_real_outstanding_not_pending_claims_and_deduplicates_money():
    d=sample();d['orders']=[order(),order(2,status='CANCELED')];d['claims']=[dict(id=1,order_id=1,status='PENDING',amount=580000)]
    d['dealers'][0]['credit_rating']=dict(grade='D',max_overdue_days=35,overdue_amount=580000,provisional=True)
    r=report(d);assert r['summary']['overdue_amount']==580000
    assert len(codes(r,'OVERDUE'))==1 and codes(r,'OVERDUE')[0]['amount']==580000
    assert codes(r,'PAYMENT_PROOFS')[0]['facts']['declared_only']
    assert codes(r,'CREDIT_RISK')[0]['facts']['provisional']
    assert '暂评' in codes(r,'CREDIT_RISK')[0]['reason'][0]


def test_sao_paulo_due_date_and_thresholds_are_explicit():
    d=sample();d['orders']=[order(due='2026-11-02')]
    r=analyze(d,at='2026-11-03T02:59:59+00:00')
    assert r['as_of']=='2026-11-02' and not codes(r,'OVERDUE')
    assert codes(r,'DUE_SOON')[0]['priority']=='MEDIUM'
    d['orders'][0]['due']='2026-10-31'
    assert codes(report(d),'OVERDUE')[0]['priority']=='MEDIUM'
    assert codes(report(d,options={'urgent_overdue_days':2}),'OVERDUE')[0]['priority']=='HIGH'


def test_payment_and_balance_reviews_have_correct_owners_and_funds():
    d=sample();d['orders']=[order(status='WAITING_FINANCE_REVIEW',confirmed=None),order(2,status='WAITING_FINANCE_REVIEW',confirmed=None,payment_method='BALANCE')]
    d['wallets'][0].update(available=100000,held=480000)
    r=report(d);assert codes(r,'CASH_REVIEW')[0]['owner'][0]=='财务'
    assert codes(r,'BALANCE_ORDER')[0]['amount']==480000
    assert not codes(r,'OVERDUE')


def test_admin_review_precedes_payment_verification_alert():
    d=sample();d['orders']=[order(status='WAITING_INTERNAL_REVIEW',confirmed=None)]
    d['claims']=[dict(id=1,order_id=1,status='PENDING',amount=10000)]
    r=report(d);assert len(codes(r,'ADMIN_REVIEW'))==1
    assert not codes(r,'CASH_REVIEW') and not codes(r,'PAYMENT_PROOFS') and not codes(r,'OVERDUE')


def test_paid_and_canceled_orders_do_not_produce_debt_or_payment_alerts():
    d=sample();d['orders']=[order(outstanding=0,received=580000),order(2,status='CANCELED'),order(3,status='REJECTED')]
    r=report(d);assert not codes(r,'OVERDUE') and not codes(r,'DUE_SOON')


def test_stock_shortage_counts_gifts_and_current_reservations_once():
    d=sample();d['orders']=[order(lines=[dict(id=1,product_id=1,quantity=10,gift=False,shipped=0,reserved=10),dict(id=2,product_id=1,quantity=1,gift=True,shipped=0,reserved=0)])]
    d['inventory'][0].update(quantity=10,reserved=10,available=0)
    r=report(d);a=codes(r,'STOCK_SHORTAGE')[0]
    assert a['quantity']==1 and a['facts']['global_remaining']==11 and a['facts']['global_reserved']==10
    assert r['summary']['shortage_units']==1 and not codes(r,'LOW_STOCK')


def test_competing_unreserved_orders_are_not_assigned_the_same_stock_twice():
    d=sample();d['orders']=[order(oid,lines=[dict(id=oid,product_id=1,quantity=5,gift=False,shipped=0,reserved=0)]) for oid in [1,2]]
    d['inventory'][0].update(quantity=8,available=8)
    r=report(d);assert len(codes(r,'STOCK_SHORTAGE'))==1
    assert codes(r,'STOCK_SHORTAGE')[0]['quantity']==2
    assert codes(r,'STOCK_SHORTAGE')[0]['facts']['related_order_ids']==[1,2]


def test_consignment_inventory_and_stopped_models_are_excluded_from_sellable_stock():
    d=sample();d['inventory'][0].update(quantity=0,available=0)
    d['inventory'].append(dict(warehouse_id=3,product_id=1,warehouse='C1',sku='R6',quantity=100,reserved=0,available=100))
    assert codes(report(d),'LOW_STOCK')[0]['quantity']==0
    d['products'][0]['active']=False
    assert not codes(report(d),'LOW_STOCK')


def test_whole_order_needing_transfer_is_explained_without_creating_a_transfer():
    d=sample();d['orders']=[order(lines=[dict(id=1,product_id=1,quantity=5,gift=False,shipped=0,reserved=0)])]
    d['inventory'][0].update(quantity=2,available=2)
    d['inventory'].append(dict(warehouse_id=2,product_id=1,warehouse='W2',sku='R6',quantity=3,reserved=0,available=3))
    before=deepcopy(d);r=report(d)
    assert len(codes(r,'TRANSFER_NEEDED'))==1 and not codes(r,'STOCK_SHORTAGE')
    assert d==before


def test_paid_active_reservations_remain_usable_after_their_original_expiry():
    d=sample();d['orders']=[order(received=100,outstanding=579900,lines=[dict(id=1,product_id=1,quantity=5,gift=False,shipped=0,reserved=5)])]
    d['inventory'][0].update(quantity=5,reserved=5,available=0)
    reservations=[dict(order_id=1,warehouse_id=1,product_id=1,quantity=5,active=True,expires='2026-11-01T10:00:00+00:00')]
    r=report(d,reservations)
    assert not codes(r,'TRANSFER_NEEDED') and not codes(r,'STOCK_SHORTAGE') and not codes(r,'RESERVATION_EXPIRING')


def test_expiring_reservations_are_grouped_by_order_and_ignore_partial_allocated_receipts():
    d=sample();d['orders']=[order()]
    rows=[dict(order_id=1,warehouse_id=wid,product_id=1,quantity=qty,active=True,expires='2026-11-02T18:00:00+00:00') for wid,qty in [(1,2),(2,3)]]
    r=report(d,rows);assert len(codes(r,'RESERVATION_EXPIRING'))==1
    assert codes(r,'RESERVATION_EXPIRING')[0]['quantity']==5
    d['orders'][0]['received']=1
    assert not codes(report(d,rows),'RESERVATION_EXPIRING')


def test_unverified_recharges_are_claims_not_wallet_balance():
    d=sample();d['recharges']=[dict(id=1,dealer_id=1,amount=20000,status='PENDING',paid_date='2026-11-01',created='2026-11-01T12:00:00+00:00'),dict(id=2,dealer_id=1,amount=99999,status='REJECTED')]
    r=report(d);assert r['summary']['recharge_claimed']==20000
    assert codes(r,'RECHARGE_REVIEW')[0]['facts']['declared_only']
    assert not codes(r,'UNALLOCATED_CREDIT')


def test_unknown_credit_history_is_not_labeled_high_risk():
    d=sample();d['dealers'][0]['credit_rating']=dict(grade='N',score=None)
    assert not codes(report(d),'CREDIT_RISK')


def test_delivery_and_consignment_alerts_follow_actual_states():
    d=sample();d['orders']=[order()]
    d['shipments']=[dict(id=1,order_id=1,status='PENDING_PICKUP',created='2026-10-20T10:00:00+00:00',serials='SN1',exception=''),dict(id=2,order_id=1,status='DELIVERED',created='2026-10-20T10:00:00+00:00')]
    d['reports']=[dict(id=1,dealer_id=1,warehouse_id=3,source_order_id=1,quantity=2,status='PENDING')]
    r=report(d);assert len(codes(r,'DELIVERY_FOLLOWUP'))==1 and len(codes(r,'CONSIGNMENT_REPORT'))==1
    assert '重复计应收' in codes(r,'CONSIGNMENT_REPORT')[0]['advice'][0]


def test_policy_and_negative_inventory_alerts():
    d=sample();d['inventory'][0].update(quantity=-1,available=-1)
    d['policies']=[dict(id=1,name='Ends soon',active=True,ends='2026-11-05'),dict(id=2,name='Disabled',active=False,ends='2026-11-05'),dict(id=3,name='Expired',active=True,ends='2026-11-01')]
    r=report(d);assert len(codes(r,'POLICY_ENDING'))==1
    assert codes(r,'NEGATIVE_STOCK')[0]['quantity']==-1


def test_selected_order_scope_does_not_hide_competing_global_demand():
    d=sample();d['orders']=[order(oid,lines=[dict(id=oid,product_id=1,quantity=5,gift=False,shipped=0,reserved=0)]) for oid in [1,2]]
    d['inventory'][0].update(quantity=8,available=8)
    r=report(d,order_ids={1},dealer_ids={1})
    assert len(codes(r,'OVERDUE'))==1 and r['summary']['overdue_amount']==580000
    a=codes(r,'STOCK_SHORTAGE')[0]
    assert a['quantity']==2 and a['facts']['global_unreserved']==10 and a['facts']['related_order_ids']==[1]


@pytest.mark.parametrize('key,value',[('pending_days',0),('low_stock_units',-1),('urgent_overdue_days',91),('pickup_days','bad'),('reservation_hours',True)])
def test_invalid_rules_rejected(key,value):
    with pytest.raises(HTTPException):settings({key:value})


def test_api_updates_after_approval_without_mutating_business_ledgers(env):
    e=env;e.stock(20);oid=e.legacy_order()
    before=e.data();r=e.admin.get('/api/admin/insights');assert r.status_code==200,r.text
    assert next(a for a in r.json()['alerts'] if a['code']=='ADMIN_REVIEW')['facts']['order_id']==oid
    after=e.data()
    for key in ['orders','inventory','receipts','allocations','shipments','recharges','wallets','audit']:
        assert after[key]==before[key]
    e.review(oid)
    assert not codes(e.admin.get('/api/admin/insights').json(),'ADMIN_REVIEW')


def test_api_scope_validation_and_live_date_filters(env):
    e=env;e.legacy_order()
    assert e.admin.get('/api/admin/insights?scope=invalid').status_code==400
    assert e.admin.get('/api/admin/insights?low_stock_units=1001').status_code==400
    assert e.admin.get('/api/admin/insights?scope=selected&start=2026-12-02&end=2026-12-01').status_code==400
    tomorrow=(today()+timedelta(days=1)).isoformat()
    selected=e.admin.get('/api/admin/insights',params=dict(scope='selected',basis='created',start=tomorrow,end=tomorrow)).json()
    assert not codes(selected,'ADMIN_REVIEW') and selected['filters']['start']==tomorrow
    assert codes(e.admin.get('/api/admin/insights').json(),'ADMIN_REVIEW')
