"""Verified dealer recharges, due-date matching, balance purchases and isolation."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from app.db import DATA_DIR,Session
from app.models import Order
from test_finance_activation import PROOF,create as bank_order,verify
from test_consignment_payments import consign,row


def topup(e,amount='1000.00',ref=None,did=None,actor=None,**kw):
    return e.act(actor or e.finance,'receipt.create',dict(dealer_id=did or e.did,account_id=e.aid,amount=amount,date='2026-11-01',reference=ref or f'TOPUP-{e.n}',is_recharge=True),**kw)


def wallet(e,actor=None,did=None):return next(w for w in e.data(actor)['wallets'] if w['dealer_id']==(did or e.did))


def balance_order(e,role='dealer',quantity=1,key=None,price=None):
    e.n+=1;c=getattr(e,role)
    item=dict(product_id=e.pid,quantity=quantity)
    if price:item['unit_price']=price
    d=dict(dealer_id=e.did,sale_type='CASH',payment_method='BALANCE',sale_date='2026-11-01',items=[item])
    r=c.post('/api/orders',data={'payload':json.dumps(d)},headers={'X-CSRF-Token':e.data(c)['csrf'],'Idempotency-Key':key or f'balance-order-{e.n}'})
    assert r.status_code==200,r.text
    return r.json()['id']


def recharge_claim(e,actor=None,did=None,key='recharge-claim-key',amount='1000.00'):
    c=actor or e.dealer;d=dict(amount=amount,paid_date='2026-11-01',reference='CUSTOMER-TRANSFER')
    if did:d['dealer_id']=did
    return c.post('/api/recharges',data=d,files={'file':('proof.pdf',PROOF,'application/pdf')},headers={'X-CSRF-Token':e.data(c)['csrf'],'Idempotency-Key':key})


@pytest.mark.parametrize('reviewer',['finance','admin'])
def test_claim_requires_verification_and_duplicate_submit_keeps_one_proof(env,reviewer):
    e=env;r=recharge_claim(e);assert r.status_code==200,r.text
    assert recharge_claim(e).json()==r.json()
    assert len(e.data()['recharges'])==1 and wallet(e)['available']==0
    rid=r.json()['id'];assert e.dealer.get(f'/api/recharges/{rid}/attachment').status_code==200
    e.act(getattr(e,reviewer),'receipt.create',dict(dealer_id=e.did,account_id=e.aid,recharge_id=rid,amount='900.00',date='2026-11-01',reference='REAL-TOPUP'))
    assert wallet(e)['available']==90000
    assert e.data(e.dealer)['recharges'][0]['status']=='VERIFIED'
    assert e.data(e.dealer)['receipts'][0]['amount']==90000


def test_due_date_matching_is_automatic_and_remainder_stays_in_wallet(env):
    e=env;a=consign(e);b=consign(e)
    e.act(e.admin,'receivable.due',dict(order_id=b,due='2026-11-02',reason='Earlier due date'))
    topup(e,'7000.00')
    assert row(e,b)['received']==580000 and row(e,a)['received']==120000
    assert wallet(e)['available']==0
    topup(e,'5000.00')
    assert row(e,a)['received']==580000 and wallet(e)['available']==40000
    assert sum(x['amount'] for x in wallet(e)['entries'])==40000


def test_future_confirmed_consignment_uses_existing_credit_once(env):
    e=env;topup(e,'6000.00');oid=consign(e)
    assert row(e,oid)['outstanding']==0 and row(e,oid)['received']==580000
    assert wallet(e)['available']==20000


@pytest.mark.parametrize('role',['dealer','sales','admin'])
def test_prepaid_purchase_needs_no_duplicate_transfer_proof(env,role):
    e=env;e.stock(5);topup(e,'6000.00');oid=balance_order(e,role)
    o=row(e,oid);assert o['status']=='CONFIRMED' and o['payment_method']=='BALANCE'
    assert o['received']==o['receivable']==580000 and o['outstanding']==0 and o['reserved']==1
    assert wallet(e)['available']==20000 and not e.data()['claims']


def test_insufficient_balance_waits_and_later_recharge_activates(env):
    e=env;topup(e,'1000.00');oid=balance_order(e)
    assert not row(e,oid)['confirmed'] and row(e,oid)['receivable']==0
    assert wallet(e)['available']==100000
    topup(e,'5000.00')
    assert row(e,oid)['status']=='CONFIRMED' and row(e,oid)['received']==580000
    assert wallet(e)['available']==20000


def test_custom_price_balance_order_still_requires_admin_approval(env):
    e=env;topup(e,'6000.00');oid=balance_order(e,'sales',price='5000.00')
    assert row(e,oid)['admin_review_pending'] and row(e,oid)['status']=='WAITING_INTERNAL_REVIEW'
    assert wallet(e)['available']==600000
    e.review(oid)
    assert row(e,oid)['status']=='CONFIRMED' and row(e,oid)['received']==500000
    assert wallet(e)['available']==100000


def test_existing_effective_debt_has_priority_over_pending_balance_purchase(env):
    e=env;old=consign(e);pending=balance_order(e)
    topup(e,'6000.00')
    assert row(e,old)['outstanding']==0 and not row(e,pending)['confirmed']
    assert wallet(e)['available']==20000


def test_partial_bank_payment_is_held_for_original_cash_order_not_spent_twice(env):
    e=env;made=bank_order(e);verify(e,made['claim_id'],'3000.00','ORIGINAL-PARTIAL')
    owed=consign(e);topup(e,'1000.00')
    assert row(e,owed)['received']==100000
    assert wallet(e)['held']==300000 and wallet(e)['available']==0 and wallet(e)['total']==300000
    verify_body=dict(dealer_id=e.did,account_id=e.aid,amount='2800.00',date='2026-11-01',reference='UNRELATED-TOPUP',is_recharge=True)
    e.act(e.finance,'receipt.create',verify_body)
    assert not row(e,made['id'])['confirmed'] and row(e,owed)['received']==380000


@pytest.mark.parametrize('viewer',['admin','finance','sales','dealer'])
def test_balances_and_applied_order_details_are_shared_with_appropriate_viewers(env,viewer):
    e=env;oid=consign(e);topup(e,'6000.00');w=wallet(e,getattr(e,viewer))
    assert w['available']==20000 and w['applied']==580000 and w['credited']==600000
    assert any(x['order_id']==oid and x['amount']==-580000 for x in w['entries'])
    assert all('account_id' not in x and 'reference' not in x for x in w['entries'])
    assert len(e.data(e.dealer2)['wallets'])==1 and e.data(e.dealer2)['wallets'][0]['dealer_id']==e.did2


def test_claim_and_attachment_access_are_dealer_scoped(env):
    e=env;r=recharge_claim(e);rid=r.json()['id']
    assert e.dealer2.get(f'/api/recharges/{rid}/attachment').status_code==403
    assert not e.data(e.dealer2)['recharges']
    assert recharge_claim(e,did=e.did2,key='forge-another-dealer').status_code==403
    assert e.warehouse.get(f'/api/recharges/{rid}/attachment').status_code==403


@pytest.mark.parametrize('role',['sales','dealer','warehouse'])
def test_read_only_roles_cannot_credit_real_balance(env,role):
    e=env;topup(e,actor=getattr(e,role),expected=403)
    assert wallet(e)['available']==0


def test_rejected_claim_changes_no_balance_and_cannot_be_verified(env):
    e=env;rid=recharge_claim(e).json()['id']
    e.act(e.finance,'recharge.reject',dict(recharge_id=rid,reason='No matching bank transfer'))
    e.act(e.finance,'receipt.create',dict(recharge_id=rid,dealer_id=e.did,account_id=e.aid,amount='1000.00',date='2026-11-01',reference='REJECTED-CLAIM'),expected=409)
    assert wallet(e)['available']==0


def test_duplicate_recharge_bank_reference_and_idempotency_never_double_credit(env):
    e=env;a=topup(e,'1000.00',ref='ONE-BANK-REF',key='topup-repeat-key')
    assert topup(e,'1000.00',ref='ONE-BANK-REF',key='topup-repeat-key')==a
    topup(e,'1000.00',ref='ONE-BANK-REF',expected=409)
    assert wallet(e)['available']==100000


def test_concurrent_prepaid_orders_cannot_overspend_one_balance(env):
    e=env;topup(e,'5800.00');csrf=e.data(e.dealer)['csrf']
    d=dict(sale_type='CASH',payment_method='BALANCE',sale_date='2026-11-01',items=[dict(product_id=e.pid,quantity=1)])
    def buy(n):return e.dealer.post('/api/orders',data={'payload':json.dumps(d)},headers={'X-CSRF-Token':csrf,'Idempotency-Key':f'prepaid-concurrent-{n}'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:assert list(pool.map(buy,[1,2]))==[200,200]
    assert sum(bool(o['confirmed']) for o in e.data()['orders'])==1
    assert wallet(e)['available']==0 and e.data()['metrics']['allocated']==580000


def test_pending_balance_order_cannot_upload_another_bank_payment_claim(env):
    e=env;oid=balance_order(e)
    r=e.dealer.post('/api/claims',data=dict(order_id=oid,amount='1000.00',paid_date='2026-11-01'),files={'file':('proof.pdf',PROOF,'application/pdf')},headers={'X-CSRF-Token':e.data(e.dealer)['csrf']})
    assert r.status_code==409


def test_balance_purchase_retry_does_not_spend_twice(env):
    e=env;topup(e,'12000.00');a=balance_order(e,key='prepaid-order-idempotent')
    b=balance_order(e,key='prepaid-order-idempotent')
    assert a==b and wallet(e)['available']==620000


def test_demo_can_activate_an_underfunded_prepaid_order_at_approved_discount(env):
    from test_demo_selection import demo
    e=env;topup(e,'5000.00');oid=balance_order(e)
    assert not row(e,oid)['confirmed']
    demo(e,oid)
    assert row(e,oid)['received']==464000 and row(e,oid)['status']=='CONFIRMED'
    assert wallet(e)['available']==36000


def test_verified_opening_credit_is_usable_without_inventing_a_bank_date(env):
    from app.models import OpeningCredit
    with Session() as db:db.add(OpeningCredit(dealer_id=env.did,amount=600000,source='Verified test opening'));db.commit()
    e=env;oid=balance_order(e)
    assert row(e,oid)['received']==580000 and wallet(e)['available']==20000
    assert row(e,oid)['payment_history'][0]['source']=='OPENING_CREDIT'
    assert row(e,oid)['payment_history'][0]['paid_date'] is None


def test_recharge_proof_is_included_in_backup_restore(env,tmp_path):
    import sqlite3
    from app.backup import backup,restore
    e=env;rid=recharge_claim(e).json()['id'];saved=backup(True)
    dest=restore(DATA_DIR/'backups'/saved['file'],tmp_path/'restored')
    with sqlite3.connect(dest/'hynak.sqlite3') as db:
        name,status=db.execute('SELECT attachment,status FROM dealer_recharges WHERE id=?',(rid,)).fetchone()
        assert status=='PENDING' and (dest/'attachments'/name).read_bytes()==PROOF
