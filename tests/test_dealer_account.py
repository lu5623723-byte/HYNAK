"""Dealer end-to-end and authorization checks, always on isolated databases."""
import io
import json
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from app.main import app
from app.db import Session
from app.models import User

PROOF=b'%PDF-1.4\n% Isolated dealer test proof; not a real payment.\n%%EOF'

def headers(client,key='dealer-flow-key'):
    return {'X-CSRF-Token':client.get('/api/data').json()['csrf'],'Idempotency-Key':key}

def create(e,kind='CASH',key='dealer-create-order',**changes):
    data=dict(dealer_id=e.did2,sale_date='2026-10-30',sale_type=kind,due='2026-11-30',
              payment_at='2026-10-30T12:30',items=[dict(product_id=e.pid,quantity=1)])
    data.update(changes)
    return e.dealer.post('/api/orders',data={'payload':json.dumps(data)},
                         files={'file':('TEST_ONLY.pdf',PROOF,'application/pdf')} if kind=='CASH' else None,
                         headers=headers(e.dealer,key))

def test_dealer_login_logout_and_disabled_account(env):
    e=env
    with TestClient(app) as client:
        assert client.get('/api/data').status_code==401
        assert client.post('/api/login',json={'username':'dealer','password':'incorrect'}).status_code==401
        assert client.post('/api/login',json={'username':' DEALER ','password':'Testing-12345'}).status_code==200
        assert client.get('/api/data').json()['user']['dealer_id']==e.did
        assert client.post('/api/logout',json={},headers=headers(client)).status_code==200
        assert client.get('/api/data').status_code==401
    uid=next(u['id'] for u in e.data()['users'] if u['username']=='dealer')
    e.act(e.admin,'user.save',dict(id=uid,username='dealer',name='Dealer',role='dealer',dealer_id=e.did,active=False))
    assert e.dealer.get('/api/data').status_code==401
    assert e.dealer.post('/api/login',json={'username':'dealer','password':'Testing-12345'}).status_code==401

def test_dealer_cash_order_review_receipt_and_balance(env):
    e=env;r=create(e);assert r.status_code==200,r.text
    oid,cid=r.json()['id'],r.json()['claim_id']
    d=e.data(e.dealer);o=d['orders'][0]
    # A submitted foreign dealer_id cannot place an order for another account.
    assert o['dealer_id']==e.did and o['mode']=='SELF' and o['status']=='WAITING_FINANCE_REVIEW'
    assert o['sales_id']==e.sid and o['receivable']==0
    assert d['claims'][0]['status']=='PENDING' and d['metrics']['verified']==0
    assert e.dealer.get(f'/api/claims/{cid}/attachment').content==PROOF
    e.act(e.admin,'order.review',dict(order_id=oid),expected=403)
    rid=e.act(e.finance,'receipt.create',dict(dealer_id=e.did,account_id=e.aid,claim_id=cid,amount='3000.00',date='2026-10-30',reference='DEALER-FLOW-BANK-REF'))['id']
    d=e.data(e.dealer)
    assert d['orders'][0]['status']=='WAITING_FINANCE_REVIEW' and d['orders'][0]['receivable']==0
    additional=e.dealer.post('/api/claims',data=dict(order_id=oid,amount='2800.00',paid_date='2026-10-30',reference='ADDITIONAL'),files={'file':('additional.png',PROOF,'image/png')},headers={'X-CSRF-Token':e.data(e.dealer)['csrf']})
    assert additional.status_code==200,additional.text
    e.act(e.finance,'receipt.create',dict(dealer_id=e.did,account_id=e.aid,claim_id=additional.json()['id'],amount='2800.00',date='2026-10-30',reference='DEALER-FLOW-BANK-REF-2'))
    d=e.data(e.dealer)
    assert d['orders'][0]['status']=='CONFIRMED'
    assert d['orders'][0]['received']==580000 and d['orders'][0]['outstanding']==0
    assert d['metrics']['verified']==580000 and d['claims'][0]['status']=='VERIFIED'
    assert 'account_id' not in d['receipts'][0] and 'reference' not in d['receipts'][0]
    assert d['bank_accounts']==[] and d['audit']==[] and d['commissions']==[]

def test_administrator_consignment_and_dealer_sold_report(env):
    e=env;e.stock(5)
    r=create(e,'CONSIGNMENT');assert r.status_code==403,r.text
    oid=e.act(e.admin,'order.create',dict(dealer_id=e.did,sale_type='CONSIGNMENT',sale_date='2026-10-30',due='2026-11-30',items=[dict(product_id=e.pid,quantity=1)]))['id']
    e.act(e.admin,'order.submit',{'order_id':oid});e.review(oid)
    token=e.act(e.admin,'order.token',{'order_id':oid})['token']
    assert e.dealer.post('/api/quote/'+token,json={'action':'CONFIRM'}).status_code==200
    assert e.data(e.dealer)['orders'][0]['receivable']==580000
    sid=e.ship(oid,consignment_location_id=e.cwid)
    e.act(e.warehouse,'shipment.action',dict(shipment_id=sid,action='DELIVER'))
    d=e.data(e.dealer);assert len(d['warehouses'])==1 and d['inventory'][0]['quantity']==1
    report=e.act(e.dealer,'consignment.report',dict(source_order_id=oid,warehouse_id=e.cwid,product_id=e.pid,quantity=1),key='dealer-consign-report')
    again=e.act(e.dealer,'consignment.report',dict(source_order_id=oid,warehouse_id=e.cwid,product_id=e.pid,quantity=1),key='dealer-consign-report')
    assert report==again and e.data(e.dealer)['metrics']['receivable']==580000
    e.act(e.admin,'consignment.confirm',dict(report_id=report['id'],due='2026-11-30'))
    d=e.data(e.dealer);assert d['metrics']['receivable']==580000
    assert next(i for i in d['inventory'] if i['product_id']==e.pid)['quantity']==0

def test_dealer_may_not_override_price_or_bypass_csrf(env):
    e=env
    r=create(e,items=[dict(product_id=e.pid,quantity=1,unit_price='0.01')],reason='Pretend admin approval')
    assert r.status_code==403 and not e.data(e.dealer)['orders']
    assert e.dealer.post('/api/orders',data={'payload':'{}'},headers={'Idempotency-Key':'missing-csrf'}).status_code==403

@pytest.mark.parametrize('action',[
    'user.save','dealer.save','product.save','warehouse.save','account.save',
    'order.submit','order.review','order.revise','order.token','order.revoke','order.reserve','order.cancel',
    'stock.move','shipment.create','shipment.action','consignment.confirm',
    'receipt.create','receipt.allocate','receivable.adjust','receivable.due',
    'followup.create','commission.approve','commission.pay','commission.adjust','import.commit',
])
def test_dealer_cannot_execute_staff_operations(env,action):
    e=env;oid=e.legacy_order()
    e.act(e.dealer,action,{'order_id':oid},expected=403)

def test_dealer_export_and_attachment_object_isolation(env):
    e=env;own=create(e);assert own.status_code==200,own.text
    other=e.legacy_order(e.dealer2);e.review(other)
    r=e.dealer2.post('/api/claims',data={'order_id':other,'amount':'100.00','paid_date':'2026-10-30','reference':'OTHER-PRIVATE-REF'},
                     files={'file':('other.pdf',PROOF,'application/pdf')},headers=headers(e.dealer2))
    assert r.status_code==200,r.text
    assert e.dealer.get('/api/claims/'+str(r.json()['id'])+'/attachment').status_code==403
    assert [o['id'] for o in e.data(e.dealer)['orders']]==[own.json()['id']]
    for module in ['orders','receivables']:
        r=e.dealer.get(f'/api/export/{module}?dealer_id={e.did2}');assert r.status_code==200
        assert load_workbook(io.BytesIO(r.content)).active.max_row==1
    for module in ['receipts','followups','commissions']:
        assert e.dealer.get('/api/export/'+module).status_code==403
    assert e.dealer.get('/api/import-template').status_code==403
    assert e.dealer.get('/api/imports/1/source').status_code==403
    assert e.dealer.post('/api/backup',json={},headers=headers(e.dealer)).status_code==403

def test_dealer_response_does_not_expose_internal_collection_or_commission(env):
    e=env;oid=e.legacy_order();e.review(oid,commission_bps=500,reason='Internal commission override')
    e.act(e.sales,'followup.create',dict(dealer_id=e.did,order_id=oid,note='INTERNAL_COLLECTION_NOTE',next_date='2026-11-01'))
    d=e.data(e.dealer)
    assert d['followups']==[], 'Dealer received internal collection notes'
    assert 'commission_bps' not in d['orders'][0], 'Dealer received staff commission override'
    assert 'commission_eligible' not in d['orders'][0]
    assert all(u['id']==e.sid for u in d['users']), 'Dealer received unrelated employee directory'
    assert 'INTERNAL_COLLECTION_NOTE' not in json.dumps(d)
    wb=load_workbook(io.BytesIO(e.dealer.get('/api/export/orders').content))
    assert 'commission_bps' not in list(wb.active.values)[0]

def test_dealer_inventory_export_does_not_expose_internal_stock_ledger(env):
    e=env;e.stock(10)
    e.act(e.warehouse,'stock.move',dict(kind='CONSIGN_SEND',warehouse_id=e.wid,target_id=e.cwid,product_id=e.pid,quantity=3,reason='INTERNAL_STOCK_REASON'))
    d=e.data(e.dealer);assert d['ledger']==[]
    wb=load_workbook(io.BytesIO(e.dealer.get('/api/export/inventory').content))
    assert 'stock_ledger' not in wb.sheetnames, 'Dealer export exposed internal stock movements'
    assert wb.active.max_row==2
