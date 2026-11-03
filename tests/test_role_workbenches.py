import json
from datetime import timedelta
import pytest
from app.core import today, now
from app.db import Session
from app.models import Commission
from test_finance_activation import create as cash_order, verify, PROOF


def workbench(e,role='admin',**params):
    r=getattr(e,role).get('/api/insights',params=params)
    assert r.status_code==200,r.text
    return r.json()


def alerts(r,code):return [a for a in r['alerts'] if a['code']==code]


def other_order(e,did=None,actor=None,quantity=1):
    return e.act(actor or e.sales,'order.create',dict(dealer_id=did or e.did,sale_type='OTHER',sale_date=today().isoformat(),
        due=(today()+timedelta(days=10)).isoformat(),terms='Isolated role workbench test',items=[dict(product_id=e.pid,quantity=quantity)]))['id']


@pytest.mark.parametrize('role',['admin','sales','finance','warehouse','dealer'])
def test_each_role_gets_its_own_profile_and_read_only_destinations(env,role):
    e=env;e.legacy_order();r=workbench(e,role)
    assert r['role']==role and len(r['profile']['title'])==3 and r['version']=='HYNAK-WORKBENCH-v2'
    assert len(r['cards'])==5 and set(r['rules'])==set(r['profile']['rule_keys'])
    assert all(a['category'] in r['profile']['categories'] for a in r['alerts'])
    assert all(a['kind'] not in {'review','verify','ship','transfer','pay'} for row in r['alerts'] for a in row['actions'])
    forged=getattr(e,role).get('/api/insights?role=admin').json()
    assert forged['role']==role


def test_sales_scope_excludes_other_dealers_and_their_unreserved_demand(env):
    e=env;e.stock(2)
    own=other_order(e,quantity=4)
    foreign=other_order(e,did=e.did2,actor=e.admin,quantity=99)
    r=workbench(e,'sales');encoded=json.dumps(r,ensure_ascii=False)
    assert 'Dealer 2' not in encoded and f'H{foreign:06d}' not in encoded
    stock=alerts(r,'STOCK_SHORTAGE')[0]
    assert stock['facts']['scoped_unreserved']==2
    assert stock['facts']['related_order_ids']==[own]
    assert 'global_unreserved' not in stock['facts'] and not alerts(r,'LOW_STOCK')
    assert all(x['id']==own for a in r['alerts'] for x in a['actions'] if x['kind']=='order')


def test_dealer_gets_only_own_purchase_progress_and_no_internal_actions(env):
    e=env;own=cash_order(e)
    foreign=other_order(e,did=e.did2,actor=e.admin)
    r=workbench(e,'dealer');encoded=json.dumps(r,ensure_ascii=False)
    assert 'Dealer 2' not in encoded and f'H{foreign:06d}' not in encoded
    cash=alerts(r,'CASH_REVIEW')[0]
    assert cash['facts']['order_id']==own['id'] and '等待财务核实' in cash['title'][0]
    assert cash['actions']==[dict(kind='order',id=own['id'])]
    assert all(x['kind'] not in {'collections','inventory','policies','commissions'} for a in r['alerts'] for x in a['actions'])
    assert not {'stock','policy','commissions'} & set(r['summary']['categories'])


@pytest.mark.parametrize('role',['sales','dealer'])
def test_scoped_roles_cannot_expand_via_filters_or_admin_endpoint(env,role):
    e=env;foreign=other_order(e,did=e.did2,actor=e.admin)
    client=getattr(e,role)
    assert client.get('/api/insights',params=dict(scope='selected',dealer_id=e.did2)).status_code==403
    assert client.get('/api/admin/insights').status_code==403
    r=client.get('/api/insights',params=dict(scope='all',dealer_id=e.did2,role='admin')).json()
    assert f'H{foreign:06d}' not in json.dumps(r)


def test_warehouse_report_has_no_money_credit_or_payment_claims(env):
    e=env;e.stock(10);oid=other_order(e,quantity=2)
    e.act(e.finance,'receipt.create',dict(dealer_id=e.did,account_id=e.aid,amount='1234.56',date=today().isoformat(),reference='PRIVATE-BANK-REF'))
    r=workbench(e,'warehouse')
    assert alerts(r,'DISPATCH_TODO')[0]['facts']['order_id']==oid
    assert not {'overdue_amount','recharge_claimed'} & set(r['summary'])
    assert all(c['format']!='money' for c in r['cards'])
    forbidden={'receivable','verified_allocated','pending_claim_amount','available_credit','total','outstanding','grade','verified_bank_amount'}
    for a in r['alerts']:
        assert a['amount'] is None and not forbidden & set(a['facts'])
        assert {x['kind'] for x in a['actions']}<={'order','inventory','consignment'}
    assert 'PRIVATE-BANK-REF' not in json.dumps(r) and '123456' not in json.dumps(r)


def test_dispatch_and_pickup_todos_clear_after_actual_steps(env):
    e=env;e.stock(10);oid=other_order(e)
    assert alerts(workbench(e,'warehouse'),'DISPATCH_TODO')
    sid=e.ship(oid,serials='TEST-SN-1')
    for role in ['warehouse','dealer','sales']:
        r=workbench(e,role)
        assert not alerts(r,'DISPATCH_TODO')
        assert alerts(r,'PICKUP_TODO')[0]['facts']['shipment_id']==sid
    e.act(e.warehouse,'shipment.action',dict(shipment_id=sid,action='DELIVER'))
    for role in ['warehouse','dealer','sales']:
        assert not alerts(workbench(e,role),'PICKUP_TODO')


def test_finance_difference_uses_already_verified_bank_funds_once(env):
    e=env;c=cash_order(e);verify(e,c['claim_id'],amount='1000.00',ref='PARTIAL-ONE')
    response=e.dealer.post('/api/claims',data=dict(order_id=c['id'],amount='2000.00',paid_date=today().isoformat()),files={'file':('partial.pdf',PROOF,'application/pdf')},headers={'X-CSRF-Token':e.data(e.dealer)['csrf']})
    assert response.status_code==200,response.text
    r=workbench(e,'finance');a=alerts(r,'PAYMENT_AMOUNT_CHECK')[0]
    assert a['facts']['verified_bank_amount']==100000
    assert a['facts']['expected_unverified']==480000 and a['facts']['pending_claim_amount']==200000
    assert a['amount']==280000
    for role in ['sales','dealer','warehouse']:
        assert not alerts(workbench(e,role),'PAYMENT_AMOUNT_CHECK')
    assert {c['format'] for c in r['cards']}=={'count','money'}


def test_normal_complete_cash_proof_has_no_false_amount_difference(env):
    e=env;cash_order(e)
    assert not alerts(workbench(e,'finance'),'PAYMENT_AMOUNT_CHECK')


def test_my_consignment_stock_is_only_from_my_own_sites(env):
    e=env;e.stock(100)
    foreign_site=e.act(e.admin,'warehouse.save',dict(code='SECRET-C2',name='Private other dealer site',kind='CONSIGNMENT',dealer_id=e.did2))['id']
    for wid,qty in [(e.cwid,3),(foreign_site,1)]:
        e.act(e.admin,'stock.move',dict(kind='CONSIGN_SEND',warehouse_id=e.wid,target_id=wid,product_id=e.pid,quantity=qty,reason='Isolated consignment send'))
    r=workbench(e,'dealer');stock=alerts(r,'MY_CONSIGNMENT_STOCK')
    assert len(stock)==1 and stock[0]['facts']['warehouse_id']==e.cwid and stock[0]['quantity']==3
    assert 'Private other dealer site' not in json.dumps(r) and not alerts(r,'LOW_STOCK') and not alerts(r,'STOCK_SHORTAGE')
    assert stock[0]['actions']==[dict(kind='consignment',id=e.did,warehouse_id=e.cwid)]


def test_commission_progress_is_personal_for_sales(env):
    e=env;own=other_order(e);second=other_order(e)
    with Session() as db:
        for oid,sid,amount in [(own,e.sid,22200),(second,e.other_sid,11100)]:
            db.add(Commission(order_id=oid,sales_id=sid,base=580000,bps=300,amount=amount,status='APPROVED',reviewer_id=e.data()['user']['id'],approved_at=now()))
        db.commit()
    sales=alerts(workbench(e,'sales'),'COMMISSION_PROGRESS')
    assert len(sales)==1 and sales[0]['facts']['order_id']==own and sales[0]['amount']==22200
    assert len(alerts(workbench(e,'finance'),'COMMISSION_PROGRESS'))==2
    assert not alerts(workbench(e,'dealer'),'COMMISSION_PROGRESS')
    assert not alerts(workbench(e,'warehouse'),'COMMISSION_PROGRESS')


def test_latest_followup_date_supersedes_old_and_settled_debt_does_not_remind(env):
    e=env;oid=other_order(e)
    for offset in [-2,3]:
        e.act(e.sales,'followup.create',dict(dealer_id=e.did,order_id=oid,note='Follow-up workbench test',next_date=(today()+timedelta(days=offset)).isoformat()))
    assert not alerts(workbench(e,'sales'),'FOLLOWUP_DUE')
    e.act(e.sales,'followup.create',dict(dealer_id=e.did,order_id=oid,note='Current due follow-up',next_date=today().isoformat()))
    assert len(alerts(workbench(e,'sales'),'FOLLOWUP_DUE'))==1
    assert not alerts(workbench(e,'dealer'),'FOLLOWUP_DUE')
    rid=e.receipt(reference='SETTLE-FOLLOWUP');e.allocate(rid,[dict(order_id=oid,amount='5800.00')])
    assert not alerts(workbench(e,'sales'),'FOLLOWUP_DUE')


@pytest.mark.parametrize('role',['admin','sales','finance','warehouse','dealer'])
def test_analysis_does_not_change_shared_business_records(env,role):
    e=env;e.stock(20);other_order(e)
    before=e.data();workbench(e,role);after=e.data()
    for key in ['orders','inventory','receipts','allocations','shipments','recharges','wallets','commissions','audit']:
        assert after[key]==before[key]


@pytest.mark.parametrize('role',['admin','sales','finance','warehouse','dealer'])
def test_each_role_can_follow_applied_order_dates(env,role):
    e=env;oid=other_order(e)
    tomorrow=(today()+timedelta(days=1)).isoformat()
    r=workbench(e,role,scope='selected',basis='created',start=tomorrow,end=tomorrow)
    assert not any(a['facts'].get('order_id')==oid for a in r['alerts'])
    assert r['filters']['basis']=='created'


def test_workbenches_do_not_grant_financial_or_order_review_permissions(env):
    e=env;c=cash_order(e)
    for role in ['sales','dealer','warehouse']:
        workbench(e,role)
        e.act(getattr(e,role),'receipt.create',dict(dealer_id=e.did,account_id=e.aid,claim_id=c['claim_id'],amount='5800.00',date=today().isoformat(),reference='FORBIDDEN'),expected=403)
    assert not e.data()['receipts']
