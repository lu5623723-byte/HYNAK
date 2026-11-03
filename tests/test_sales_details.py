import io
import json
import sqlite3
from pathlib import Path
import pytest
from sqlalchemy import event, select, func
from openpyxl import load_workbook
from fastapi.testclient import TestClient
from app.db import Session, DATA_DIR, migrate, engine
from app.models import Order, Claim, Operation, Receivable, StockLedger
from app.core import balance

PROOF=b'\x89PNG\r\n\x1a\n'+b'isolated-test-payment-proof'

def payload(e, kind='CONSIGNMENT', **overrides):
    return dict(dealer_id=e.did, sale_type=kind, sale_date='2026-10-28',
                due='2026-11-28', payment_at='2026-10-28T14:30',
                items=[dict(product_id=e.pid,quantity=1)], **overrides)

def post_order(e, data, proof=None, client=None, key='new-order-with-details', oid=None):
    c=client or (e.admin if data.get('sale_type')=='CONSIGNMENT' else e.dealer)
    headers={'X-CSRF-Token':c.get('/api/data').json()['csrf'],'Idempotency-Key':key}
    files={'file':('payment.png',proof,'image/png')} if proof is not None else None
    url=f'/api/orders/{oid}/revise' if oid else '/api/orders'
    return c.post(url, data={'payload':json.dumps(data)}, files=files, headers=headers)

def order(e,oid):return next(o for o in e.data()['orders'] if o['id']==oid)

def approve_consignment(e,oid):
    e.act(e.admin,'order.submit',{'order_id':oid})
    e.review(oid)
    token=e.act(e.admin,'order.token',{'order_id':oid})['token']
    assert e.dealer.post('/api/quote/'+token,json={'action':'CONFIRM'}).status_code==200

def test_consignment_date_and_due_are_required(env):
    e=env
    for missing in ['sale_date','due']:
        d=payload(e);d.pop(missing)
        r=post_order(e,d);assert r.status_code==400,r.text
    r=post_order(e,payload(e));assert r.status_code==200,r.text
    oid=r.json()['id'];approve_consignment(e,oid)
    o=order(e,oid)
    assert o['sale_date']=='2026-10-28' and o['sale_type']=='CONSIGNMENT'
    assert o['due']=='2026-11-28' and o['payment_at'] is None
    assert o['receivable']==580000 and e.data()['metrics']['month_sales']==0

@pytest.mark.parametrize('change,proof',[
    ({'payment_at':''},PROOF), ({'payment_at':'2026-99-01T14:00'},PROOF),
    ({},None), ({},b'not-a-payment-image'),
    ({},b'%PDF-'+b'x'*(5*1024*1024)),
])
def test_cash_requires_valid_payment_time_and_proof(env,change,proof):
    e=env;d=payload(e,'CASH');d.update(change)
    files=set((DATA_DIR/'attachments').iterdir())
    r=post_order(e,d,proof)
    assert r.status_code==400,r.text
    assert not e.data()['orders'] and not e.data()['claims']
    assert set((DATA_DIR/'attachments').iterdir())==files

def test_cash_order_proof_is_atomic_private_and_not_verified_receipt(env):
    e=env;r=post_order(e,payload(e,'CASH'),PROOF);assert r.status_code==200,r.text
    oid,cid=r.json()['id'],r.json()['claim_id']
    o=order(e,oid);claim=e.data()['claims'][0]
    assert o['sale_date']=='2026-10-28' and o['payment_at']=='2026-10-28T14:30:00-03:00'
    assert o['due']=='2026-10-28' and claim['paid_at']==o['payment_at']
    assert claim['amount']==580000 and claim['status']=='PENDING'
    assert e.data()['metrics']['verified']==0 and o['receivable']==0
    assert e.dealer.get(f'/api/claims/{cid}/attachment').content==PROOF
    assert e.dealer2.get(f'/api/claims/{cid}/attachment').status_code==403
    e.act(e.admin,'order.review',dict(order_id=oid),expected=403)
    e.act(e.finance,'receipt.create',dict(dealer_id=e.did,account_id=e.aid,claim_id=cid,amount='5800.00',date='2026-10-28',reference='FULL-CASH-PAYMENT'))
    assert order(e,oid)['receivable']==580000 and order(e,oid)['outstanding']==0 and e.data()['metrics']['verified']==580000
    raw=e.dealer.get('/api/export/orders').content
    wb=load_workbook(io.BytesIO(raw));values=list(wb.active.values)
    assert 'sale_date' in values[0] and 'sale_type' in values[0] and 'payment_at' in values[0]

def test_cash_idempotency_and_json_cannot_bypass_proof(env):
    e=env;d=payload(e,'CASH')
    e.act(e.dealer,'order.create',{**d,'cash_proof':True},expected=400)
    one=post_order(e,d,PROOF);two=post_order(e,d,PROOF)
    assert one.status_code==two.status_code==200 and one.json()==two.json()
    assert len(e.data()['orders'])==len(e.data()['claims'])==1
    assert post_order(e,d,PROOF+b'changed').status_code==409

def test_database_failure_rolls_back_order_and_uploaded_file(env):
    e=env;files=set((DATA_DIR/'attachments').iterdir())
    def reject(*args):raise RuntimeError('Simulated database write failure')
    event.listen(Operation,'before_insert',reject)
    try:
        with pytest.raises(RuntimeError,match='Simulated database'):
            post_order(e,payload(e,'CASH'),PROOF)
    finally:event.remove(Operation,'before_insert',reject)
    assert not e.data()['orders'] and not e.data()['claims']
    assert set((DATA_DIR/'attachments').iterdir())==files

def test_revised_cash_time_needs_new_proof_and_revokes_quote(env):
    e=env;r=post_order(e,payload(e,'CASH'),PROOF,client=e.sales)
    assert r.status_code==200,r.text
    oid=r.json()['id']
    assert order(e,oid)['status']=='WAITING_FINANCE_REVIEW'
    e.act(e.sales,'order.token',{'order_id':oid},expected=403)
    d=payload(e,'CASH');d.update(reason='Correction of reported payment time',payment_at='2026-10-29T10:45')
    assert post_order(e,d,client=e.admin,oid=oid,key='cash-revision-no-proof').status_code==400
    r=post_order(e,d,PROOF+b'new-proof',client=e.admin,oid=oid,key='cash-revision-new-proof')
    assert r.status_code==200,r.text
    assert order(e,oid)['version']==2 and order(e,oid)['payment_at']=='2026-10-29T10:45:00-03:00'
    claims=e.data()['claims'];assert sorted(c['status'] for c in claims)==['PENDING','SUPERSEDED']

def test_consignment_dispatch_and_cancel_preserve_hynak_stock(env):
    e=env;e.stock(10);r=post_order(e,payload(e));assert r.status_code==200,r.text
    oid=r.json()['id'];approve_consignment(e,oid)
    e.act(e.warehouse,'shipment.create',{'order_id':oid,'warehouse_id':e.wid,'exception':'Missing legacy SN','reason':'Consignment dispatch'},expected=400)
    sid=e.ship(oid,consignment_location_id=e.cwid)
    with Session() as db:
        assert balance(db,e.wid,e.pid)==9 and balance(db,e.cwid,e.pid)==1
        assert db.scalar(select(func.count(Receivable.id)))==1
    e.act(e.warehouse,'shipment.action',{'shipment_id':sid,'action':'CANCEL','reason':'Not picked up'})
    with Session() as db:assert balance(db,e.wid,e.pid)==10 and balance(db,e.cwid,e.pid)==0
    assert not order(e,oid)['commission_eligible']

def test_consignment_sold_report_does_not_duplicate_original_receivable(env):
    e=env;e.stock(5);oid=post_order(e,payload(e)).json()['id'];approve_consignment(e,oid)
    sid=e.ship(oid,consignment_location_id=e.cwid)
    e.act(e.warehouse,'shipment.action',{'shipment_id':sid,'action':'DELIVER'})
    report=e.act(e.dealer,'consignment.report',{'source_order_id':oid,'warehouse_id':e.cwid,'product_id':e.pid,'quantity':1})['id']
    sold=e.act(e.admin,'consignment.confirm',{'report_id':report,'due':'2026-11-28'})['id']
    assert sold==oid and order(e,oid)['receivable']==580000
    assert e.data()['metrics']['receivable']==580000

def test_legacy_migration_preserves_orders_and_is_repeatable(tmp_path):
    import os, subprocess, sys
    data=tmp_path/'legacy';data.mkdir();database=data/'hynak.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE schema_versions (version INTEGER PRIMARY KEY)')
        db.execute('INSERT INTO schema_versions VALUES (1)')
        # Create a genuine pre-upgrade schema by stripping only the added columns.
        from sqlalchemy.schema import CreateTable
        for table in [Order.__table__,Claim.__table__]:
            ddl=str(CreateTable(table).compile(engine))
            ddl='\n'.join(line for line in ddl.splitlines() if not any(x in line for x in ['sale_date VARCHAR','sale_type VARCHAR','payment_at VARCHAR','paid_at VARCHAR']))
            db.execute(ddl)
        db.execute("INSERT INTO orders(id,dealer_id,creator_id,mode,status,version,due,total,freight,partial_allowed,created,source) VALUES(1,1,1,'SELF','CONFIRMED',1,'2026-11-01',580000,0,0,'2026-10-01T00:00:00+00:00','CURRENT')")
    code='from app.db import migrate; migrate(); migrate()'
    subprocess.run([sys.executable,'-c',code],env={**os.environ,'HYNAK_DATA_DIR':str(data),'PYTHONPATH':str(Path(__file__).resolve().parents[1])},check=True)
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT total,sale_date,sale_type,payment_at FROM orders').fetchone()==(580000,None,'WHOLESALE',None)
        assert db.execute('SELECT COUNT(*) FROM schema_versions WHERE version=2').fetchone()[0]==1
