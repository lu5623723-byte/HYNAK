"""Atomic, dated multi-proof consignment creation and bank verification."""
import json
from datetime import timedelta
import pytest
from app import main
from app.core import today
from app.db import DATA_DIR
from test_finance_activation import PROOF
from test_sales_details import approve_consignment


def payload(e, count=3):
    return dict(dealer_id=e.did, sale_type='CONSIGNMENT', sale_date='2026-10-28',
                due='2026-11-30', items=[dict(product_id=e.pid,quantity=1)],
                payments=[dict(amount=str(1000+i*100)+'.00',paid_date=f'2026-10-{28+i:02d}',reference=f'TEST-CLAIM-{i}') for i in range(count)])


def post(e, d=None, actor=None, key=None, files=None, oid=None):
    d=payload(e) if d is None else d;c=actor or e.admin;e.n+=1
    if files is None:
        files=[(f'payment_proof_{i}',(f'test-{i}.pdf',PROOF+str(i).encode(),'application/pdf')) for i in range(len(d.get('payments',[])))]
    return c.post(f'/api/orders/{oid}/revise' if oid else '/api/orders',
                  data={'payload':json.dumps(d)},files=files,
                  headers={'X-CSRF-Token':e.data(c)['csrf'],'Idempotency-Key':key or f'consign-batch-{e.n}'})


def order(e,oid,actor=None):return next(o for o in e.data(actor)['orders'] if o['id']==oid)


def verify(e,cid,actor=None,amount='1000.00',expected=200):
    return e.act(actor or e.finance,'receipt.create',dict(dealer_id=e.did,account_id=e.aid,
                 claim_id=cid,amount=amount,date='2026-10-31',reference=f'TEST-BANK-{cid}'),expected=expected)


@pytest.mark.parametrize('creator',['admin','sales'])
def test_three_proofs_saved_with_dates_private_and_unverified(env,creator):
    e=env;r=post(e,actor=getattr(e,creator));assert r.status_code==200,r.text
    oid=r.json()['id'];ids=r.json()['claim_ids'];assert len(ids)==3
    for viewer in [e.admin,e.sales,e.finance,e.dealer]:
        d=e.data(viewer);claims=sorted(d['claims'],key=lambda c:c['id'])
        assert [(c['paid_date'],c['amount'],c['status']) for c in claims]==[(f'2026-10-{28+i:02d}',100000+i*10000,'PENDING') for i in range(3)]
        for i,cid in enumerate(ids):assert viewer.get(f'/api/claims/{cid}/attachment').content==PROOF+str(i).encode()
        assert order(e,oid,viewer)['receivable']==0 and d['metrics']['verified']==0
    assert not e.data()['receipts'] and not e.data()['allocations']
    assert e.dealer2.get(f'/api/claims/{ids[0]}/attachment').status_code==403
    for actor in [e.admin,e.finance]:verify(e,ids[0],actor,expected=409)
    assert not e.data()['receipts']
    if creator=='admin':approve_consignment(e,oid)
    else:e.review(oid)
    verify(e,ids[0],e.finance);verify(e,ids[1],e.admin,amount='1100.00');verify(e,ids[2],e.finance,amount='1200.00')
    o=order(e,oid);assert o['received']==330000 and o['outstanding']==250000 and o['payment_count']==3
    assert [c['status'] for c in e.data()['claims']]==['VERIFIED']*3
    verify(e,ids[0],expected=409)


def test_batch_retry_does_not_duplicate_and_changes_conflict(env):
    e=env;d=payload(e);a=post(e,d,key='same-batch-retry');b=post(e,d,key='same-batch-retry')
    assert a.status_code==b.status_code==200 and a.json()==b.json()
    assert len(e.data()['orders'])==1 and len(e.data()['claims'])==3
    files=[(f'payment_proof_{i}',('proof.pdf',PROOF+str(i).encode()+(b'changed' if i==2 else b''),'application/pdf')) for i in range(3)]
    assert post(e,d,key='same-batch-retry',files=files).status_code==409
    d['payments'][2]['paid_date']='2026-10-29'
    assert post(e,d,key='same-batch-retry').status_code==409
    d['payments'][2]['amount']='999.99'
    assert post(e,d,key='same-batch-retry').status_code==409
    assert len(e.data()['claims'])==3


@pytest.mark.parametrize('invalid',['amount','date','future','proof','missing','duplicate','too_many','wrong_type','non_list','orphan','reference'])
def test_invalid_batch_rolls_back_all_records_and_files(env,invalid):
    e=env;d=payload(e);files=None
    if invalid=='amount':d['payments'][2].pop('amount')
    if invalid=='date':d['payments'][2]['paid_date']='bad-date'
    if invalid=='future':d['payments'][2]['paid_date']=(today()+timedelta(days=1)).isoformat()
    if invalid in ['proof','missing','orphan']:
        files=[(f'payment_proof_{i}',('proof.pdf',PROOF+str(i).encode(),'application/pdf')) for i in range(3)]
        if invalid=='proof':files[2]=('payment_proof_2',('bad.txt',b'not proof','text/plain'))
        if invalid=='missing':files.pop()
        if invalid=='orphan':files.append(('payment_proof_9',('extra.pdf',PROOF,'application/pdf')))
    if invalid=='duplicate':
        d['payments'][2]=d['payments'][0].copy()
        files=[(f'payment_proof_{i}',('proof.pdf',PROOF+str(0 if i==2 else i).encode(),'application/pdf')) for i in range(3)]
    if invalid=='too_many':d['payments']=payload(e,20)['payments']+[payload(e,1)['payments'][0]];files=[]
    if invalid=='wrong_type':d.update(sale_type='OTHER',terms='Test only')
    if invalid=='non_list':d['payments']={};files=[]
    if invalid=='reference':d['payments'][2]['reference']='x'*201
    before=set((DATA_DIR/'attachments').iterdir());r=post(e,d,files=files)
    assert r.status_code==400,r.text
    assert not e.data()['orders'] and not e.data()['claims']
    assert set((DATA_DIR/'attachments').iterdir())==before


def test_mid_save_failure_cleans_earlier_proofs_and_rolls_back(env,monkeypatch):
    e=env;original=main.save_claim;calls=0
    def fail_last(*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls==3:raise RuntimeError('Simulated disk failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(main,'save_claim',fail_last);before=set((DATA_DIR/'attachments').iterdir())
    with pytest.raises(RuntimeError,match='Simulated disk failure'):post(e)
    assert not e.data()['orders'] and not e.data()['claims']
    assert set((DATA_DIR/'attachments').iterdir())==before


def test_revision_preserves_existing_proofs_and_appends_new(env):
    e=env;r=post(e);oid=r.json()['id'];old=r.json()['claim_ids']
    d=payload(e,1);d['reason']='TEST_ONLY revision';d['payments'][0]['amount']='500.00'
    r=post(e,d,oid=oid);assert r.status_code==200,r.text
    assert len(e.data()['claims'])==4 and all(c['status']=='PENDING' for c in e.data()['claims'])
    assert all(e.admin.get(f'/api/claims/{cid}/attachment').status_code==200 for cid in old)
    e.review(oid)
    token=e.act(e.admin,'order.token',{'order_id':oid})['token']
    assert e.dealer.post('/api/quote/'+token,json={'action':'CONFIRM'}).status_code==200
    verify(e,old[0]);assert post(e,d,oid=oid).status_code==409


@pytest.mark.parametrize('actor',['dealer','finance','warehouse'])
def test_unauthorized_role_cannot_create_consignment_with_proofs(env,actor):
    e=env;r=post(e,actor=getattr(e,actor));assert r.status_code==403,r.text
    assert not e.data()['orders'] and not e.data()['claims']


def test_sales_cannot_write_another_dealers_batch(env):
    e=env;d=payload(e);d['dealer_id']=e.did2
    assert post(e,d,actor=e.sales).status_code==403
    assert not e.data()['orders'] and not e.data()['claims']


def test_json_cannot_silently_drop_batch_and_unpaid_consignment_remains_supported(env):
    e=env;e.act(e.admin,'order.create',payload(e),expected=400)
    d=payload(e);d.pop('payments');r=post(e,d);assert r.status_code==200,r.text
    assert not e.data()['claims']
    e.act(e.admin,'order.revise',dict(**payload(e),order_id=r.json()['id'],reason='test'),expected=400)


def test_append_proof_to_pending_consignment_but_not_canceled(env):
    e=env;r=post(e);oid=r.json()['id']
    def append(date='2026-10-31'):
        return e.dealer.post('/api/claims',data=dict(order_id=oid,amount='20.00',paid_date=date),
            files={'file':('extra.pdf',PROOF,'application/pdf')},headers={'X-CSRF-Token':e.data(e.dealer)['csrf']})
    r=append();assert r.status_code==200,r.text
    verify(e,r.json()['id'],expected=409)
    assert append((today()+timedelta(days=1)).isoformat()).status_code==400
    e.act(e.admin,'order.cancel',dict(order_id=oid,reason='TEST_ONLY cancel'))
    assert append().status_code in [400,409]


@pytest.mark.parametrize('sizes',[(5*1024*1024+1,), (4*1024*1024,)*3])
def test_per_file_and_total_limits_without_partial_write(env,sizes):
    e=env;d=payload(e,len(sizes))
    files=[(f'payment_proof_{i}',('test.pdf',b'%PDF-'+bytes([65+i])*(size-5),'application/pdf')) for i,size in enumerate(sizes)]
    before=set((DATA_DIR/'attachments').iterdir());r=post(e,d,files=files)
    assert r.status_code in [400,413],r.text
    assert not e.data()['orders'] and not e.data()['claims'] and set((DATA_DIR/'attachments').iterdir())==before
