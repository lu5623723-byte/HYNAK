"""Manual receipt allocation is available to administrators and finance."""
import pytest
from sqlalchemy import select
from app.db import Session
from app.models import Allocation
from test_finance_activation import create


@pytest.mark.parametrize('role',['admin','finance'])
def test_staff_allocate_verified_receipt_once_with_balance_limits(env,role):
    e=env;actor=getattr(e,role)
    oid=e.legacy_order();e.review(oid);rid=e.receipt('1000.00')
    payload=dict(dealer_id=e.did,receipt_id=rid,items=[dict(order_id=oid,amount='600.00')])
    e.act(actor,'receipt.allocate',payload,key='manual-allocation')
    e.act(actor,'receipt.allocate',payload,key='manual-allocation')
    d=e.data();o=next(o for o in d['orders'] if o['id']==oid)
    assert o['received']==60000 and o['outstanding']==520000
    assert len(d['allocations'])==1
    with Session() as db:
        assert db.scalar(select(Allocation)).actor_id==e.data(actor)['user']['id']
    payload['items'][0]['amount']='400.01'
    e.act(actor,'receipt.allocate',payload,expected=400)
    payload['dealer_id']=e.did2
    e.act(actor,'receipt.allocate',payload,expected=400)
    assert len(e.data()['allocations'])==1


@pytest.mark.parametrize('role',['dealer','sales','warehouse'])
def test_other_roles_cannot_manually_allocate(env,role):
    e=env;oid=e.legacy_order();e.review(oid);rid=e.receipt()
    e.act(getattr(e,role),'receipt.allocate',dict(dealer_id=e.did,receipt_id=rid,items=[dict(order_id=oid,amount='1.00')]),expected=403)
    assert not e.data()['allocations']


def test_admin_allocation_cannot_activate_pending_cash_order(env):
    e=env;oid=create(e)['id'];rid=e.receipt()
    e.act(e.admin,'receipt.allocate',dict(dealer_id=e.did,receipt_id=rid,items=[dict(order_id=oid,amount='5800.00')]),expected=400)
    d=e.data();o=next(o for o in d['orders'] if o['id']==oid)
    assert o['status']=='WAITING_FINANCE_REVIEW' and o['confirmed'] is None
    assert not d['allocations'] and o['receivable']==0
