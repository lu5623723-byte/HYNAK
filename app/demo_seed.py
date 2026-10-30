"""Explicit local test environment only. Never seeds a normal installation."""
from datetime import timedelta
import base64
from sqlalchemy import select, func
from .db import Session, write_lock
from .core import hash_password, audit, today
from .models import User
from . import catalog, orders, inventory
from .models import Order
from .uploads import save_claim

TEST_PASSWORD = 'Web-test-2026'

def seed_demo():
    with Session() as db:
        write_lock(db)
        if db.scalar(select(func.count(User.id))):
            return
        admin = User(username='admin', name='测试管理员', role='admin', password=hash_password(TEST_PASSWORD))
        db.add(admin)
        db.flush()
        audit(db, admin, 'TEST_ENVIRONMENT_INIT', admin.id, 'Isolated fictional test data; no real bank receipts.')
        sid = catalog.save_user(db, admin, dict(username='sales', name='测试销售 Ana', role='sales', password=TEST_PASSWORD, commission_bps=300))['id']
        for role, name in [('finance', '测试财务'), ('warehouse', '测试仓管')]:
            catalog.save_user(db, admin, dict(username=role, name=name, role=role, password=TEST_PASSWORD))
        did = catalog.save_dealer(db, admin, dict(code='TEST-SP', name='测试经销商 · São Paulo', sales_id=sid, language='pt', phone=''))['id']
        catalog.save_dealer(db, admin, dict(code='TEST-RJ', name='测试经销商 · Rio', sales_id=sid, language='en', phone=''))
        uid = catalog.save_user(db, admin, dict(username='dealer', name='测试经销商用户', role='dealer', password=TEST_PASSWORD, dealer_id=did))['id']
        products = []
        for sku, name, price in [('R6-MINI', 'R6 mini', '5800.00'), ('R6-PRO', 'R6 Pro', '6800.00'), ('MT2', 'MT2', '7200.00')]:
            products.append(catalog.save_product(db, admin, dict(sku=sku, name=name, price=price))['id'])
        primary = catalog.save_warehouse(db, admin, dict(code='TEST-W1', name='测试一级仓', kind='PRIMARY'))['id']
        catalog.save_warehouse(db, admin, dict(code='TEST-W2', name='测试二级仓', kind='SECONDARY'))
        consign = catalog.save_warehouse(db, admin, dict(code='TEST-C1', name='测试经销商寄售点', kind='CONSIGNMENT', dealer_id=did))['id']
        for pid in products:
            inventory.stock_move(db, admin, dict(kind='OPENING', warehouse_id=primary, product_id=pid, quantity=50, reason='明确标记的独立测试期初，非真实业务库存'))
        dealer, sales = db.get(User, uid), db.get(User, sid)
        def create(actor, quantity, promotion='STANDARD', due=None):
            paid_date=due or today().isoformat()
            oid=orders.create_order(db, actor, dict(dealer_id=did,sale_type='CASH',sale_date=paid_date,payment_at=paid_date+'T12:00',promotion=promotion,items=[dict(product_id=products[0],quantity=quantity)],terms='测试订单与占位水单；不构成真实购买或付款。'),cash_proof=True)['id']
            order=db.get(Order,oid)
            proof={'raw':base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1EAAAAASUVORK5CYII='),'extension':'.png','mime':'image/png'}
            save_claim(db,actor,order,proof,order.total,paid_date,order.payment_at,'TEST_ONLY_NOT_A_BANK_RECEIPT')
            return oid
        create(dealer, 2)  # Ready for the administrator's review.
        confirmed = create(dealer, 1, 'DEMO', (today()-timedelta(days=5)).isoformat())
        orders.review_order(db, admin, dict(order_id=confirmed))
        assisted = create(sales, 10, 'TEN_PLUS_ONE')
        orders.submit_order(db, sales, dict(order_id=assisted))
        orders.review_order(db, admin, dict(order_id=assisted))
        create(sales, 3)  # Draft ready to submit.
        inventory.stock_move(db, admin, dict(kind='CONSIGN_SEND', warehouse_id=primary, target_id=consign, product_id=products[1], quantity=10, reason='测试寄售发出，不产生应收'))
        inventory.report_consignment(db, dealer, dict(warehouse_id=consign, product_id=products[1], quantity=3, _operation_key='demo-consignment-report'))
        catalog.save_account(db, admin, dict(name='测试收款账户（非真实银行）', aliases='TF,xt'))
        db.commit()
