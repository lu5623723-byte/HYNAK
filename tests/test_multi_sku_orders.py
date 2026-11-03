"""Dealer, sales and admin orders retain each SKU/quantity through submission."""
import json
import pytest
from test_finance_activation import PROOF


def payload(e,kind='CASH'):
    pid=e.act(e.admin,'product.save',dict(sku='SECOND',name='Second model',price='100.00'))['id']
    return dict(dealer_id=e.did,sale_type=kind,sale_date='2026-11-01',due='2026-11-30',payment_at='2026-11-01T09:00',terms='Isolated multi-SKU test',items=[dict(product_id=e.pid,quantity=2),dict(product_id=pid,quantity=3)])


def post(e,role,d,key='multi-sku-order'):
    c=getattr(e,role)
    return c.post('/api/orders',data={'payload':json.dumps(d)},files={'file':('test.pdf',PROOF,'application/pdf')} if d['sale_type']=='CASH' else None,headers={'X-CSRF-Token':e.data(c)['csrf'],'Idempotency-Key':key})


@pytest.mark.parametrize('role',['dealer','sales','admin'])
def test_multi_sku_cash_order_upload_total_finance_and_reservations(env,role):
    e=env;d=payload(e);e.stock(10)
    e.act(e.warehouse,'stock.move',dict(kind='RECEIVE',warehouse_id=e.wid,items=[dict(product_id=d['items'][1]['product_id'],quantity=10)],reason='Isolated test stock'))
    r=post(e,role,d);assert r.status_code==200,r.text
    assert post(e,role,d).json()==r.json()
    created=r.json();before=e.data();o=before['orders'][0]
    assert len(before['orders'])==len(before['claims'])==1
    assert [(l['product_id'],l['quantity']) for l in o['lines']]==[(d['items'][0]['product_id'],2),(d['items'][1]['product_id'],3)]
    assert o['total']==1190000 and before['claims'][0]['amount']==1190000
    assert o['status']=='WAITING_FINANCE_REVIEW' and o['receivable']==0
    e.act(e.finance,'receipt.create',dict(dealer_id=e.did,account_id=e.aid,claim_id=created['claim_id'],amount='11900.00',date='2026-11-01',reference='MULTI-SKU-CASH'))
    o=e.data()['orders'][0]
    assert o['status']=='CONFIRMED' and o['received']==1190000 and o['outstanding']==0
    assert [l['reserved'] for l in o['lines']]==[2,3]


@pytest.mark.parametrize('role',['sales','admin'])
@pytest.mark.parametrize('kind',['CONSIGNMENT','OTHER'])
def test_multi_sku_non_cash_order_retains_both_lines(env,role,kind):
    e=env;d=payload(e,kind);r=post(e,role,d)
    assert r.status_code==200,r.text
    o=e.data()['orders'][0]
    assert len(o['lines'])==2 and [l['quantity'] for l in o['lines']]==[2,3]
    assert o['total']==1190000
    assert o['status']==('CONFIRMED' if kind=='OTHER' else 'DRAFT' if role=='admin' else 'WAITING_INTERNAL_REVIEW')


@pytest.mark.parametrize('role',['dealer','sales','admin'])
@pytest.mark.parametrize('invalid',['duplicate','zero_quantity'])
def test_invalid_second_sku_rejects_whole_order_and_claim(env,role,invalid):
    e=env;d=payload(e)
    if invalid=='duplicate':d['items'][1]['product_id']=e.pid
    else:d['items'][1]['quantity']=0
    r=post(e,role,d);assert r.status_code==400,r.text
    assert not e.data()['orders'] and not e.data()['claims']
