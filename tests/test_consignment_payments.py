"""Whole-order consignment balances and shared, bank-dated payment histories."""
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select,func
from app.db import Session
from app.models import Order,Receivable,Allocation,ConsignmentReport
from test_finance_activation import PROOF
from test_sales_details import approve_consignment


def row(e,oid,actor=None):return next(o for o in e.data(actor)['orders'] if o['id']==oid)


def consign(e,role='sales',dealer_id=None,qty=1):
    e.n+=1;c=getattr(e,role)
    d=dict(dealer_id=dealer_id or e.did,sale_type='CONSIGNMENT',sale_date='2026-10-31',due='2026-11-30',items=[dict(product_id=e.pid,quantity=qty)])
    r=c.post('/api/orders',data={'payload':json.dumps(d)},headers={'X-CSRF-Token':e.data(c)['csrf'],'Idempotency-Key':f'consignment-payment-order-{e.n}'})
    assert r.status_code==200,r.text
    oid=r.json()['id']
    if role=='sales':e.review(oid)
    else:approve_consignment(e,oid)
    return oid


def pay(e,oid,amount='1000.00',date='2026-11-01',ref=None,actor=None,**kw):
    return e.act(actor or e.finance,'receipt.create',dict(order_id=oid,dealer_id=e.did,account_id=e.aid,amount=amount,date=date,reference=ref or f'PAY-{e.n}'),**kw)


@pytest.mark.parametrize('creator',['sales','admin'])
def test_consignment_activation_creates_full_receivable_without_bank_cash(env,creator):
    e=env;e.stock(5);oid=consign(e,creator,qty=2);o=row(e,oid)
    assert o['consignment_full_settlement'] and o['total']==o['receivable']==o['outstanding']==1160000
    assert o['received']==o['payment_count']==0 and not o['payment_history']
    assert o['reserved']==2 and not e.data()['receipts'] and e.data()['metrics']['verified']==0


@pytest.mark.parametrize('viewer',['admin','finance','sales','dealer'])
def test_two_payment_dates_and_remaining_balance_are_identical_in_four_roles(env,viewer):
    e=env;oid=consign(e)
    # Backdated second entry must be displayed in bank-date order, not entry order.
    r1=pay(e,oid,'1800.00',date='2026-11-01',ref='PRIVATE-BANK-SECOND')['id']
    r2=pay(e,oid,'2000.00',date='2026-10-31',ref='PRIVATE-BANK-FIRST')['id']
    o=row(e,oid,getattr(e,viewer))
    assert o['receivable']==580000 and o['received']==380000 and o['outstanding']==200000
    assert o['payment_status']=='PARTIAL' and o['payment_count']==2
    assert [(p['sequence'],p['paid_date'],p['amount'],p['receipt_id']) for p in o['payment_history']]==[(1,'2026-10-31',200000,r2),(2,'2026-11-01',180000,r1)]
    assert not any(k in p for p in o['payment_history'] for k in ['reference','account_id','actor_id'])


def test_upload_is_unverified_until_bank_check_then_auto_applies_to_this_order(env):
    e=env;oid=consign(e)
    c=e.dealer.post('/api/claims',data=dict(order_id=oid,amount='3000.00',paid_date='2026-10-31',reference='CLAIM-NOT-CASH'),files={'file':('proof.pdf',PROOF,'application/pdf')},headers={'X-CSRF-Token':e.data(e.dealer)['csrf']})
    assert c.status_code==200,c.text
    cid=c.json()['id'];assert row(e,oid)['outstanding']==580000 and not row(e,oid)['payment_history']
    e.act(e.finance,'receipt.create',dict(claim_id=cid,dealer_id=e.did,account_id=e.aid,amount='2000.00',date='2026-11-01',reference='ACTUAL-BANK-AMOUNT'))
    o=row(e,oid);assert o['received']==200000 and o['outstanding']==380000 and o['payment_count']==1
    assert o['payment_history'][0]['paid_date']=='2026-11-01'
    assert e.dealer.get(f'/api/claims/{cid}/attachment').status_code==200


@pytest.mark.parametrize('role',['admin','finance'])
def test_authorized_bank_check_overpayment_becomes_credit_and_is_idempotent(env,role):
    e=env;oid=consign(e)
    a=pay(e,oid,'6000.00',ref='OVERPAYMENT',actor=getattr(e,role),key='consign-payment-idempotent')
    assert pay(e,oid,'6000.00',ref='OVERPAYMENT',actor=getattr(e,role),key='consign-payment-idempotent')==a
    o=row(e,oid);assert o['received']==580000 and o['outstanding']==0 and o['payment_status']=='PAID'
    assert o['payment_count']==1 and o['payment_history'][0]['amount']==580000
    assert e.data(e.dealer)['metrics']['credit']==20000
    pay(e,oid,'6000.00',ref='OVERPAYMENT',expected=409)


@pytest.mark.parametrize('role',['sales','dealer','warehouse'])
def test_readers_cannot_register_verified_payments(env,role):
    e=env;oid=consign(e);pay(e,oid,actor=getattr(e,role),expected=403)
    assert not row(e,oid)['payment_history']


def test_unallocated_receipt_does_not_reduce_order_balance_and_split_allocations_merge(env):
    e=env;oid=consign(e);rid=e.receipt('2000.00')
    assert row(e,oid)['outstanding']==580000 and not row(e,oid)['payment_history']
    e.allocate(rid,[dict(order_id=oid,amount='500.00')]);e.allocate(rid,[dict(order_id=oid,amount='1500.00')])
    o=row(e,oid);assert o['outstanding']==380000 and o['payment_count']==1
    assert o['payment_history'][0]['amount']==200000


def test_order_and_claim_scope_cannot_be_changed_to_another_dealer(env):
    e=env;oid=consign(e);other=consign(e,'admin',e.did2)
    pay(e,other,expected=403)
    assert all(o['id']!=other for o in e.data(e.sales)['orders'])
    assert all(o['id']!=oid for o in e.data(e.dealer2)['orders'])
    c=e.dealer.post('/api/claims',data=dict(order_id=oid,amount='1.00',paid_date='2026-11-01'),files={'file':('proof.pdf',PROOF,'application/pdf')},headers={'X-CSRF-Token':e.data(e.dealer)['csrf']})
    assert c.status_code==200
    e.act(e.finance,'receipt.create',dict(order_id=other,claim_id=c.json()['id'],dealer_id=e.did2,account_id=e.aid,amount='1.00',date='2026-11-01',reference='BAD-CLAIM-TARGET'),expected=403)
    assert not e.data()['receipts']


def test_sold_report_uses_original_order_without_second_receivable(env):
    e=env;e.stock(10);oid=consign(e,qty=3)
    sid=e.ship(oid,consignment_location_id=e.cwid)
    e.act(e.warehouse,'shipment.action',dict(shipment_id=sid,action='DELIVER'))
    e.act(e.dealer,'consignment.report',dict(warehouse_id=e.cwid,product_id=e.pid,quantity=1),expected=409)
    rid=e.act(e.dealer,'consignment.report',dict(source_order_id=oid,warehouse_id=e.cwid,product_id=e.pid,quantity=1))['id']
    for _ in range(2):assert e.act(e.admin,'consignment.confirm',dict(report_id=rid))['id']==oid
    assert len(e.data()['orders'])==1 and row(e,oid)['receivable']==1740000
    with Session() as db:assert db.scalar(select(func.count(Receivable.id)))==1
    assert next(i['quantity'] for i in e.data()['inventory'] if i['warehouse_id']==e.cwid)==2


def test_report_cannot_claim_stock_of_another_original_order(env):
    e=env;e.stock(10);a=consign(e);b=consign(e)
    e.ship(a,consignment_location_id=e.cwid)
    e.act(e.dealer,'consignment.report',dict(source_order_id=b,warehouse_id=e.cwid,product_id=e.pid,quantity=1),expected=409)
    assert not e.data()['reports']


def test_bank_payment_cannot_target_pending_or_canceled_order(env):
    e=env;oid=consign(e);e.act(e.admin,'order.cancel',dict(order_id=oid,reason='Test cancellation'))
    pay(e,oid,expected=409)
    assert not e.data()['receipts']


def test_concurrent_distinct_payments_allocate_only_remaining_and_keep_excess_credit(env):
    e=env;oid=consign(e);csrf=e.data(e.finance)['csrf']
    def receive(n):
        return e.finance.post('/api/action/receipt.create',json=dict(order_id=oid,dealer_id=e.did,account_id=e.aid,amount='4000.00',date='2026-11-01',reference=f'CONCURRENT-{n}'),headers={'X-CSRF-Token':csrf,'Idempotency-Key':f'consignment-concurrent-{n}'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(receive,[1,2]))
    assert codes==[200,200]
    assert row(e,oid)['received']==580000 and row(e,oid)['payment_count']==2
    assert e.data()['metrics']['credit']==220000
