"""Multi-SKU warehouse actions commit together and remain idempotent."""
import pytest


def second_product(e):
    return e.act(e.admin,'product.save',dict(sku='SECOND',name='Second model',price='100.00'))['id']


def move(e,items,kind='RECEIVE',actor=None,**kw):
    return e.act(actor or e.warehouse,'stock.move',dict(kind=kind,warehouse_id=e.wid,items=items,reason='Isolated batch test',**kw))


def quantities(e):
    return {(r['warehouse_id'],r['product_id']):r['quantity'] for r in e.data()['inventory']}


@pytest.mark.parametrize('kind',['RECEIVE','TRANSFER','CONSIGN_SEND','CONSIGN_RETURN','OPENING','ADJUST'])
def test_multi_sku_actions_are_atomic_and_idempotent(env,kind):
    e=env;p2=second_product(e)
    items=[dict(product_id=e.pid,quantity=3),dict(product_id=p2,quantity=4)]
    if kind not in ['RECEIVE','OPENING']:
        move(e,[dict(product_id=e.pid,quantity=10),dict(product_id=p2,quantity=20)])
    payload=dict(kind=kind,warehouse_id=e.wid,items=items,reason='Batch '+kind)
    if kind=='TRANSFER':payload['target_id']=e.wid2
    if kind=='CONSIGN_SEND':payload['target_id']=e.cwid
    if kind=='CONSIGN_RETURN':
        move(e,[dict(product_id=e.pid,quantity=10),dict(product_id=p2,quantity=20)],kind='CONSIGN_SEND',target_id=e.cwid)
        payload.update(warehouse_id=e.cwid,target_id=e.wid)
    if kind=='ADJUST':payload['negative']=True
    actor=e.admin if kind in ['OPENING','ADJUST'] else e.warehouse
    a=e.act(actor,'stock.move',payload,key='same-batch-operation')
    assert e.act(actor,'stock.move',payload,key='same-batch-operation')==a
    assert a['item_count']==2
    q=quantities(e)
    assert q[e.wid,e.pid]==(7 if kind in ['TRANSFER','CONSIGN_SEND','ADJUST'] else 3)
    assert q[e.wid,p2]==(16 if kind in ['TRANSFER','CONSIGN_SEND','ADJUST'] else 4)
    if kind=='TRANSFER':assert q[e.wid2,e.pid]==3 and q[e.wid2,p2]==4
    if kind=='CONSIGN_SEND':assert q[e.cwid,e.pid]==3 and q[e.cwid,p2]==4
    if kind=='CONSIGN_RETURN':assert q[e.cwid,e.pid]==7 and q[e.cwid,p2]==16
    batch=[r for r in e.data()['ledger'] if r['reason']=='Batch '+kind]
    assert len(batch)==(4 if kind in ['TRANSFER','CONSIGN_SEND','CONSIGN_RETURN'] else 2)
    assert len({r['batch'] for r in batch})==1 and batch[0]['batch']


@pytest.mark.parametrize('kind',['TRANSFER','CONSIGN_RETURN'])
def test_later_insufficient_sku_rolls_back_entire_batch(env,kind):
    e=env;p2=second_product(e)
    move(e,[dict(product_id=e.pid,quantity=10),dict(product_id=p2,quantity=1)])
    source=e.wid;target=e.wid2
    if kind=='CONSIGN_RETURN':
        move(e,[dict(product_id=e.pid,quantity=10),dict(product_id=p2,quantity=1)],kind='CONSIGN_SEND',target_id=e.cwid)
        source=e.cwid;target=e.wid
    before=quantities(e);ledger=len(e.data()['ledger'])
    d=dict(kind=kind,warehouse_id=source,target_id=target,items=[dict(product_id=e.pid,quantity=2),dict(product_id=p2,quantity=2)],reason='Must roll back')
    e.act(e.warehouse,'stock.move',d,key='retry-batch-after-error',expected=400)
    assert quantities(e)==before and len(e.data()['ledger'])==ledger
    d['items'][1]['quantity']=1
    e.act(e.warehouse,'stock.move',d,key='retry-batch-after-error')
    q=quantities(e);assert q[source,e.pid]==8 and q[source,p2]==0


@pytest.mark.parametrize('role',['sales','dealer','finance'])
def test_batch_does_not_expand_warehouse_permissions(env,role):
    e=env
    e.act(getattr(e,role),'stock.move',dict(kind='RECEIVE',warehouse_id=e.wid,items=[dict(product_id=e.pid,quantity=1)],reason='Forbidden'),expected=403)
    assert not e.data()['ledger']


@pytest.mark.parametrize('case',['empty','duplicate','invalid_quantity','missing_product','bad_format'])
def test_invalid_batch_cannot_fall_back_to_single_sku_or_partially_save(env,case):
    e=env;items=[dict(product_id=e.pid,quantity=1)]
    if case=='empty':items=[]
    if case=='duplicate':items*=2
    if case=='invalid_quantity':items.append(dict(product_id=e.pid,quantity=0))
    if case=='missing_product':items.append(dict(product_id=999999,quantity=1))
    if case=='bad_format':items.append('not a row')
    e.act(e.warehouse,'stock.move',dict(kind='RECEIVE',warehouse_id=e.wid,product_id=e.pid,quantity=999,items=items,reason='Invalid batch'),expected=404 if case=='missing_product' else 400)
    assert not e.data()['ledger']
