import json
from datetime import timedelta
import httpx
import pytest
from app import policy_assistant as pa
from app.core import today


def proposal(**kw):
    return dict(name='TEST_ONLY November 10+1',kind='TEN_PLUS_ONE',starts=today().isoformat(),
                ends=(today()+timedelta(days=30)).isoformat(),description='同车型每买10台赠1台，适用全部经销商与在售车型，不叠加。',scope='ALL',unresolved=[])|kw


def draft(e,**kw):
    return e.act(e.admin,'policy-draft.save',dict(idea='所有在售车型，同车型每采购10台赠1台，不叠加。',proposal=proposal(**kw)))


def preview(e,d,**kw):
    payload=dict(id=d['id'],version=d['version'],sample=[dict(product_id=e.pid,quantity=10)],warehouse_id=e.wid)|kw
    return payload,e.act(e.admin,'policy-draft.preview',payload)


def publish(e,d,expected=200,**kw):
    payload,r=preview(e,d)
    return e.act(e.admin,'policy-draft.publish',dict(payload,review_token=r['review_token'],confirmed=True)|kw,expected=expected)


def post_ai(e,client=None,key='ai-draft-test-001',idea='本月全部在售车型，同车型买10台送1台，不叠加'):
    c=client or e.admin
    return c.post('/api/policies/assistant/generate',json={'idea':idea},headers={'X-CSRF-Token':e.data(c)['csrf'],'Idempotency-Key':key})


def test_draft_and_preview_never_publish_or_move_stock(env):
    e=env;e.stock(10);before=e.data();d=draft(e);_,r=preview(e,d)
    assert r['publishable'] and r['lines'][0]['gift']==1 and r['lines'][0]['shortage']==1
    assert r['total']==5800000 and r['lines'][0]['needed']==11
    after=e.data();assert after['inventory']==before['inventory'] and not after['orders'] and not after['policies']
    assert after['policy_drafts'][0]['id']==d['id']
    assert '不判断毛利' in r['warnings'][-1]


@pytest.mark.parametrize('role',['sales','dealer','finance','warehouse'])
def test_admin_only_all_assistant_operations_and_drafts(env,role):
    e=env;d=draft(e);c=getattr(e,role)
    for action,payload in [('policy-draft.save',dict(idea='forbidden policy',proposal=proposal())),('policy-draft.preview',dict(id=d['id'],version=1)),('policy-draft.publish',dict(id=d['id'],confirmed=True))]:
        e.act(c,action,payload,expected=403)
    assert post_ai(e,c).status_code==403
    data=e.data(c);assert data['policy_drafts']==[] and data['policy_ai'] is None


def test_generation_requires_config_and_csrf_and_does_not_fake_ai(env,monkeypatch):
    e=env;monkeypatch.delenv('OPENAI_API_KEY',raising=False);monkeypatch.delenv('HYNAK_AI_MODEL',raising=False)
    assert not e.data()['policy_ai']['configured']
    assert post_ai(e).status_code==503
    assert not e.data()['policy_drafts']
    assert e.admin.post('/api/policies/assistant/generate',json={'idea':'全部车型买10台送1台'},headers={'Idempotency-Key':'wrong-csrf-key'}).status_code==403


def test_real_adapter_request_shape_and_no_automatic_business_data(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','test-only-key');monkeypatch.setenv('HYNAK_AI_MODEL','test-only-model')
    captured={}
    class FakeClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            captured.update(url=url,**kwargs)
            return httpx.Response(200,json={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(proposal())}]}]})
    monkeypatch.setattr(pa.httpx,'AsyncClient',FakeClient)
    import asyncio
    assert asyncio.run(pa.generate('本月所有车型，买十台送一台'))['kind']=='TEN_PLUS_ONE'
    assert captured['url']=='https://api.openai.com/v1/responses'
    body=captured['json'];assert body['store'] is False and body['text']['format']['strict'] is True
    assert set(json.loads(body['input']))=={'today','idea'}
    assert 'tools' not in body and body['model']=='test-only-model'


def test_ai_generation_persists_only_draft_and_is_idempotent(env,monkeypatch):
    e=env;monkeypatch.setenv('OPENAI_API_KEY','test-only');monkeypatch.setenv('HYNAK_AI_MODEL','test-model')
    calls=[]
    async def generated(idea):calls.append(idea);return proposal()
    monkeypatch.setattr(pa,'generate',generated)
    r=post_ai(e);assert r.status_code==200,r.text
    assert post_ai(e).json()==r.json() and len(calls)==1
    assert r.json()['source']=='OPENAI' and r.json()['status']=='DRAFT' and r.json()['model']=='test-model'
    assert not e.data()['policies'] and not e.data(e.sales)['policy_drafts']
    assert post_ai(e,idea='另一条不同需求，不能重复使用此请求').status_code==409


@pytest.mark.parametrize('kind,scope,unresolved',[
    ('UNSUPPORTED','ALL',[]),('DEMO','ALL',[]),('STANDARD','UNSUPPORTED',[]),('TEN_PLUS_ONE','ALL',['需要明确活动期限']),
])
def test_unsupported_or_unresolved_cannot_publish(env,kind,scope,unresolved):
    e=env;d=draft(e,kind=kind,scope=scope,unresolved=unresolved);_,r=preview(e,d)
    assert not r['publishable'] and r['blockers']
    publish(e,d,expected=409);assert not e.data()['policies']


def test_requires_confirmation_and_current_preview(env):
    e=env;d=draft(e)
    e.act(e.admin,'policy-draft.publish',dict(id=d['id'],version=1,confirmed=True,sample=[dict(product_id=e.pid,quantity=10)],warehouse_id=e.wid,review_token='forged'),expected=409)
    publish(e,d,expected=400,confirmed=False)
    assert not e.data()['policies']


@pytest.mark.parametrize('change',['stock','price','quantity','warehouse'])
def test_changes_invalidate_review(env,change):
    e=env;e.stock(30);d=draft(e);payload,r=preview(e,d)
    if change=='stock':e.stock(1)
    if change=='price':e.act(e.admin,'product.save',dict(id=e.pid,sku='R6',name='R6 mini',price='5900.00'))
    if change=='quantity':payload['sample'][0]['quantity']=11
    if change=='warehouse':payload['warehouse_id']=None
    e.act(e.admin,'policy-draft.publish',dict(payload,review_token=r['review_token'],confirmed=True),expected=409)
    assert not e.data()['policies']


def test_version_conflict_and_published_draft_immutable(env):
    e=env;d=draft(e)
    e.act(e.admin,'policy-draft.save',dict(id=d['id'],version=0,idea=d['idea'],proposal=proposal()),expected=409)
    payload,r=preview(e,d)
    updated=e.act(e.admin,'policy-draft.save',dict(id=d['id'],version=1,idea=d['idea'],proposal=proposal(name='Updated')))
    e.act(e.admin,'policy-draft.publish',dict(payload,review_token=r['review_token'],confirmed=True),expected=409)
    publish(e,updated)
    e.act(e.admin,'policy-draft.save',dict(id=d['id'],version=2,idea=d['idea'],proposal=proposal()),expected=409)


def test_ai_unresolved_constraints_cannot_be_silently_removed(env,monkeypatch):
    e=env;monkeypatch.setenv('OPENAI_API_KEY','test');monkeypatch.setenv('HYNAK_AI_MODEL','test')
    async def generated(idea):return proposal(kind='UNSUPPORTED',unresolved=['暂不支持九折'])
    monkeypatch.setattr(pa,'generate',generated);d=post_ai(e).json()
    e.act(e.admin,'policy-draft.save',dict(id=d['id'],version=1,idea=d['idea'],proposal=proposal()),expected=409)
    e.act(e.admin,'policy-draft.save',dict(id=d['id'],version=1,idea='已经修改了想法，需要重新解释',proposal=d['proposal']),expected=409)


def test_published_ai_flow_reuses_existing_order_gift_reservation_and_shipping(env):
    e=env;e.stock(30);d=draft(e);result=publish(e,d)
    pid=result['policy_id'];assert e.data(e.sales)['policies'][0]['id']==pid
    assert not e.data(e.dealer)['policies']
    payload=dict(dealer_id=e.did,sale_type='OTHER',sale_date=today().isoformat(),due=(today()+timedelta(days=30)).isoformat(),terms='Use reviewed policy',policy_id=pid,items=[dict(product_id=e.pid,quantity=10)])
    oid=e.act(e.sales,'order.create',payload)['id'];o=e.data()['orders'][0]
    assert o['status']=='CONFIRMED' and o['total']==5800000 and o['reserved']==11
    assert [(l['quantity'],l['gift']) for l in o['lines']]==[(10,False),(1,True)]
    e.ship(oid);assert e.data()['orders'][0]['status']=='SHIPPED'
    assert e.data()['inventory'][0]['quantity']==19


def test_multi_sku_never_pools_gifts_and_ignores_consignment_stock(env):
    e=env;e.stock(12)
    pid2=e.act(e.admin,'product.save',dict(sku='R7',name='R7',price='100.00'))['id']
    e.act(e.admin,'stock.move',dict(kind='OPENING',warehouse_id=e.wid,product_id=pid2,quantity=100,reason='test inbound'))
    e.act(e.admin,'stock.move',dict(kind='CONSIGN_SEND',warehouse_id=e.wid,target_id=e.cwid,product_id=pid2,quantity=100,reason='test consignment'))
    d=draft(e);_,r=preview(e,d,warehouse_id=None,sample=[dict(product_id=e.pid,quantity=9),dict(product_id=pid2,quantity=9)])
    assert [l['gift'] for l in r['lines']]==[0,0]
    assert r['lines'][1]['available']==0 and r['lines'][1]['shortage']==9


def test_sample_and_proposal_validation(env):
    e=env;d=draft(e)
    e.act(e.admin,'policy-draft.preview',dict(id=d['id'],version=1,sample=[dict(product_id=e.pid,quantity=1)]*2),expected=400)
    e.act(e.admin,'policy-draft.preview',dict(id=d['id'],version=1,sample=[dict(product_id=e.pid,quantity=1)],warehouse_id=e.cwid),expected=400)
    e.act(e.admin,'policy-draft.save',dict(idea='valid manual idea',proposal=proposal(extra_action='drop database')),expected=400)
    e.act(e.admin,'policy-draft.save',dict(idea='valid manual idea',proposal=proposal(starts='2026-12-10',ends='2026-12-01')),expected=400)


@pytest.mark.parametrize('response',[
    httpx.Response(200,json=[]),
    httpx.Response(401,json={'error':'private-provider-details'}),
    httpx.Response(429,json={'error':'private-provider-details'}),
    httpx.Response(200,json={'status':'incomplete','output':[]}),
    httpx.Response(200,json={'status':'completed','output':[{'type':'message','content':[{'type':'refusal','refusal':'blocked'}]}]}),
    httpx.Response(200,json={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'not-json'}]}]}),
])
def test_provider_failures_are_safe_and_never_publish(env,monkeypatch,response):
    e=env;monkeypatch.setenv('OPENAI_API_KEY','test-private-key');monkeypatch.setenv('HYNAK_AI_MODEL','test')
    class FakeClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,*args,**kwargs):return response
    monkeypatch.setattr(pa.httpx,'AsyncClient',FakeClient)
    result=post_ai(e);assert result.status_code in [422,502,503]
    assert 'private-provider-details' not in result.text and 'test-private-key' not in result.text
    assert not e.data()['policies'] and not e.data()['policy_drafts']
