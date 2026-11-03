"""Post-order, staff-selected Demo; real cash/ledger/ownership boundaries."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select, text
from app.db import Session,engine,migrate
from app.models import Order,OrderLine,Audit
from test_finance_activation import PROOF,verify
import json


def cash(e,role='dealer',**kw):
    e.n+=1
    c=getattr(e,role)
    d=dict(dealer_id=e.did,sale_type='CASH',sale_date='2026-10-31',payment_at='2026-10-31T09:00',items=[dict(product_id=e.pid,quantity=1)])
    d.update(kw)
    r=c.post('/api/orders',data={'payload':json.dumps(d)},files={'file':('test.pdf',PROOF,'application/pdf')},headers={'X-CSRF-Token':e.data(c)['csrf'],'Idempotency-Key':f'demo-cash-{e.n}'})
    assert r.status_code==200,r.text
    return r.json()


def row(e,oid):return next(o for o in e.data()['orders'] if o['id']==oid)


def demo(e,oid,ids=None,actor=None,**kw):
    o=row(e,oid)
    return e.act(actor or e.sales,'order.demo',dict(order_id=oid,version=o['version'],product_ids=ids if ids is not None else [e.pid]),**kw)


@pytest.mark.parametrize('role',['sales','admin'])
def test_dealer_normal_price_then_staff_selects_one_model_only(env,role):
    e=env;p2=e.act(e.admin,'product.save',dict(sku='P2',name='Second',price='100.01'))['id']
    made=cash(e,items=[dict(product_id=e.pid,quantity=2),dict(product_id=p2,quantity=3)])
    o=row(e,made['id']);assert o['total']==1190003 and not any(l['demo_applied'] for l in o['lines'])
    demo(e,o['id'],[p2],actor=getattr(e,role));o=row(e,o['id'])
    assert [(l['unit_price'],l['demo_applied']) for l in o['lines']]==[(580000,False),(8001,True)]
    assert o['total']==1184003 and o['status']=='WAITING_FINANCE_REVIEW' and not o['admin_review_pending']
    assert not o['confirmed'] and not o['receivable']
    assert e.data(e.dealer)['claims'][0]['amount']==1190003  # Actual proof is not rewritten.


def test_cash_demo_keeps_finance_check_and_overpayment_credit(env):
    e=env;e.stock(10);made=cash(e);demo(e,made['id'])
    e.act(e.admin,'order.review',dict(order_id=made['id']),expected=403)
    verify(e,made['claim_id'],ref='DEMO-OVERPAID')
    o=row(e,made['id']);assert o['total']==o['receivable']==o['received']==464000
    assert o['status']=='CONFIRMED' and o['reserved']==1
    assert e.data(e.dealer)['metrics']['credit']==116000
    demo(e,o['id'],[],expected=409)


def test_verified_partial_receipt_prevents_repricing(env):
    e=env;made=cash(e);verify(e,made['claim_id'],'1000.00','PARTIAL-BEFORE-DEMO')
    demo(e,made['id'],expected=409)
    assert row(e,made['id'])['total']==580000


@pytest.mark.parametrize('role',['dealer','finance','warehouse'])
def test_only_sales_and_admin_can_set_demo(env,role):
    e=env;made=cash(e);demo(e,made['id'],actor=getattr(e,role),expected=403)
    assert not row(e,made['id'])['lines'][0]['demo_applied']


def test_sales_cannot_set_another_dealers_demo(env):
    e=env;made=cash(e,role='admin',dealer_id=e.did2)
    demo(e,made['id'],expected=403)


def test_historical_used_model_blocked_but_new_model_and_dealer_allowed(env):
    e=env;old=e.legacy_order(promotion='DEMO');e.review(old)
    p2=e.act(e.admin,'product.save',dict(sku='NEW',name='New model',price='200.00'))['id']
    made=cash(e,items=[dict(product_id=e.pid,quantity=1),dict(product_id=p2,quantity=1)])
    demo(e,made['id'],[e.pid,p2],expected=409)
    assert row(e,made['id'])['total']==600000 and not any(l['demo_applied'] for l in row(e,made['id'])['lines'])
    demo(e,made['id'],[p2]);assert row(e,made['id'])['total']==596000
    other=cash(e,role='admin',dealer_id=e.did2)
    demo(e,other['id'],actor=e.admin);assert row(e,other['id'])['total']==464000


def test_prior_full_price_purchase_does_not_use_demo_opportunity(env):
    e=env;first=cash(e);verify(e,first['claim_id'],ref='NORMAL-PRICE')
    second=cash(e);demo(e,second['id'])
    assert row(e,second['id'])['total']==464000


def test_pending_selection_blocks_second_order_and_unchecking_releases(env):
    e=env;a=cash(e);b=cash(e);demo(e,a['id'])
    demo(e,b['id'],expected=409)
    demo(e,a['id'],[]);assert row(e,a['id'])['total']==580000
    demo(e,b['id']);assert row(e,b['id'])['total']==464000


def test_rejected_pending_or_canceled_effective_order_releases(env):
    e=env;first=e.legacy_order();demo(e,first);e.review(first)
    second=cash(e);demo(e,second['id'],expected=409)
    e.act(e.admin,'order.cancel',dict(order_id=first,reason='Whole order canceled'))
    demo(e,second['id']);e.act(e.admin,'order.cancel',dict(order_id=second['id'],reason='Pending order canceled'))
    third=cash(e);demo(e,third['id'])


def test_two_orders_concurrently_compete_for_one_demo(env):
    e=env;a=cash(e);b=cash(e);csrf=e.data(e.sales)['csrf']
    def choose(oid):
        return e.sales.post('/api/action/order.demo',json=dict(order_id=oid,version=1,product_ids=[e.pid]),headers={'X-CSRF-Token':csrf,'Idempotency-Key':f'demo-race-{oid}'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(choose,[a['id'],b['id']]))
    assert sorted(codes)==[200,409]
    assert sum(o['lines'][0]['demo_applied'] for o in e.data()['orders'])==1


def test_demo_selection_is_versioned_and_idempotent(env):
    e=env;made=cash(e);oid=made['id'];d=dict(order_id=oid,version=1,product_ids=[e.pid])
    a=e.act(e.sales,'order.demo',d,key='demo-idempotent')
    assert e.act(e.sales,'order.demo',d,key='demo-idempotent')==a
    e.act(e.sales,'order.demo',{**d,'product_ids':[]},expected=409)
    assert row(e,oid)['version']==2
    with Session() as db:assert len(db.scalars(select(Audit).where(Audit.action=='ORDER_DEMO_SET')).all())==1


@pytest.mark.parametrize('ids',[[99999],[True],[1,1],'not-a-list'])
def test_invalid_selection_rolls_back(env,ids):
    e=env;made=cash(e);demo(e,made['id'],ids,expected=400)
    assert row(e,made['id'])['version']==1 and row(e,made['id'])['total']==580000


def test_demo_does_not_approve_unrelated_custom_price(env):
    e=env;p2=e.act(e.admin,'product.save',dict(sku='P2',name='Second',price='100.00'))['id']
    made=cash(e,role='sales',items=[dict(product_id=e.pid,quantity=1),dict(product_id=p2,quantity=1,unit_price='75.00')])
    demo(e,made['id']);o=row(e,made['id'])
    assert o['total']==471500 and o['admin_review_pending'] and o['status']=='WAITING_INTERNAL_REVIEW'
    demo(e,made['id'],[e.pid,p2]);o=row(e,made['id'])
    assert o['total']==472000 and not o['admin_review_pending'] and o['status']=='WAITING_FINANCE_REVIEW'


def test_admin_revision_retains_per_model_demo_and_checks_gift_stacking(env):
    e=env;made=cash(e);demo(e,made['id'])
    d=dict(order_id=made['id'],reason='Update quantity',items=[dict(product_id=e.pid,quantity=2)])
    e.act(e.admin,'order.revise',d)
    o=row(e,made['id']);assert o['total']==928000 and o['lines'][0]['demo_applied']
    e.act(e.admin,'order.revise',{**d,'promotion':'TEN_PLUS_ONE'},expected=400)
    assert row(e,made['id'])['total']==928000


def test_migration_preserves_historical_demo_prices_and_usage(env):
    e=env;oid=e.legacy_order(promotion='DEMO');e.review(oid)
    with engine.begin() as conn:
        conn.execute(text('DELETE FROM schema_versions WHERE version=7'))
        conn.execute(text('ALTER TABLE order_lines DROP COLUMN demo_applied'))
    migrate();migrate()
    assert row(e,oid)['total']==464000 and row(e,oid)['lines'][0]['demo_applied']
    made=cash(e);demo(e,made['id'],expected=409)
