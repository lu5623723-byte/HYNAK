import io, json, zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlalchemy import select, text
from openpyxl import load_workbook
from app.db import Session, DATA_DIR
from app.models import *
from app.core import digest
from app.backup import backup, restore

def row(e,oid,c=None):return next(o for o in e.data(c)['orders'] if o['id']==oid)
def csrf(c):return {'X-CSRF-Token':c.get('/api/data').json()['csrf']}
def test_U01_self_order_review_activates_once(env):
    e=env;oid=e.order();assert row(e,oid)['status']=='WAITING_INTERNAL_REVIEW';assert e.data()['metrics']['receivable']==0
    e.review(oid);assert row(e,oid)['status']=='CONFIRMED';assert row(e,oid)['receivable']==580000
    e.act(e.admin,'order.review',{'order_id':oid},expected=409)
def test_U02_assisted_quote_manual_confirmation(env):
    e=env;oid=e.order(e.sales);assert row(e,oid)['status']=='DRAFT'
    e.act(e.sales,'order.token',{'order_id':oid},expected=409)
    e.act(e.sales,'order.submit',{'order_id':oid});e.review(oid)
    t=e.act(e.sales,'order.token',{'order_id':oid});raw=t['token']
    assert len(raw)>=43
    with Session() as db:assert db.get(ConfirmationToken,digest(raw)).hash!=raw
    public=e.dealer.get('/api/quote/'+raw).json();assert 'dealer_id' not in public and 'bank_accounts' not in public
    assert row(e,oid)['receivable']==0
    assert e.dealer.get('/confirm/'+raw).status_code==200
    assert row(e,oid)['receivable']==0
    assert e.dealer.post('/api/quote/'+raw,json={'action':'CONFIRM'}).json()['status']=='CONFIRMED'
    assert row(e,oid)['receivable']==580000

def assisted(e):
    oid=e.order(e.sales);e.act(e.sales,'order.submit',{'order_id':oid});e.review(oid)
    return oid,e.act(e.sales,'order.token',{'order_id':oid})['token']
def test_U03_revocation_expiry_replay_revision(env):
    e=env;oid,raw=assisted(e)
    assert e.dealer.post('/api/quote/'+raw,json={'action':'CONFIRM'}).status_code==200
    assert e.dealer.post('/api/quote/'+raw,json={'action':'CONFIRM'}).json()['repeated']
    assert e.dealer.post('/api/quote/'+raw,json={'action':'REJECT'}).status_code==409
    assert e.data()['metrics']['receivable']==580000
    oid,raw=assisted(e);e.act(e.sales,'order.revoke',{'order_id':oid});assert e.dealer.get('/api/quote/'+raw).status_code==410
    raw=e.act(e.sales,'order.token',{'order_id':oid})['token']
    e.act(e.admin,'order.revise',{'order_id':oid,'reason':'Correct quote','due':'2026-12-01','items':[{'product_id':e.pid,'quantity':2,'unit_price':'5500.00'}]})
    assert e.dealer.post('/api/quote/'+raw,json={'action':'CONFIRM'}).status_code==410
    assert row(e,oid)['version']==2
    e.review(oid);raw=e.act(e.sales,'order.token',{'order_id':oid})['token']
    with Session() as db:db.get(ConfirmationToken,digest(raw)).expires='2000-01-01T00:00:00+00:00';db.commit()
    assert e.dealer.post('/api/quote/'+raw,json={'action':'CONFIRM'}).status_code==410
    assert row(e,oid)['status']=='EXPIRED'
def test_U04_demo_and_no_stacking(env):
    e=env;oid=e.order(promotion='DEMO');assert row(e,oid)['lines'][0]['unit_price']==464000
    from test_sales_details import post_order,payload,PROOF
    data=payload(e,'CASH');data['promotion']='DEMO+TEN_PLUS_ONE'
    assert post_order(e,data,PROOF).status_code==400
def test_U05_25_paid_2_gifts_real_stock(env):
    e=env;e.stock(27);oid=e.order(quantity=25,promotion='TEN_PLUS_ONE');e.review(oid)
    o=row(e,oid);assert o['total']==14500000;assert [(l['quantity'],l['unit_price']) for l in o['lines']]==[(25,580000),(2,0)]
    e.ship(oid);assert next(i for i in e.data()['inventory'] if i['warehouse_id']==e.wid)['quantity']==0

def claim(e,oid):
    return e.dealer.post('/api/claims',data={'order_id':oid,'amount':'5000.00','paid_date':'2026-10-28','reference':'CLAIM-1'},files={'file':('proof.png',b'\x89PNG\r\n\x1a\nproof','image/png')},headers=csrf(e.dealer))
def test_U06_claim_does_not_change_receipts(env):
    e=env;oid=e.order();e.review(oid);r=claim(e,oid);assert r.status_code==200,r.text
    assert e.data()['metrics']['verified']==0;assert row(e,oid)['outstanding']==580000
    cid=r.json()['id'];e.act(e.finance,'receipt.create',{'dealer_id':e.did,'account_id':e.aid,'amount':'5000.00','date':'2026-10-28','reference':'BANK-CLAIM','claim_id':cid})
    assert e.data()['metrics']['verified']==500000;assert row(e,oid)['outstanding']==580000
    assert e.data()['claims'][0]['status']=='VERIFIED'
def test_U07_allocate_two_orders_once_and_overpayment(env):
    e=env;o1=e.order();o2=e.order();e.review(o1);e.review(o2);rid=e.receipt('3000.00')
    payload=[{'order_id':o1,'amount':'1000.00'},{'order_id':o2,'amount':'2000.00'}]
    e.allocate(rid,payload,key='same-allocation');e.allocate(rid,payload,key='same-allocation')
    assert e.data()['metrics']['verified']==300000;assert row(e,o1)['outstanding']==480000;assert row(e,o2)['outstanding']==380000
    e.allocate(rid,[{'order_id':o1,'amount':'0.01'}],expected=400)
    rid=e.receipt('10000.00');e.allocate(rid,[{'order_id':o1,'amount':'4800.00'}]);assert e.data()['dealers'][0]['credit']==520000
    e.act(e.finance,'receipt.create',{'dealer_id':e.did,'account_id':e.aid,'amount':'3000.00','date':'2026-10-28','reference':'BANK-CLAIM-DUP'},key='dup-receipt')
    e.act(e.finance,'receipt.create',{'dealer_id':e.did,'account_id':e.aid,'amount':'3000.00','date':'2026-10-28','reference':' bank-claim-dup '},expected=409)
def test_U08_overdue_and_collection(env):
    e=env;oid=e.order(due='2026-01-01');e.review(oid);assert row(e,oid)['overdue_days']>0;assert row(e,oid)['payment_status']=='OVERDUE'
    e.act(e.sales,'followup.create',{'dealer_id':e.did,'order_id':oid,'note':'Called customer','next_date':'2026-11-01'})
    assert e.data(e.sales)['followups'][0]['note']=='Called customer'
def test_U09_whatsapp_draft(env):
    e=env;oid=e.order();e.review(oid);r=e.sales.get('/api/whatsapp/'+str(oid)).json();assert r['url'].startswith('https://wa.me/5511991234567?text=');assert r['status']=='draft_only'
def test_U10_negative_stock_admin_exception_and_rollback(env):
    e=env;e.stock(5);oid=e.order(quantity=10);e.review(oid)
    e.act(e.warehouse,'shipment.create',{'order_id':oid,'warehouse_id':e.wid,'exception':'legacy','reason':'shortage','allow_negative':True},expected=400)
    assert e.data()['shipments']==[]
    e.ship(oid,e.admin,allow_negative=True,reason='Approved historical mismatch')
    assert next(i for i in e.data()['inventory'] if i['warehouse_id']==e.wid)['quantity']==-5
    with Session() as db:
        import pytest
        with pytest.raises(Exception):db.execute(text('UPDATE stock_ledger SET delta=999'))
def test_U11_transfer_no_sales(env):
    e=env;e.stock(10);e.act(e.warehouse,'stock.move',{'kind':'TRANSFER','warehouse_id':e.wid,'target_id':e.wid2,'product_id':e.pid,'quantity':5,'reason':'Restock secondary'},key='transfer-unique')
    e.act(e.warehouse,'stock.move',{'kind':'TRANSFER','warehouse_id':e.wid,'target_id':e.wid2,'product_id':e.pid,'quantity':5,'reason':'Restock secondary'},key='transfer-unique')
    inv=e.data()['inventory'];assert sum(i['quantity'] for i in inv)==10;assert {i['quantity'] for i in inv}=={5};assert e.data()['metrics']['receivable']==0

def test_U12_consignment_confirmation_idempotent(env):
    e=env;e.stock(10);e.act(e.warehouse,'stock.move',{'kind':'CONSIGN_SEND','warehouse_id':e.wid,'target_id':e.cwid,'product_id':e.pid,'quantity':10,'reason':'Consignment, not sale'})
    assert e.data()['metrics']['receivable']==0
    rid=e.act(e.dealer,'consignment.report',{'warehouse_id':e.cwid,'product_id':e.pid,'quantity':3})['id']
    for _ in range(2):e.act(e.admin,'consignment.confirm',{'report_id':rid,'due':'2026-12-01'})
    assert next(i for i in e.data()['inventory'] if i['warehouse_id']==e.cwid)['quantity']==7
    assert e.data()['metrics']['receivable']==1740000;assert len(e.data()['orders'])==1

def test_U13_commission_full_receipt_and_actual_delivery(env):
    e=env;e.stock();oid=e.order();e.review(oid);sid=e.ship(oid)
    e.act(e.admin,'commission.approve',{'order_id':oid},expected=400)
    rid=e.receipt('3000.00');e.allocate(rid,[{'order_id':oid,'amount':'3000.00'}])
    e.act(e.warehouse,'shipment.action',{'shipment_id':sid,'action':'DELIVER'})
    e.act(e.admin,'commission.approve',{'order_id':oid},expected=400)
    rid=e.receipt('2800.00');e.allocate(rid,[{'order_id':oid,'amount':'2800.00'}])
    cid=e.act(e.admin,'commission.approve',{'order_id':oid})['id'];c=e.data()['commissions'][0];assert c['base']==580000;assert c['amount']==17400
    e.act(e.finance,'commission.pay',{'commission_id':cid,'amount':'174.00','date':'2026-10-28','note':'Paid manually'})
    assert e.data()['commissions'][0]['status']=='PAID'

def test_U14_import_verified_only_and_duplicate(env):
    e=env;raw=e.admin.get('/api/import-template').content;wb=load_workbook(io.BytesIO(raw))
    wb['products'].append(['NEW','New vehicle','100.00',1]);wb['products'].append(['PENDING','Not verified','999.00',0])
    wb['stock'].append(['W1','Primary','PRIMARY','NEW',10,1]);wb['stock'].append(['W1','Primary','PRIMARY','PENDING',999,0])
    wb['receivables'].append(['AR-OPEN-1','D1','200.00','2026-01-01',1]);wb['credit'].append(['CREDIT-1','D1','50.00',1]);out=io.BytesIO();wb.save(out)
    def preview():return e.admin.post('/api/import-preview',data={'cutover':'2026-10-28','source':'Verified test snapshot'},files={'file':('snapshot.xlsx',out.getvalue())},headers=csrf(e.admin))
    r=preview();assert r.status_code==200,r.text;r=r.json();assert not r['errors'];assert r['pending']==2
    e.act(e.admin,'import.commit',{'batch_id':r['id']});e.act(e.admin,'import.commit',{'batch_id':r['id']});assert preview().json()['repeated']
    data=e.data();assert data['metrics']['receivable']==20000;assert data['metrics']['verified']==0;assert data['metrics']['month_sales']==0;assert data['metrics']['credit']==5000
    assert sum(i['quantity'] for i in data['inventory'])==10;assert 'PENDING' not in [p['sku'] for p in data['products']]
def test_U15_object_authorization_attachments_exports(env):
    e=env;oid=e.order();e.review(oid);cid=claim(e,oid).json()['id']
    assert e.dealer2.get('/api/claims/'+str(cid)+'/attachment').status_code==403
    assert e.data(e.dealer2)['orders']==[];assert e.data(e.dealer2)['claims']==[]
    e.act(e.dealer2,'shipment.create',{'order_id':oid,'warehouse_id':e.wid},expected=403)
    assert e.dealer2.get('/api/export/receipts').status_code==403
    book=load_workbook(io.BytesIO(e.dealer2.get('/api/export/orders?dealer_id='+str(e.did)).content));assert book.active.max_row==1
    assert e.data(e.sales)['dealers'][0]['id']==e.did;assert len(e.data(e.sales)['dealers'])==1
    assert e.data(e.dealer)['warehouses'][0]['id']==e.cwid
    assert e.data(e.dealer)['bank_accounts']==[]
def test_U16_backup_restore_restart_persistence(env,tmp_path):
    e=env;e.stock(11);oid=e.order();e.review(oid);cid=claim(e,oid).json()['id']
    result=backup(True);dest=restore(DATA_DIR/'backups'/result['file'],tmp_path/'restored')
    import sqlite3
    with sqlite3.connect(dest/'hynak.sqlite3') as db:
        assert db.execute('SELECT amount FROM receivables').fetchone()[0]==580000
        path=db.execute('SELECT attachment FROM payment_claims WHERE id=?',(cid,)).fetchone()[0]
        assert (dest/'attachments'/path).read_bytes().startswith(b'\x89PNG')
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    from app.db import engine
    engine.dispose();assert row(e,oid)['receivable']==580000;assert e.dealer.get(f'/api/claims/{cid}/attachment').status_code==200

def test_U17_local_confirmation_only_cloud_pending(env):
    # Local counterpart only. Actual remote HTTPS acceptance explicitly remains manual.
    e=env;oid,raw=assisted(e);assert e.dealer.post('/api/quote/'+raw,json={'action':'CONFIRM'}).status_code==200
    assert row(e,oid)['receivable']==580000

def test_reservation_72h_release_claim_only_and_keep_real_payment(env):
    e=env;e.stock(10);o1=e.order();o2=e.order();e.review(o1);e.review(o2);claim(e,o1)
    rid=e.receipt('1.00');e.allocate(rid,[{'order_id':o2,'amount':'1.00'}])
    with Session() as db:
        for r in db.scalars(select(Reservation)).all():r.expires='2000-01-01T00:00:00+00:00'
        db.commit()
    assert row(e,o1)['reserved']==0;assert row(e,o2)['reserved']==1

def test_transactions_rollback_and_same_key_concurrency(env):
    e=env;oid=e.order();e.review(oid);rid=e.receipt('100.00')
    e.allocate(rid,[{'order_id':oid,'amount':'50.00'},{'order_id':99999,'amount':'50.00'}],expected=404)
    assert row(e,oid)['received']==0
    payload={'dealer_id':e.did,'receipt_id':rid,'items':[{'order_id':oid,'amount':'100.00'}]};headers={**csrf(e.finance),'Idempotency-Key':'concurrent-key'}
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(lambda _:e.finance.post('/api/action/receipt.allocate',json=payload,headers=headers),range(2)))
    assert all(r.status_code==200 for r in responses);assert row(e,oid)['received']==10000

def test_csrf_aliases_and_price_locks(env):
    e=env;assert e.admin.post('/api/action/product.save',json={'sku':'X'},headers={'Idempotency-Key':'missing-csrf'}).status_code==403
    assert e.admin.post('/api/login',json={},headers={'Origin':'https://evil.invalid'}).status_code==403
    e.act(e.admin,'account.save',{'name':'xt'},expected=400)
    e.act(e.admin,'account.save',{'name':'xiaolu','aliases':'卢'},expected=400)
    oid=e.order();e.review(oid);e.act(e.admin,'order.revise',{'order_id':oid,'reason':'wrong','items':[]},expected=409)
    e.act(e.admin,'product.save',{'sku':'bad','name':'bad','price':0.1},expected=400)

def test_7030_gifts_and_partial_shipments(env):
    e=env;e.stock(30);oid=e.order(quantity=10,promotion='TEN_PLUS_ONE',special_7030=True);e.review(oid,partial_allowed=True)
    o=row(e,oid);paid,gift=o['lines'];sid=e.ship(oid,lines=[{'line_id':paid['id'],'quantity':10}])
    e.act(e.warehouse,'shipment.create',{'order_id':oid,'warehouse_id':e.wid,'exception':'missing','reason':'Gift','lines':[{'line_id':gift['id'],'quantity':1}]},expected=400)
    e.act(e.warehouse,'shipment.action',{'shipment_id':sid,'action':'CANCEL','reason':'Not collected'})
    assert sum(i['quantity'] for i in e.data()['inventory'])==30

def test_quote_snapshot_product_edit_cancel_and_adjustments(env):
    e=env;oid,raw=assisted(e)
    e.act(e.admin,'product.save',{'id':e.pid,'sku':'R6-NEW','name':'Renamed model','price':'9999.00'})
    quote=e.dealer.get('/api/quote/'+raw).json();assert quote['lines'][0]['sku']=='R6';assert quote['lines'][0]['name']=='R6 mini';assert quote['total']==580000
    e.act(e.admin,'order.cancel',{'order_id':oid,'reason':'Customer request'})
    assert e.dealer.get('/api/quote/'+raw).status_code==410
    oid=e.order();e.review(oid);e.act(e.admin,'receivable.adjust',{'order_id':oid,'amount':'-99.00','reason':'Approved correction'})
    assert row(e,oid)['receivable']==990000
    e.act(e.admin,'order.cancel',{'order_id':oid,'reason':'Cancel unshipped unpaid'})
    assert row(e,oid)['receivable']==0;assert row(e,oid)['outstanding']==0

def test_export_filters_and_receivable_reconciliation(env):
    e=env;e.stock(20);oid=e.order();e.review(oid);rid=e.receipt('6000.00');e.allocate(rid,[{'order_id':oid,'amount':'5800.00'}])
    data=e.data();assert data['metrics']['receivable']-data['metrics']['allocated']==data['metrics']['outstanding'];assert data['metrics']['verified']-data['metrics']['allocated']==data['metrics']['credit']
    raw=e.sales.get('/api/export/orders?product_id='+str(e.pid)).content;wb=load_workbook(io.BytesIO(raw));assert wb.active.max_row==2
    raw=e.sales.get('/api/export/orders?dealer_id='+str(e.did2)).content;wb=load_workbook(io.BytesIO(raw));assert wb.active.max_row==1
    raw=e.admin.get('/api/export/inventory?warehouse_id='+str(e.wid)+'&from=2026-01-01&to=2027-01-01').content;wb=load_workbook(io.BytesIO(raw));assert wb.active.max_row==2
    assert wb.active.cell(2,8).value==20

def test_paid_commission_manual_adjustment_before_physical_return(env):
    e=env;e.stock();oid=e.order();e.review(oid);sid=e.ship(oid);e.act(e.warehouse,'shipment.action',{'shipment_id':sid,'action':'DELIVER'})
    rid=e.receipt();e.allocate(rid,[{'order_id':oid,'amount':'5800.00'}]);cid=e.act(e.admin,'commission.approve',{'order_id':oid})['id']
    e.act(e.finance,'commission.pay',{'commission_id':cid,'amount':'174.00','date':'2026-10-28'})
    e.act(e.admin,'shipment.action',{'shipment_id':sid,'action':'RETURN','actual_return':True,'reason':'Actual returned vehicles'},expected=400)
    e.act(e.admin,'commission.adjust',{'commission_id':cid,'amount':'-174.00','reason':'Returned vehicles, manual payroll review'})
    e.act(e.admin,'shipment.action',{'shipment_id':sid,'action':'RETURN','actual_return':True,'reason':'Actual returned vehicles'})
    assert e.data()['commissions'][0]['status']=='REVIEW_REQUIRED';assert e.data()['commissions'][0]['adjustments']==-17400

def test_import_template_native_date_and_pending_only(env):
    e=env;wb=load_workbook(io.BytesIO(e.admin.get('/api/import-template').content))
    assert wb.sheetnames==['products','dealers','stock','consignment','receivables','credit']
    from datetime import datetime
    ws=wb['receivables'];ws.cell(2,1,'AR-NATIVE-DATE');ws.cell(2,2,'D1');ws.cell(2,3,123.45);ws.cell(2,4,datetime(2026,12,1));ws.cell(2,4).number_format='yyyy-mm-dd';ws.cell(2,5,1)
    out=io.BytesIO();wb.save(out)
    r=e.admin.post('/api/import-preview',data={'cutover':'2026-10-28','source':'Native Excel dates'},files={'file':('native-date.xlsx',out.getvalue())},headers=csrf(e.admin));assert r.status_code==200,r.text
    result=r.json();assert not result['errors'];e.act(e.admin,'import.commit',{'batch_id':result['id']})
    assert e.data()['orders'][0]['due']=='2026-12-01';assert e.data()['metrics']['verified']==0
