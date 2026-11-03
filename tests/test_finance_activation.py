"""Current cash orders activate only through verified, order-linked bank funds."""
import json
import pytest
from app.db import Session
from app.models import Order,ConfirmationToken
from app.core import digest,future

PROOF=b'%PDF-1.4\n% Isolated fictional payment proof\n%%EOF'

def create(e,role='dealer',**changes):
    c=getattr(e,role)
    payload=dict(dealer_id=e.did,sale_type='CASH',sale_date='2026-10-31',payment_at='2026-10-31T09:00',items=[dict(product_id=e.pid,quantity=1)],**changes)
    r=c.post('/api/orders',data={'payload':json.dumps(payload)},files={'file':('test.pdf',PROOF,'application/pdf')},headers={'X-CSRF-Token':e.data(c)['csrf'],'Idempotency-Key':f'new-cash-{role}-{e.n}'})
    assert r.status_code==200,r.text
    return r.json()

def verify(e,cid,amount='5800.00',ref='BANK-TEST',actor=None,key=None,expected=200):
    return e.act(actor or e.finance,'receipt.create',dict(dealer_id=e.did,account_id=e.aid,claim_id=cid,amount=amount,date='2026-10-31',reference=ref),key=key,expected=expected)

@pytest.mark.parametrize('role',['dealer','sales'])
def test_finance_full_receipt_activates_and_allocates_once(env,role):
    e=env;e.stock(10);created=create(e,role);oid,cid=created['id'],created['claim_id']
    before=e.data();o=before['orders'][0]
    assert o['status']=='WAITING_FINANCE_REVIEW' and o['payment_status']=='VERIFYING'
    assert o['receivable']==0 and o['reserved']==0 and o['confirmed'] is None
    assert e.data(e.dealer)['metrics']['month_order_count']==0
    e.act(e.admin,'order.review',dict(order_id=oid),expected=403)
    e.act(e.sales,'order.token',dict(order_id=oid),expected=403)
    r=verify(e,cid,key='verify-full-order')
    again=verify(e,cid,key='verify-full-order')
    assert r==again
    after=e.data(e.dealer);o=after['orders'][0]
    assert o['status']=='CONFIRMED' and o['payment_status']=='PAID'
    assert o['received']==o['receivable']==580000 and o['outstanding']==0 and o['reserved']==1
    assert len(after['allocations'])==1 and after['metrics']['month_order_count']==1
    verify(e,cid,ref='BANK-TEST',expected=409)


def test_partial_payments_wait_then_overpayment_becomes_credit(env):
    e=env;created=create(e);cid=created['claim_id']
    verify(e,cid,'3000.00','PART-1')
    d=e.data(e.dealer)
    assert d['orders'][0]['status']=='WAITING_FINANCE_REVIEW'
    assert d['orders'][0]['receivable']==0 and d['orders'][0]['confirmed'] is None
    assert d['metrics']['month_order_count']==0 and d['metrics']['credit']==300000
    verify(e,cid,'3000.00','REUSED-PROOF',expected=409)
    response=e.dealer.post('/api/claims',data=dict(order_id=created['id'],amount='3000.00',paid_date='2026-10-31',reference='ADDITIONAL-TRANSFER'),files={'file':('second.pdf',PROOF,'application/pdf')},headers={'X-CSRF-Token':e.data(e.dealer)['csrf']})
    assert response.status_code==200,response.text
    verify(e,response.json()['id'],'3000.00','PART-2')
    d=e.data(e.dealer)
    assert d['orders'][0]['status']=='CONFIRMED' and d['orders'][0]['outstanding']==0
    assert d['metrics']['verified']==600000 and d['metrics']['credit']==20000
    assert len(d['allocations'])==2


@pytest.mark.parametrize('role',['admin','dealer','sales','warehouse'])
def test_only_finance_may_verify_pending_cash_order(env,role):
    e=env;created=create(e)
    verify(e,created['claim_id'],actor=getattr(e,role),expected=403)
    d=e.data();assert d['orders'][0]['status']=='WAITING_FINANCE_REVIEW'
    assert not d['receipts'] and not d['allocations']


def test_existing_customer_link_and_admin_type_change_cannot_bypass_finance(env):
    e=env;created=create(e,'sales');oid=created['id'];raw='old-cash-confirmation-token'
    with Session() as db:
        o=db.get(Order,oid);o.status='WAITING_DEALER_CONFIRMATION'
        db.add(ConfirmationToken(hash=digest(raw),order_id=oid,version=o.version,expires=future(24)))
        db.commit()
    assert e.dealer.post('/api/quote/'+raw,json={'action':'CONFIRM'}).status_code==403
    e.act(e.admin,'order.revise',dict(order_id=oid,sale_type='WHOLESALE',due='2026-11-30',reason='Attempt to bypass',items=[dict(product_id=e.pid,quantity=1)]),expected=403)
    assert e.data()['orders'][0]['confirmed'] is None
    verify(e,created['claim_id'])
    assert e.data()['orders'][0]['status']=='CONFIRMED'


def test_unlinked_receipt_and_other_dealer_cannot_activate_order(env):
    e=env;created=create(e);oid=created['id']
    rid=e.receipt()
    assert e.data()['orders'][0]['status']=='WAITING_FINANCE_REVIEW'
    e.allocate(rid,[dict(order_id=oid,amount='5800.00')],expected=400)
    e.act(e.finance,'receipt.create',dict(dealer_id=e.did2,account_id=e.aid,claim_id=created['claim_id'],amount='5800.00',date='2026-10-31',reference='WRONG-DEALER'),expected=409)
    assert not e.data()['allocations']


def test_revision_and_cancel_cannot_discard_partially_verified_cash(env):
    e=env;created=create(e);verify(e,created['claim_id'],'1000.00','PARTIAL')
    e.act(e.admin,'order.revise',dict(order_id=created['id'],reason='Change price',promotion='DEMO',items=[dict(product_id=e.pid,quantity=1)]),expected=409)
    e.act(e.admin,'order.cancel',dict(order_id=created['id'],reason='Cancel'),expected=409)
    assert e.data()['metrics']['verified']==100000


def test_sales_cannot_create_legacy_wholesale_to_bypass_finance(env):
    e=env
    payload=dict(dealer_id=e.did,sale_type='WHOLESALE',due='2026-11-30',items=[dict(product_id=e.pid,quantity=1)])
    e.act(e.sales,'order.create',payload,expected=403)
    r=e.sales.post('/api/orders',data={'payload':json.dumps(payload)},headers={'X-CSRF-Token':e.data(e.sales)['csrf'],'Idempotency-Key':'sales-wholesale-bypass'})
    assert r.status_code==403
    assert e.data()['orders']==[]
