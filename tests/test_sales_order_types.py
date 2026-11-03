"""Sales can submit consignment and other orders for administrator approval."""
import json
import pytest


def payload(e,kind,**changes):
    return dict(dealer_id=e.did,sale_type=kind,sale_date='2026-10-31',due='2026-11-30',terms='Other: agreed credit purchase',items=[dict(product_id=e.pid,quantity=1)],**changes)


def post(e,d,key='sales-noncash-test'):
    return e.sales.post('/api/orders',data={'payload':json.dumps(d)},headers={'X-CSRF-Token':e.data(e.sales)['csrf'],'Idempotency-Key':key})


@pytest.mark.parametrize('kind',['CONSIGNMENT'])
def test_sales_noncash_admin_approval_controls_effects(env,kind):
    e=env;e.stock(5);d=payload(e,kind)
    r=post(e,d);assert r.status_code==200,r.text
    oid=r.json()['id'];assert post(e,d).json()==r.json()
    o=e.data()['orders'][0]
    assert o['status']=='WAITING_INTERNAL_REVIEW' and o['sale_type']==kind
    assert o['confirmed'] is None and o['receivable']==o['reserved']==0
    e.act(e.sales,'order.token',dict(order_id=oid),expected=409)
    e.act(e.admin,'order.revise',{**d,'order_id':oid,'sale_type':'WHOLESALE','reason':'Bypass'},expected=403)
    e.act(e.admin,'order.revise',{**d,'order_id':oid,'reason':'Clarify notes'})
    assert e.data()['orders'][0]['status']=='WAITING_INTERNAL_REVIEW'
    for role in ['finance','sales','dealer','warehouse']:
        e.act(getattr(e,role),'order.review',dict(order_id=oid),expected=403)
    e.act(e.admin,'order.review',dict(order_id=oid,reason='Information checked'),key='finance-approval')
    e.act(e.admin,'order.review',dict(order_id=oid,reason='Information checked'),key='finance-approval')
    o=e.data()['orders'][0]
    assert o['status']=='CONFIRMED' and o['confirmed'] and o['reserved']==1
    assert o['receivable']==580000
    assert e.data()['metrics']['verified']==0 and not e.data()['allocations']
    e.act(e.admin,'order.review',dict(order_id=oid),expected=409)


@pytest.mark.parametrize('kind',['CONSIGNMENT','OTHER'])
@pytest.mark.parametrize('field',['sale_date','due'])
def test_sales_noncash_requires_dates(env,kind,field):
    e=env;d=payload(e,kind);d[field]=''
    assert post(e,d).status_code==400
    assert not e.data()['orders']


def test_other_requires_notes_and_sales_cannot_order_for_another_owner(env):
    e=env;d=payload(e,'OTHER');d['terms']='   '
    assert post(e,d).status_code==400
    d=payload(e,'OTHER');d['dealer_id']=e.did2
    assert post(e,d).status_code==403
    assert not e.data()['orders']


def test_admin_can_reject_without_creating_receivable_or_reservation(env):
    e=env;d=payload(e,'OTHER');d['items'][0]['unit_price']='5000.00';oid=post(e,d).json()['id']
    e.act(e.admin,'order.review',dict(order_id=oid,reject=True),expected=400)
    e.act(e.admin,'order.review',dict(order_id=oid,reject=True,reason='Incomplete agreement'))
    o=e.data()['orders'][0]
    assert o['status']=='REJECTED' and not o['confirmed']
    assert o['receivable']==o['reserved']==0


def test_finance_non_cash_review_cannot_replace_cash_receipt_verification(env):
    from test_finance_activation import create
    e=env;oid=create(e,'sales')['id']
    e.act(e.admin,'order.review',dict(order_id=oid),expected=403)
    assert e.data()['orders'][0]['status']=='WAITING_FINANCE_REVIEW'


def test_custom_cash_price_needs_admin_approval_before_finance_can_verify(env):
    from test_finance_activation import PROOF,verify
    e=env;d=payload(e,'CASH');d['payment_at']='2026-10-31T09:00';d['items'][0]['unit_price']='5000.00'
    r=e.sales.post('/api/orders',data={'payload':json.dumps(d)},files={'file':('test.pdf',PROOF,'application/pdf')},headers={'X-CSRF-Token':e.data(e.sales)['csrf'],'Idempotency-Key':'sales-custom-cash'})
    assert r.status_code==200,r.text
    created=r.json()
    oid=created['id'];o=e.data()['orders'][0]
    assert o['total']==500000 and o['admin_review_pending']
    assert o['status']=='WAITING_INTERNAL_REVIEW' and o['confirmed'] is None
    verify(e,created['claim_id'],'5000.00',expected=409)
    assert not e.data()['receipts']
    e.act(e.sales,'order.review',dict(order_id=oid),expected=403)
    e.act(e.admin,'order.review',dict(order_id=oid),key='approve-custom-price')
    e.act(e.admin,'order.review',dict(order_id=oid),key='approve-custom-price')
    o=e.data()['orders'][0]
    assert not o['admin_review_pending'] and o['status']=='WAITING_FINANCE_REVIEW'
    assert o['confirmed'] is None and o['receivable']==0
    verify(e,created['claim_id'],'5000.00')
    o=e.data()['orders'][0]
    assert o['status']=='CONFIRMED' and o['received']==500000 and o['outstanding']==0


@pytest.mark.parametrize('kind',['CONSIGNMENT','OTHER'])
def test_custom_non_cash_price_activates_only_after_admin_approval(env,kind):
    e=env;d=payload(e,kind);d['items'][0]['unit_price']='4999.99'
    oid=post(e,d).json()['id']
    assert e.data()['orders'][0]['total']==499999
    e.act(e.finance,'order.review',dict(order_id=oid),expected=403)
    e.act(e.admin,'order.review',dict(order_id=oid))
    assert e.data()['orders'][0]['status']=='CONFIRMED'


def test_dealer_custom_price_remains_forbidden(env):
    from test_finance_activation import PROOF
    e=env;d=payload(e,'CASH');d['payment_at']='2026-10-31T09:00';d['items'][0]['unit_price']='1.00'
    r=e.dealer.post('/api/orders',data={'payload':json.dumps(d)},files={'file':('test.pdf',PROOF,'application/pdf')},headers={'X-CSRF-Token':e.data(e.dealer)['csrf'],'Idempotency-Key':'dealer-forged-custom'})
    assert r.status_code==403 and not e.data()['orders']
