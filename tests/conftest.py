import os, tempfile, json
from pathlib import Path
os.environ['HYNAK_DATA_DIR']=tempfile.mkdtemp(prefix='hynak-test-')
os.environ['PUBLIC_BASE_URL']='http://127.0.0.1:8765'
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import engine, DATA_DIR

class Env:
    def __init__(self,admin):self.admin=admin;self.clients={};self.n=0
    def login(self,username,password='Testing-12345'):
        c=TestClient(app);assert c.post('/api/login',json={'username':username,'password':password}).status_code==200
        self.clients[username]=c;return c
    def act(self,c,name,data,key=None,expected=200):
        self.n+=1;csrf=c.get('/api/data').json()['csrf']
        r=c.post('/api/action/'+name,json=data,headers={'X-CSRF-Token':csrf,'Idempotency-Key':key or f'test-key-{self.n}'})
        assert r.status_code==expected,r.text
        return r.json()
    def data(self,c=None):return (c or self.admin).get('/api/data').json()
    def legacy_order(self,c=None,quantity=1,promotion='STANDARD',due='2026-01-01',**kw):
        """Seed pre-policy wholesale records for ledger/history regression tests.

        Current cash workflows must be tested through /api/orders, not this fixture.
        """
        from app.db import Session
        from app.models import User,Order
        from app import orders
        actor=self.data(c or self.dealer)['user']
        did=actor['dealer_id'] if actor['role']=='dealer' else self.did
        with Session() as db:
            admin=db.get(User,self.data()['user']['id'])
            oid=orders.create_order(db,admin,dict(dealer_id=did,sale_type='WHOLESALE',sale_date=due,due=due,promotion=promotion,items=[dict(product_id=self.pid,quantity=quantity)],**kw))['id']
            o=db.get(Order,oid);o.creator_id=actor['id'];o.mode='SELF' if actor['role']=='dealer' else 'ASSISTED'
            o.status='WAITING_INTERNAL_REVIEW' if o.mode=='SELF' else 'DRAFT'
            db.commit()
        return oid
    def review(self,oid,**kw):return self.act(self.admin,'order.review',{'order_id':oid,**kw})
    def receipt(self,amount='5800.00',reference=None):return self.act(self.finance,'receipt.create',{'dealer_id':self.did,'account_id':self.aid,'amount':amount,'date':'2026-10-28','reference':reference or f'BANK-{self.n}'})['id']
    def allocate(self,rid,items,**kw):return self.act(self.finance,'receipt.allocate',{'dealer_id':self.did,'receipt_id':rid,'items':items},**kw)
    def stock(self,qty=100):return self.act(self.admin,'stock.move',{'warehouse_id':self.wid,'product_id':self.pid,'quantity':qty,'kind':'OPENING','reason':'Verified test stock'})
    def ship(self,oid,c=None,**kw):return self.act(c or self.warehouse,'shipment.create',{'order_id':oid,'warehouse_id':self.wid,'exception':'Missing legacy serial documented','reason':'Test dispatch',**kw})['id']

@pytest.fixture
def env():
    engine.dispose()
    for name in ['hynak.sqlite3','hynak.sqlite3-wal','hynak.sqlite3-shm']:(DATA_DIR/name).unlink(missing_ok=True)
    with TestClient(app) as a:
        assert a.post('/api/setup',json={'username':'admin','name':'Admin','password':'Testing-12345'}).status_code==200
        assert a.post('/api/login',json={'username':'admin','password':'Testing-12345'}).status_code==200
        e=Env(a)
        e.sid=e.act(a,'user.save',{'username':'sales','name':'Sales','password':'Testing-12345','role':'sales','commission_bps':300})['id']
        e.other_sid=e.act(a,'user.save',{'username':'other-sales','name':'Other sales','password':'Testing-12345','role':'sales'})['id']
        e.did=e.act(a,'dealer.save',{'code':'D1','name':'Dealer 1','sales_id':e.sid,'phone':'+5511991234567','language':'pt'})['id']
        e.did2=e.act(a,'dealer.save',{'code':'D2','name':'Dealer 2','sales_id':e.other_sid,'phone':'+5521991234567','language':'en'})['id']
        for username,role,did in [('dealer','dealer',e.did),('other-dealer','dealer',e.did2),('finance','finance',None),('warehouse','warehouse',None)]:e.act(a,'user.save',{'username':username,'name':username,'password':'Testing-12345','role':role,'dealer_id':did})
        e.sales=e.login('sales');e.dealer=e.login('dealer');e.dealer2=e.login('other-dealer');e.finance=e.login('finance');e.warehouse=e.login('warehouse')
        e.pid=e.act(a,'product.save',{'sku':'R6','name':'R6 mini','price':'5800.00'})['id']
        e.wid=e.act(a,'warehouse.save',{'code':'W1','name':'Primary','kind':'PRIMARY'})['id']
        e.wid2=e.act(a,'warehouse.save',{'code':'W2','name':'Secondary','kind':'SECONDARY'})['id']
        e.cwid=e.act(a,'warehouse.save',{'code':'C1','name':'Consignment D1','kind':'CONSIGNMENT','dealer_id':e.did})['id']
        e.aid=e.act(a,'account.save',{'name':'TF','aliases':'xt'})['id']
        yield e
        for c in e.clients.values():c.close()
