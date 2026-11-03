"""Many warehouses per tier, editable locations and all four transfer directions."""
import pytest


def create(e,kind,code,actor=None,**kw):
    return e.act(actor or e.warehouse,'warehouse.save',dict(kind=kind,code=code,name='Warehouse '+code,location='São Paulo',address='Test address',contact='Test contact',phone='+551100000000',**kw))['id']


@pytest.mark.parametrize('role',['admin','warehouse'])
def test_multiple_primary_and_secondary_warehouses_with_editable_info(env,role):
    e=env;actor=getattr(e,role);ids=[]
    for kind in ['PRIMARY','SECONDARY']:
        for i in range(2):ids.append(create(e,kind,f'{kind}-{i}',actor=actor))
    rows=[w for w in e.data()['warehouses'] if w['id'] in ids]
    assert len(rows)==4 and [w['kind'] for w in rows].count('PRIMARY')==2
    assert all(w['location']=='São Paulo' and w['address']=='Test address' for w in rows)
    e.act(e.warehouse,'stock.move',dict(kind='RECEIVE',warehouse_id=ids[0],items=[dict(product_id=e.pid,quantity=7)],reason='Test receipt'))
    e.act(actor,'warehouse.save',dict(id=ids[0],name='Renamed warehouse',location='Campinas',address='Updated address',contact='Updated contact',phone='+551199999999'))
    updated=next(w for w in e.data()['warehouses'] if w['id']==ids[0])
    assert updated['location']=='Campinas' and updated['name']=='Renamed warehouse' and updated['code']=='PRIMARY-0'
    assert next(r for r in e.data()['inventory'] if r['warehouse_id']==ids[0])['quantity']==7


@pytest.mark.parametrize('source_kind',['PRIMARY','SECONDARY'])
@pytest.mark.parametrize('target_kind',['PRIMARY','SECONDARY'])
def test_all_four_tier_transfer_directions_preserve_stock_without_sales(env,source_kind,target_kind):
    e=env;source=create(e,source_kind,'SRC');target=create(e,target_kind,'DST')
    p2=e.act(e.admin,'product.save',dict(sku='SECOND',name='Second model',price='100.00'))['id']
    e.act(e.warehouse,'stock.move',dict(kind='RECEIVE',warehouse_id=source,items=[dict(product_id=e.pid,quantity=10),dict(product_id=p2,quantity=20)],reason='Test receipt'))
    d=dict(kind='TRANSFER',warehouse_id=source,target_id=target,items=[dict(product_id=e.pid,quantity=3),dict(product_id=p2,quantity=4)],reason='Tier transfer test')
    result=e.act(e.warehouse,'stock.move',d,key='four-direction-transfer')
    assert e.act(e.warehouse,'stock.move',d,key='four-direction-transfer')==result
    data=e.data();stock={(r['warehouse_id'],r['product_id']):r['quantity'] for r in data['inventory']}
    assert stock[source,e.pid]==7 and stock[target,e.pid]==3
    assert stock[source,p2]==16 and stock[target,p2]==4
    assert data['orders']==[] and data['metrics']['receivable']==0 and data['metrics']['verified']==0
    assert next(w for w in data['warehouses'] if w['id']==source)['kind']==source_kind
    assert next(w for w in data['warehouses'] if w['id']==target)['kind']==target_kind


@pytest.mark.parametrize('role',['sales','dealer','finance'])
def test_other_roles_cannot_create_or_edit_warehouse(env,role):
    e=env;actor=getattr(e,role)
    e.act(actor,'warehouse.save',dict(kind='PRIMARY',code='NO',name='No access',location='City'),expected=403)
    e.act(actor,'warehouse.save',dict(id=e.wid,name='Forbidden edit'),expected=403)
    assert len(e.data()['warehouses'])==3


def test_duplicate_codes_invalid_location_and_type_changes_are_rejected(env):
    e=env;wid=create(e,'PRIMARY','NEW')
    e.act(e.warehouse,'warehouse.save',dict(kind='SECONDARY',code='new',name='Duplicate',location='City'),expected=409)
    e.act(e.warehouse,'warehouse.save',dict(id=wid,location=' '),expected=400)
    e.act(e.warehouse,'warehouse.save',dict(id=wid,kind='SECONDARY'),expected=409)
    e.act(e.warehouse,'warehouse.save',dict(id=wid,dealer_id=e.did),expected=400)
    assert len(e.data()['warehouses'])==4


def test_transfers_cannot_target_self_or_use_consignment_as_normal_warehouse(env):
    e=env;e.stock(10)
    d=dict(kind='TRANSFER',warehouse_id=e.wid,items=[dict(product_id=e.pid,quantity=1)],reason='Invalid target')
    e.act(e.warehouse,'stock.move',{**d,'target_id':e.wid},expected=400)
    e.act(e.warehouse,'stock.move',{**d,'target_id':e.cwid},expected=400)
    assert len(e.data()['ledger'])==1


def test_existing_consignment_cannot_change_dealer(env):
    e=env
    e.act(e.warehouse,'warehouse.save',dict(id=e.cwid,dealer_id=e.did2),expected=409)
    assert next(w for w in e.data()['warehouses'] if w['id']==e.cwid)['dealer_id']==e.did
