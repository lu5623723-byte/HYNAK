"""Latest business policy: dealers pay cash; sales consignments require finance review."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from app.db import DATA_DIR, Session
from app.models import Operation
from app.core import digest

PROOF=b'%PDF-1.4\n% Explicit isolated test fixture, not a bank receipt.\n%%EOF'

def data(e,kind='CASH'):
    return dict(dealer_id=e.did,sale_type=kind,sale_date='2026-10-30',due='2026-11-30',
                payment_at='2026-10-30T12:00',items=[dict(product_id=e.pid,quantity=1)])

def post(e,client,payload,key='cash-only-form',proof=None):
    return client.post('/api/orders',data={'payload':json.dumps(payload)},
                       files={'file':('test.pdf',proof,'application/pdf')} if proof is not None else None,
                       headers={'X-CSRF-Token':e.data(client)['csrf'],'Idempotency-Key':key})

@pytest.mark.parametrize('role',['dealer','finance','warehouse'])
@pytest.mark.parametrize('endpoint',['json','form'])
def test_dealer_and_non_order_roles_cannot_create_consignment(env,role,endpoint):
    e=env;client=getattr(e,role);payload=data(e,'CONSIGNMENT')
    files=set((DATA_DIR/'attachments').iterdir())
    if endpoint=='json':e.act(client,'order.create',payload,expected=403)
    else:assert post(e,client,payload).status_code==403
    assert e.data()['orders']==[] and e.data()['claims']==[]
    assert set((DATA_DIR/'attachments').iterdir())==files

@pytest.mark.parametrize('kind',['WHOLESALE',None,'CONSIGNMENT','OTHER'])
def test_dealer_cannot_use_legacy_or_missing_type_to_bypass_cash(env,kind):
    e=env;payload=data(e)
    if kind is None:payload.pop('sale_type')
    else:payload['sale_type']=kind
    payload.update(role='admin',cash_proof=True)
    e.act(e.dealer,'order.create',payload,expected=403)
    assert post(e,e.dealer,payload,proof=PROOF).status_code==403
    assert not e.data()['orders']

def test_dealer_cash_requires_time_and_real_upload_and_remains_unverified(env):
    e=env;payload=data(e)
    e.act(e.dealer,'order.create',{**payload,'cash_proof':True},expected=400)
    assert post(e,e.dealer,payload).status_code==400
    assert post(e,e.dealer,{**payload,'payment_at':''},proof=PROOF).status_code==400
    response=post(e,e.dealer,payload,proof=PROOF)
    assert response.status_code==200,response.text
    own=e.data(e.dealer)
    assert own['orders'][0]['sale_type']=='CASH'
    assert own['orders'][0]['mode']=='SELF' and own['orders'][0]['status']=='WAITING_FINANCE_REVIEW'
    assert own['claims'][0]['status']=='PENDING' and own['metrics']['verified']==0
    # A cash request replay is still safe and creates neither a second order nor proof.
    again=post(e,e.dealer,payload,proof=PROOF)
    assert again.json()==response.json() and len(e.data()['claims'])==1

def test_administrator_consignment_does_not_require_cash_proof(env):
    e=env;payload=data(e,'CONSIGNMENT');payload.pop('payment_at')
    response=post(e,e.admin,payload)
    assert response.status_code==200,response.text
    order=e.data()['orders'][0]
    assert order['sale_type']=='CONSIGNMENT' and order['due']=='2026-11-30'
    assert order['status']=='DRAFT' and order['creator_id']==e.data()['user']['id']
    assert e.data()['claims']==[] and order['receivable']==0

def test_old_idempotency_response_cannot_bypass_new_permission(env):
    e=env;payload=data(e,'CONSIGNMENT');actor=e.data(e.dealer)['user']['id']
    key='old-consignment-key'
    with Session() as db:
        db.add(Operation(key=f'{actor}:{key}',payload_hash=digest('order.create'+json.dumps(payload,sort_keys=True,ensure_ascii=False)),result='{"id":999}'))
        db.commit()
    e.act(e.dealer,'order.create',payload,key=key,expected=403)
    assert post(e,e.dealer,payload,key=key).status_code==403
    assert e.data()['orders']==[]

def test_new_demo_installation_uses_cash_orders_and_unverified_proofs(tmp_path):
    code='''from app.db import migrate,Session
from app.demo_seed import seed_demo
from app.models import Order,Claim,Receipt
from sqlalchemy import select,func
migrate();seed_demo();seed_demo()
with Session() as db:
 assert db.scalar(select(func.count(Order.id)))==4
 assert all(o.sale_type=='CASH' for o in db.scalars(select(Order)))
 assert db.scalar(select(func.count(Claim.id)))==4
 assert all(c.status=='PENDING' for c in db.scalars(select(Claim)))
 assert db.scalar(select(func.count(Receipt.id)))==0
'''
    subprocess.run([sys.executable,'-c',code],env={**os.environ,'HYNAK_DATA_DIR':str(tmp_path/'new-demo'),'PYTHONPATH':str(Path(__file__).resolve().parents[1])},check=True)

@pytest.mark.parametrize('role',['dealer','sales'])
@pytest.mark.parametrize('promotion',['DEMO','TEN_PLUS_ONE'])
def test_only_admin_selects_promotions_on_both_endpoints(env,role,promotion):
    e=env;payload={**data(e),'promotion':promotion,'role':'admin'}
    client=getattr(e,role)
    e.act(client,'order.create',payload,expected=403)
    assert post(e,client,payload,proof=PROOF).status_code==403
    assert e.data()['orders']==[] and e.data()['claims']==[]

def test_dealer_cannot_restore_removed_payment_terms(env):
    e=env;payload={**data(e),'special_7030':True}
    e.act(e.dealer,'order.create',payload,expected=403)
    assert post(e,e.dealer,payload,proof=PROOF).status_code==403
    assert e.data()['orders']==[]

def test_admin_can_apply_promotion_to_dealer_purchase(env):
    e=env;payload={**data(e),'terms':'Dealer purchase note'}
    response=post(e,e.dealer,payload,proof=PROOF)
    assert response.status_code==200,response.text
    oid=response.json()['id']
    e.act(e.admin,'order.revise',dict(order_id=oid,promotion='DEMO',reason='Approved promotion',items=payload['items']))
    order=e.data(e.dealer)['orders'][0]
    assert order['promotion']=='DEMO' and order['total']==464000
    assert order['terms']=='Dealer purchase note'
