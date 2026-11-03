from datetime import date
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import Session
from app.models import Order, OrderLine, Receipt, Claim
from sqlalchemy import select


def confirmed(e, at='2026-10-15T15:00:00+00:00', client=None, quantity=1, **fields):
    oid = e.legacy_order(c=client, quantity=quantity, due='2026-10-20')
    e.review(oid)
    with Session() as db:
        o = db.get(Order, oid)
        o.confirmed = at
        o.created = '2026-09-01T15:00:00+00:00'
        o.sale_date = '2026-09-10'
        for key, value in fields.items():
            setattr(o, key, value)
        db.commit()
    return oid


def summary(e, client=None, **filters):
    return (client or e.admin).get('/api/dashboard-summary', params={'start':'2026-10-01','end':'2026-10-31',**filters})


def test_balances_are_current_cohort_balances_receipts_are_actual_bank_period(env, monkeypatch):
    import app.views as views
    import app.dashboard as dashboard
    monkeypatch.setattr(views, 'today', lambda: date(2026, 11, 2))
    monkeypatch.setattr(dashboard, 'today', lambda: date(2026, 11, 2))
    e=env
    old=confirmed(e, at='2026-09-20T15:00:00+00:00')
    current=confirmed(e, quantity=2)
    receipt=e.receipt('6000.00')
    e.allocate(receipt,[{'order_id':old,'amount':'5800.00'},{'order_id':current,'amount':'200.00'}])
    r=summary(e).json()
    assert [o['id'] for o in r['orders']]==[current]
    assert r['metrics']['order_count']==1 and r['metrics']['order_amount']==1160000
    assert r['metrics']['receivable']==1160000
    assert r['metrics']['outstanding']==1140000 and r['metrics']['overdue']==1140000
    # Receipt pays mostly an earlier order but belongs to this bank period.
    assert r['metrics']['verified']==600000
    assert r['receipts'][0]['selected_amount']==600000
    assert r['sales_summary'][0]['outstanding']==1140000
    assert r['as_of']=='2026-11-02'


def test_sao_paulo_inclusive_boundaries_and_selectable_order_dates(env):
    e=env
    before=confirmed(e,at='2026-10-01T02:59:59+00:00')
    first=confirmed(e,at='2026-10-01T03:00:00+00:00')
    last=confirmed(e,at='2026-11-01T02:59:59+00:00')
    after=confirmed(e,at='2026-11-01T03:00:00+00:00')
    assert {o['id'] for o in summary(e).json()['orders']}=={first,last}
    points=summary(e).json()['ranking_trends']['models'][0]['points']
    assert [p['date'] for p in points]==['2026-10-01','2026-10-31']
    assert summary(e,start='2026-10-31',end='2026-10-31').json()['metrics']['order_count']==1
    assert summary(e,basis='sale_date').json()['metrics']['matched_order_count']==0
    assert summary(e,basis='created',start='2026-09-01',end='2026-09-01').json()['metrics']['matched_order_count']==4
    assert summary(e,basis='due',start='2026-10-20',end='2026-10-20').json()['metrics']['matched_order_count']==4
    assert summary(e,start='',end='').json()['metrics']['order_count']==4


def test_empty_pending_opening_and_consignment_are_not_confirmed_sales(env):
    e=env
    confirmed(e)
    confirmed(e,status='CANCELED')
    confirmed(e,status='REJECTED')
    confirmed(e,source='OPENING')
    confirmed(e,sale_type='CONSIGNMENT',mode='ASSISTED')
    pending=e.legacy_order(e.sales)
    with Session() as db:
        db.get(Order,pending).created='2026-10-20T20:00:00+00:00';db.commit()
    r=summary(e).json()
    assert r['metrics']['order_count']==1
    assert r['metrics']['matched_order_count']==5
    assert r['rankings']['models'][0]['quantity']==1
    assert r['rankings']['dealers'][0]['order_count']==1
    assert summary(e,basis='created',status='DRAFT').json()['metrics']['waiting']==1
    empty=summary(e,start='2025-01-01',end='2025-12-31').json()
    assert all(value==0 for value in empty['metrics'].values())
    assert empty['orders']==[] and empty['receipts']==[]
    assert empty['rankings']=={'models':[],'dealers':[]}
    assert empty['ranking_trends']['models']==[] and empty['ranking_trends']['dealers']==[]


def test_type_and_status_attribute_receipts_without_duplicate_or_unassigned_credit(env):
    e=env
    other=confirmed(e,sale_type='OTHER')
    wholesale=confirmed(e)
    rid=e.receipt('10000.00')
    e.allocate(rid,[{'order_id':other,'amount':'1000.00'}])
    e.allocate(rid,[{'order_id':other,'amount':'2000.00'}])
    e.allocate(rid,[{'order_id':wholesale,'amount':'4000.00'}])
    r=summary(e,sale_type='OTHER').json()
    assert r['metrics']['verified']==300000
    assert len(r['receipts'])==1 and r['receipts'][0]['selected_amount']==300000
    assert r['receipt_scope']=='order_attributed'
    assert summary(e).json()['metrics']['verified']==1000000
    assert summary(e,payment_status='OVERDUE').json()['metrics']['verified']==700000
    assert summary(e,status='DRAFT').json()['metrics']['verified']==0
    admin_id=e.data()['user']['id']
    with Session() as db:
        db.add(Receipt(dealer_id=e.did,account_id=e.aid,amount=10000,date='2026-11-01',reference='outside-period',actor_id=admin_id));db.commit()
    assert summary(e).json()['metrics']['verified']==1000000
    assert summary(e,start='2026-11-01',end='2026-11-01').json()['metrics']['verified']==10000


def test_verified_claim_before_activation_is_attributed_for_every_role(env):
    e=env
    oid=confirmed(e,sale_type='CASH',status='WAITING_FINANCE_REVIEW')
    admin_id=e.data()['user']['id']
    with Session() as db:
        claim=Claim(order_id=oid,amount=10000,paid_date='2026-10-28',reference='test-claim',attachment='test.png',media_type='image/png',status='VERIFIED')
        db.add(claim);db.flush()
        db.add(Receipt(dealer_id=e.did,account_id=e.aid,amount=10000,date='2026-10-28',reference='claim-receipt',claim_id=claim.id,actor_id=admin_id));db.commit()
    for client in [e.admin,e.finance,e.sales,e.dealer]:
        r=summary(e,client,sale_type='CASH').json()
        assert r['metrics']['verified']==10000


def test_dealer_sales_and_finance_scope_all_details_and_totals(env):
    e=env
    own=confirmed(e)
    other=confirmed(e,client=e.dealer2,quantity=4)
    rid=e.receipt('100.00')
    for client in [e.dealer,e.sales]:
        r=summary(e,client).json()
        assert [o['id'] for o in r['orders']]==[own]
        assert r['metrics']['order_amount']==580000
        assert {d['id'] for d in r['dealers']}=={e.did}
        assert summary(e,client,dealer_id=e.did2).status_code==403
        assert summary(e,client,sales_id=e.other_sid).status_code==403
    assert len(summary(e,e.finance).json()['orders'])==2
    assert [o['id'] for o in summary(e,sales_id=e.other_sid).json()['orders']]==[other]
    assert summary(e,dealer_id=e.did,sales_id=e.other_sid).json()['orders']==[]


@pytest.mark.parametrize('filters',[
    {'start':'2026-11-01','end':'2026-10-01'}, {'start':'2026-02-30'},
    {'start':''}, {'end':''}, {'basis':'receipt.date'}, {'sale_type':'invalid'},
    {'status':'invalid'}, {'payment_status':'invalid'}, {'dealer_id':'1 OR 1=1'},
])
def test_invalid_filters_are_rejected(env,filters):
    assert summary(env,**filters).status_code==400


def test_dashboard_requires_login(env):
    with TestClient(app) as c:
        assert c.get('/api/dashboard-summary').status_code==401


def test_rankings_aggregate_multi_sku_exclude_gifts_and_freight_use_agreed_prices(env):
    e=env
    pid2=e.act(e.admin,'product.save',dict(sku='MT2',name='MT2',price='200.00'))['id']
    first=confirmed(e,quantity=10)
    second=confirmed(e,quantity=1)
    with Session() as db:
        # Historic confirmed order: ten paid vehicles, one gift, another SKU,
        # and freight. A later order contains a single model at its Demo price.
        db.add(OrderLine(order_id=first,product_id=e.pid,quantity=1,product_sku='R6',product_name='R6 mini',standard_price=580000,unit_price=0,gift=True))
        db.add(OrderLine(order_id=first,product_id=pid2,quantity=3,product_sku='MT2',product_name='MT2',standard_price=20000,unit_price=20000,gift=False))
        db.get(Order,first).freight=5000;db.get(Order,first).total=5865000
        line=db.scalar(select(OrderLine).where(OrderLine.order_id==second));line.unit_price=464000;line.demo_applied=True
        db.get(Order,second).total=464000
        db.commit()
    # Catalog changes must not rewrite historical order revenue.
    e.act(e.admin,'product.save',dict(id=e.pid,sku='R6',name='R6 renamed',price='9999.00',active=True))
    report=summary(e).json();ranks=report['rankings']
    a,b=ranks['models']
    assert (a['product_id'],a['quantity'],a['amount'],a['order_count'],a['dealer_count'])==(e.pid,11,6264000,2,1)
    assert a['name']=='R6 renamed'
    assert (b['product_id'],b['quantity'],b['amount'],b['order_count'])==(pid2,3,60000,1)
    assert ranks['dealers']==[dict(dealer_id=e.did,name='Dealer 1',code='D1',quantity=14,amount=6324000,order_count=2)]
    trends=report['ranking_trends']
    assert trends['basis']=='confirmed' and trends['start']=='2026-10-01' and trends['end']=='2026-10-31'
    assert trends['models'][0]['points']==[dict(date='2026-10-15',quantity=11,amount=6264000,order_count=2)]
    assert trends['dealers'][0]['points']==[dict(date='2026-10-15',quantity=14,amount=6324000,order_count=2)]


def test_rankings_follow_date_basis_and_business_filters(env):
    e=env
    confirmed(e,quantity=2,sale_type='CASH')
    confirmed(e,quantity=7,at='2026-11-01T03:00:00+00:00',sale_type='OTHER')
    confirmed(e,quantity=9,at='2026-09-20T15:00:00+00:00')
    october=summary(e).json()['rankings']
    assert october['models'][0]['quantity']==2
    assert october['dealers'][0]['order_count']==1
    november=summary(e,start='2026-11-01',end='2026-11-01').json()['rankings']
    assert november['models'][0]['quantity']==7
    trends=summary(e,basis='sale_date',start='2026-09-10',end='2026-09-10').json()['ranking_trends']
    assert trends['models'][0]['points']==[dict(date='2026-09-10',quantity=18,amount=10440000,order_count=3)]
    assert summary(e,start='',end='').json()['rankings']['models'][0]['quantity']==18
    assert summary(e,start='',end='',sale_type='OTHER').json()['rankings']['models'][0]['quantity']==7
    assert summary(e,status='DRAFT').json()['rankings']=={'models':[],'dealers':[]}
    assert summary(e,basis='sale_date',start='2026-09-10',end='2026-09-10').json()['rankings']['models'][0]['quantity']==18


def test_rankings_preserve_scope_and_sort_deterministically(env):
    e=env
    confirmed(e,quantity=2)
    confirmed(e,quantity=7,client=e.dealer2)
    all_ranks=summary(e).json()['rankings']
    assert [d['dealer_id'] for d in all_ranks['dealers']]==[e.did2,e.did]
    assert all_ranks['models'][0]['dealer_count']==2
    for client in [e.sales,e.dealer]:
        data=summary(e,client).json();ranks=data['rankings']
        assert ranks['models'][0]['quantity']==2 and ranks['models'][0]['dealer_count']==1
        assert [d['dealer_id'] for d in ranks['dealers']]==[e.did]
        assert [s['id'] for s in data['ranking_trends']['dealers']]==[e.did]
        assert data['ranking_trends']['models'][0]['points'][0]['quantity']==2
    own=summary(e,e.dealer2).json()['rankings']
    assert own['models'][0]['quantity']==7
    selected=summary(e,sales_id=e.other_sid).json()['rankings']
    assert [d['dealer_id'] for d in selected['dealers']]==[e.did2]


def test_undated_orders_remain_in_ranking_but_are_identified_as_missing_in_trend(env):
    e=env;confirmed(e,sale_date=None)
    data=summary(e,basis='sale_date',start='',end='').json()
    assert data['rankings']['models'][0]['quantity']==1
    assert data['ranking_trends']['undated_order_count']==1
    assert data['ranking_trends']['start']=='' and data['ranking_trends']['end']==''
    assert data['ranking_trends']['models'][0]['points']==[]
