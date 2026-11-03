"""Dealer catalog shares the warehouse ledger without exposing warehouse details."""
def product(e,client=None):
    return next(p for p in e.data(client or e.dealer)['products'] if p['id']==e.pid)

def test_catalog_tracks_stock_reservations_and_excludes_consignment(env):
    e=env;e.stock(10)
    e.act(e.warehouse,'stock.move',dict(kind='RECEIVE',warehouse_id=e.wid2,product_id=e.pid,quantity=5,reason='Test delivery'))
    assert product(e)['available_quantity']==15
    oid=e.legacy_order(quantity=3);e.review(oid)
    assert product(e)['available_quantity']==12
    e.act(e.warehouse,'stock.move',dict(kind='CONSIGN_SEND',warehouse_id=e.wid,target_id=e.cwid,product_id=e.pid,quantity=4,reason='Test consignment'))
    assert product(e)['available_quantity']==8
    assert product(e,e.dealer2)['available_quantity']==8
    private=e.data(e.dealer2)
    assert private['ledger']==[]
    assert all(w['dealer_id']==e.did2 for w in private['warehouses'])
    e.act(e.admin,'order.cancel',dict(order_id=oid,reason='Test cancellation'))
    assert product(e)['available_quantity']==11

def test_catalog_price_and_stock_update_after_warehouse_change(env):
    e=env
    assert product(e)['available_quantity']==0 and product(e)['in_stock'] is False
    e.act(e.admin,'product.save',dict(id=e.pid,sku='R6',name='R6 mini',price='6000.00',active=True))
    e.act(e.warehouse,'stock.move',dict(kind='RECEIVE',warehouse_id=e.wid,product_id=e.pid,quantity=7,reason='Test delivery'))
    p=product(e)
    assert p['price']==600000 and p['available_quantity']==7 and p['in_stock'] is True
    assert e.data(e.dealer)['stock_checked_at']

def test_catalog_does_not_offer_negative_stock(env):
    e=env;e.stock(5)
    e.act(e.admin,'stock.move',dict(kind='ADJUST',warehouse_id=e.wid,product_id=e.pid,quantity=8,negative=True,allow_negative=True,reason='Verified test shortage'))
    assert product(e)['available_quantity']==0 and product(e)['in_stock'] is False
    e.act(e.warehouse,'stock.move',dict(kind='RECEIVE',warehouse_id=e.wid2,product_id=e.pid,quantity=4,reason='Test delivery'))
    assert product(e)['available_quantity']==4 and product(e)['in_stock'] is True
