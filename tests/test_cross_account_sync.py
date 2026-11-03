"""Mutations by one account are consistently visible in fresh reads by others."""
import json
import re
from datetime import datetime
from app.core import today
from app import frontend
from test_finance_activation import PROOF


def readers(e):return [e.admin,e.finance,e.sales,e.warehouse,e.dealer]
def row(e,c,oid):return next(o for o in e.data(c)['orders'] if o['id']==oid)


def test_every_role_uses_same_versioned_assets_and_uncached_data(env):
    e=env;versions=set()
    for c in readers(e):
        r=c.get('/api/data');assert r.status_code==200
        d=r.json();v=d['app_version'];versions.add(v)
        assert len(v)==16 and datetime.fromisoformat(d['server_time']).tzinfo
        assert r.headers['cache-control']=='no-store'
        html=c.get('/').text
        assert f'data-app-version="{v}"' in html
        for path in ['app.js','app.css','ranking-charts.js','inventory-scope.js','session-sync.js']:
            assert f'/static/{path}?v={v}' in html
        assert c.get('/static/app.js?v='+v).headers['cache-control']=='no-store'
    assert len(versions)==1


def test_admin_product_and_warehouse_changes_propagate_without_relogin(env):
    e=env;before=[e.data(c) for c in readers(e)]
    e.act(e.admin,'product.save',dict(id=e.pid,sku='R6',name='Synced model name',price='6100.00',active=True))
    wid=e.act(e.admin,'warehouse.save',dict(code='SYNC-W2',name='Shared new secondary',kind='SECONDARY',location='Campinas'))['id']
    e.act(e.warehouse,'stock.move',dict(kind='RECEIVE',warehouse_id=wid,reason='Isolated synchronization test',items=[dict(product_id=e.pid,quantity=3)]))
    for c in readers(e):
        d=e.data(c);p=next(p for p in d['products'] if p['id']==e.pid)
        assert (p['name'],p['price'],p['available_quantity'])==('Synced model name',610000,3)
        if c is not e.dealer:
            assert next(w for w in d['warehouses'] if w['id']==wid)['kind']=='SECONDARY'
            assert next(i for i in d['inventory'] if i['warehouse_id']==wid)['quantity']==3
        else:assert all(w['id']!=wid for w in d['warehouses'])
    site=e.act(e.admin,'warehouse.save',dict(code='SYNC-C',name='Synced own consignment',kind='CONSIGNMENT',dealer_id=e.did,location='São Paulo'))['id']
    e.act(e.warehouse,'stock.move',dict(kind='CONSIGN_SEND',warehouse_id=wid,target_id=site,reason='Isolated sync send',items=[dict(product_id=e.pid,quantity=1)]))
    for c in readers(e):
        d=e.data(c)
        assert next(i for i in d['inventory'] if i['warehouse_id']==site)['quantity']==1
        assert next(p for p in d['products'] if p['id']==e.pid)['available_quantity']==2
    assert all(w['id']!=site for w in e.data(e.dealer2)['warehouses'])


def test_sales_order_finance_payment_and_recharge_sync_same_ledger_to_all_roles(env):
    e=env;e.stock(10)
    d=dict(dealer_id=e.did,sale_type='CONSIGNMENT',sale_date='2026-10-01',due='2026-10-31',items=[dict(product_id=e.pid,quantity=1)],payments=[dict(amount='1000.00',paid_date='2026-10-28'),dict(amount='2000.00',paid_date='2026-10-30')])
    r=e.sales.post('/api/orders',data={'payload':json.dumps(d)},files=[('payment_proof_0',('one.pdf',PROOF+b'one','application/pdf')),('payment_proof_1',('two.pdf',PROOF+b'two','application/pdf'))],headers={'X-CSRF-Token':e.data(e.sales)['csrf'],'Idempotency-Key':'cross-account-sync-order'})
    assert r.status_code==200,r.text;oid=r.json()['id'];cid=r.json()['claim_ids'][0]
    for c in readers(e):assert row(e,c,oid)['status']=='WAITING_INTERNAL_REVIEW'
    e.review(oid)
    e.act(e.finance,'receipt.create',dict(dealer_id=e.did,claim_id=cid,account_id=e.aid,amount='1000.00',date='2026-10-28',reference='SYNC-BANK-ONE'))
    for c in readers(e):
        o=row(e,c,oid);assert o['status']=='CONFIRMED'
        assert (o['receivable'],o['received'],o['outstanding'])==(580000,100000,480000)
        if c is not e.warehouse:
            assert o['payment_history'][0]['paid_date']=='2026-10-28'
            claims=[cl for cl in e.data(c)['claims'] if cl['order_id']==oid]
            assert sorted((cl['amount'],cl['status']) for cl in claims)==[(100000,'VERIFIED'),(200000,'PENDING')]
    e.act(e.admin,'receipt.create',dict(dealer_id=e.did,account_id=e.aid,amount='6000.00',date='2026-10-31',reference='SYNC-RECHARGE',is_recharge=True))
    scores=[]
    for c in readers(e):
        d=e.data(c);o=next(o for o in d['orders'] if o['id']==oid)
        assert (o['received'],o['outstanding'],o['payment_status'])==(580000,0,'PAID')
        if c is not e.warehouse:
            assert next(w for w in d['wallets'] if w['dealer_id']==e.did)['available']==120000
            scores.append(next(x for x in d['dealers'] if x['id']==e.did)['credit_rating'])
    assert all(score==scores[0] for score in scores)
    for c in [e.sales,e.dealer,e.warehouse]:
        e.act(c,'receipt.create',dict(dealer_id=e.did,account_id=e.aid,amount='1.00',date='2026-10-31',reference='CANNOT-VERIFY'),expected=403)


def test_published_policy_updates_sales_without_granting_dealer_policy_selection(env):
    e=env;e.data(e.sales);e.data(e.dealer)
    pid=e.act(e.admin,'policy.save',dict(name='Shared current policy',kind='STANDARD',starts=today().isoformat(),active=True,description='First version'))['id']
    assert next(p for p in e.data(e.sales)['policies'] if p['id']==pid)['description']=='First version'
    e.act(e.admin,'policy.save',dict(id=pid,name='Updated shared policy',description='New instructions'))
    assert next(p for p in e.data(e.sales)['policies'] if p['id']==pid)['description']=='New instructions'
    for c in [e.finance,e.warehouse,e.dealer]:assert e.data(c)['policies']==[]


def test_asset_version_changes_with_content_but_not_dealer_data(env,tmp_path,monkeypatch):
    e=env;original=e.data()['app_version']
    e.act(e.admin,'product.save',dict(id=e.pid,sku='R6',name='Another product name',price='5800.00'))
    assert e.data()['app_version']==original
    f=tmp_path/'one.js';f.write_text('version-one')
    monkeypatch.setattr(frontend,'ROOT',tmp_path);monkeypatch.setattr(frontend,'ASSETS',('one.js',))
    one=frontend.frontend_version();assert frontend.frontend_version()==one
    f.write_text('version-two-changed');assert frontend.frontend_version()!=one
