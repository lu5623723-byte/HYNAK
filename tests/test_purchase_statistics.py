from datetime import date
from fastapi.testclient import TestClient
import pytest
from app.db import Session
from app.main import app
from app.models import Order


def confirmed(e,quantity=1,at='2026-10-15T15:00:00+00:00',client=None,**fields):
    oid=e.legacy_order(c=client,quantity=quantity,due='2020-01-01')
    e.review(oid)
    with Session() as db:
        o=db.get(Order,oid);o.confirmed=at
        for key,value in fields.items():setattr(o,key,value)
        db.commit()
    return oid


def summary(e,start='2026-10-01',end='2026-10-31',client=None,**extra):
    return (client or e.dealer).get('/api/purchase-summary',params=dict(start=start,end=end,**extra))


def test_month_and_custom_range_use_sao_paulo_confirmation_dates(env,monkeypatch):
    import app.views as views
    monkeypatch.setattr(views,'today',lambda:date(2026,10,31))
    e=env
    confirmed(e,1,'2026-10-01T02:59:59+00:00')  # September in São Paulo.
    confirmed(e,2,'2026-10-01T03:00:00+00:00')
    confirmed(e,3,'2026-11-01T02:59:59+00:00')
    confirmed(e,4,'2026-11-01T03:00:00+00:00')
    m=e.data(e.dealer)['metrics']
    assert m['month_order_count']==2 and m['month_sales']==2900000
    assert m['month']=='2026-10'
    r=summary(e).json()
    assert r['order_count']==2 and r['total_amount']==2900000 and r['basis']=='confirmed'
    assert summary(e,'2026-10-31','2026-10-31').json()['order_count']==1
    assert summary(e,'2025-01-01','2025-12-31').json()['total_amount']==0


def test_summary_excludes_pending_canceled_opening_and_consignment_dispatch(env):
    e=env
    for state in ['CONFIRMED','PARTIALLY_SHIPPED','SHIPPED','CLOSED']:
        confirmed(e,status=state)
    confirmed(e,status='CANCELED')
    confirmed(e,status='REJECTED')
    confirmed(e,status='EXPIRED')
    confirmed(e,source='OPENING')
    confirmed(e,sale_type='CONSIGNMENT',mode='ASSISTED')
    e.legacy_order()  # Pending internal review.
    e.legacy_order(e.sales)  # Draft.
    oid=e.legacy_order(e.sales)
    e.act(e.sales,'order.submit',dict(order_id=oid));e.review(oid)  # Awaiting customer.
    r=summary(e).json()
    assert r['order_count']==4 and r['total_amount']==2320000


def test_summary_scopes_to_authenticated_dealer_and_preserves_totals_after_payment(env):
    e=env;oid=confirmed(e)
    confirmed(e,quantity=9,client=e.dealer2)
    r=summary(e,dealer_id=e.did2).json()
    assert r['order_count']==1 and r['total_amount']==580000
    assert summary(e,client=e.dealer2).json()['total_amount']==5220000
    rid=e.receipt();e.allocate(rid,[dict(order_id=oid,amount='5800.00')])
    assert summary(e).json()['total_amount']==580000
    assert e.data(e.dealer)['metrics']['outstanding']==0


@pytest.mark.parametrize('start,end',[('','2026-10-31'),('2026-10-01',''),('2026-02-30','2026-10-31'),('2026-11-01','2026-10-01'),('invalid','2026-10-31')])
def test_summary_validates_dates(env,start,end):
    assert summary(env,start,end).status_code==400


@pytest.mark.parametrize('role',['admin','sales','warehouse','finance'])
def test_dealer_summary_does_not_expose_dealer_reporting_to_other_roles(env,role):
    assert summary(env,client=getattr(env,role)).status_code==403


def test_summary_requires_login(env):
    with TestClient(app) as c:
        assert c.get('/api/purchase-summary',params=dict(start='2026-10-01',end='2026-10-31')).status_code==401
