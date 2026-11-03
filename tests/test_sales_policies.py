"""Monthly policies remain independent of post-order Demo selection."""
import json
from datetime import timedelta
import pytest
from app.core import today


def policy(e,kind='STANDARD',**kw):
    d=dict(name='Monthly '+kind,kind=kind,starts=today().isoformat(),description='Published test policy',active=True)
    d.update(kw)
    return e.act(e.admin,'policy.save',d)['id']


def payload(e,pid=None,**kw):
    d=dict(dealer_id=e.did,sale_type='OTHER',sale_date=today().isoformat(),due=(today()+timedelta(days=30)).isoformat(),terms='Published policy purchase',items=[dict(product_id=e.pid,quantity=1)])
    if pid is not None:d['policy_id']=pid
    d.update(kw)
    return d


def post(e,d,key='policy-order-unique',client=None):
    c=client or e.sales
    return c.post('/api/orders',data={'payload':json.dumps(d)},headers={'X-CSRF-Token':e.data(c)['csrf'],'Idempotency-Key':key})


@pytest.mark.parametrize('role',['sales','dealer','finance','warehouse'])
def test_only_admin_may_create_or_edit_policy(env,role):
    e=env;pid=policy(e)
    e.act(getattr(e,role),'policy.save',dict(name='Forbidden',kind='STANDARD',starts=today().isoformat()),expected=403)
    e.act(getattr(e,role),'policy.save',dict(id=pid,active=False),expected=403)
    assert len(e.data(e.sales)['policies'])==1


def test_sales_see_only_published_current_policies_and_updates(env):
    e=env;active=policy(e);policy(e,active=False);policy(e,starts=(today()+timedelta(days=1)).isoformat());policy(e,starts='2020-01-01',ends='2020-01-31')
    assert [p['id'] for p in e.data(e.sales)['policies']]==[active]
    assert len(e.data()['policies'])==4 and e.data(e.dealer)['policies']==[]
    e.act(e.admin,'policy.save',dict(id=active,name='Updated monthly policy',description='New published instructions'))
    assert e.data(e.sales)['policies'][0]['description']=='New published instructions'
    e.act(e.admin,'policy.save',dict(id=active,active=False))
    assert not e.data(e.sales)['policies']


@pytest.mark.parametrize('kind',['STANDARD','TEN_PLUS_ONE'])
def test_compliant_policy_activates_once_and_warehouse_can_ship(env,kind):
    e=env;e.stock(30);pid=policy(e,kind)
    d=payload(e,pid,items=[dict(product_id=e.pid,quantity=10)])
    r=post(e,d);assert r.status_code==200,r.text
    assert post(e,d).json()==r.json()
    o=e.data()['orders'][0]
    assert o['status']=='CONFIRMED' and not o['admin_review_pending'] and o['confirmed']
    assert o['total']==5800000 and o['policy_name']=='Monthly '+kind and o['policy_id']==pid
    assert o['reserved']==(11 if kind=='TEN_PLUS_ONE' else 10)
    if kind=='TEN_PLUS_ONE':assert [(l['quantity'],l['unit_price'],l['gift']) for l in o['lines']]==[(10,580000,False),(1,0,True)]
    assert not e.data()['receipts'] and not e.data()['allocations']
    e.ship(o['id']);assert e.data()['orders'][0]['status']=='SHIPPED'


def test_standard_price_other_order_also_needs_no_approval(env):
    e=env;r=post(e,payload(e,items=[dict(product_id=e.pid,quantity=1,unit_price='5800.00')]))
    assert r.status_code==200,r.text
    assert e.data()['orders'][0]['status']=='CONFIRMED'


def test_policy_price_exception_requires_admin_every_order(env):
    e=env;pid=policy(e,'TEN_PLUS_ONE');d=payload(e,pid,items=[dict(product_id=e.pid,quantity=1,unit_price='4500.00')])
    r=post(e,d);assert r.status_code==200
    oid=r.json()['id'];o=e.data()['orders'][0]
    assert o['admin_review_pending'] and o['status']=='WAITING_INTERNAL_REVIEW' and not o['confirmed']
    e.act(e.finance,'order.review',dict(order_id=oid),expected=403)
    e.act(e.warehouse,'shipment.create',dict(order_id=oid,warehouse_id=e.wid),expected=409)
    e.act(e.admin,'order.review',dict(order_id=oid))
    assert e.data()['orders'][0]['status']=='CONFIRMED'
    assert post(e,d,key='second-custom-price').status_code==200
    assert e.data()['orders'][0]['status']=='WAITING_INTERNAL_REVIEW'


def test_invalid_policy_or_dealer_scope_cannot_bypass(env):
    e=env;pid=policy(e,active=False)
    assert post(e,payload(e,pid)).status_code==409
    assert post(e,payload(e,99999)).status_code==404
    pid=policy(e)
    assert post(e,payload(e,pid,dealer_id=e.did2)).status_code==403
    assert post(e,payload(e,pid,sale_type='CONSIGNMENT')).status_code==403
    assert post(e,payload(e,promotion='DEMO')).status_code==403
    assert not e.data()['orders']


def test_published_demo_cannot_discount_new_order_automatically(env):
    e=env;pid=policy(e,'DEMO')
    assert post(e,payload(e,pid)).status_code==409
    assert not e.data()['orders']


def test_policy_change_preserves_historical_name_prices_and_rule(env):
    e=env;pid=policy(e,'TEN_PLUS_ONE');post(e,payload(e,pid))
    e.act(e.admin,'policy.save',dict(id=pid,name='Updated next month',description='Latest content'))
    o=e.data()['orders'][0]
    assert o['policy_name']=='Monthly TEN_PLUS_ONE' and o['total']==580000
    e.act(e.admin,'policy.save',dict(id=pid,kind='STANDARD'),expected=409)
    assert e.data()['orders'][0]['total']==580000
