from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, UniqueConstraint, Index
from .db import Base

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')
class User(Base):
    __tablename__='users'
    id=Column(Integer, primary_key=True)
    username=Column(String(120), unique=True, nullable=False)
    name=Column(String(120), nullable=False)
    password=Column(Text, nullable=False)
    role=Column(String(20), nullable=False)
    dealer_id=Column(Integer, ForeignKey('dealers.id'), unique=True)
    store_id=Column(String(100))
    commission_bps=Column(Integer, default=0, nullable=False)
    active=Column(Boolean, default=True, nullable=False)
class Dealer(Base):
    __tablename__='dealers'
    __table_args__=(Index('uq_dealers_cnpj', 'cnpj', unique=True),)
    id=Column(Integer, primary_key=True)
    code=Column(String(100), unique=True, nullable=False)
    name=Column(String(200), nullable=False)
    phone=Column(String(30), default='')
    language=Column(String(10), default='pt')
    sales_id=Column(Integer, ForeignKey('users.id'))
    auto_apply_credit=Column(Boolean, nullable=False, default=False, server_default='false')
    cnpj=Column(String(14))
    address_state=Column(String(2), nullable=False, default='', server_default='')
    address_city_id=Column(String(7), nullable=False, default='', server_default='')
    address_city=Column(String(160), nullable=False, default='', server_default='')
    address_street=Column(String(200), nullable=False, default='', server_default='')
    address_number=Column(String(30), nullable=False, default='', server_default='')
    address_district=Column(String(150), nullable=False, default='', server_default='')
    address_complement=Column(String(200), nullable=False, default='', server_default='')
    address_cep=Column(String(8), nullable=False, default='', server_default='')
class LoginSession(Base):
    __tablename__='login_sessions'
    token=Column(String(64), primary_key=True)
    user_id=Column(Integer, ForeignKey('users.id'), nullable=False)
    csrf=Column(String(80), nullable=False)
    expires=Column(String(40), nullable=False)
class LoginAttempt(Base):
    __tablename__='login_attempts'
    id=Column(Integer, primary_key=True)
    key=Column(String(200), nullable=False)
    at=Column(String(40), default=now, nullable=False)
class Product(Base):
    __tablename__='products'
    id=Column(Integer, primary_key=True)
    sku=Column(String(100), unique=True, nullable=False)
    name=Column(String(200), nullable=False)
    price=Column(Integer, nullable=False)
    active=Column(Boolean, default=True, nullable=False)
class Warehouse(Base):
    __tablename__='warehouses'
    id=Column(Integer, primary_key=True)
    code=Column(String(100), unique=True, nullable=False)
    name=Column(String(200), nullable=False)
    kind=Column(String(20), nullable=False)
    dealer_id=Column(Integer, ForeignKey('dealers.id'))
    location=Column(String(200), nullable=False, default='', server_default='')
    address=Column(Text, nullable=False, default='', server_default='')
    contact=Column(String(120), nullable=False, default='', server_default='')
    phone=Column(String(30), nullable=False, default='', server_default='')
class SalesPolicy(Base):
    __tablename__='sales_policies'
    id=Column(Integer, primary_key=True)
    name=Column(String(200), nullable=False)
    kind=Column(String(30), nullable=False)
    description=Column(Text, nullable=False, default='')
    starts=Column(String(10), nullable=False)
    ends=Column(String(10))
    active=Column(Boolean, nullable=False, default=True)
    creator_id=Column(Integer, ForeignKey('users.id'), nullable=False)
    created=Column(String(40), nullable=False, default=now)
class PolicyDraft(Base):
    __tablename__='sales_policy_drafts'
    id=Column(Integer, primary_key=True)
    creator_id=Column(Integer, ForeignKey('users.id'), nullable=False)
    idea=Column(Text, nullable=False)
    proposal=Column(Text, nullable=False)
    source=Column(String(20), nullable=False, default='MANUAL')
    model=Column(String(120), nullable=False, default='')
    version=Column(Integer, nullable=False, default=1)
    status=Column(String(20), nullable=False, default='DRAFT')
    review_hash=Column(String(64))
    policy_id=Column(Integer, ForeignKey('sales_policies.id'))
    created=Column(String(40), nullable=False, default=now)
    updated=Column(String(40), nullable=False, default=now)
class Order(Base):
    __tablename__='orders'
    id=Column(Integer, primary_key=True)
    dealer_id=Column(Integer, ForeignKey('dealers.id'), nullable=False)
    sales_id=Column(Integer, ForeignKey('users.id'))
    creator_id=Column(Integer, ForeignKey('users.id'), nullable=False)
    mode=Column(String(20), nullable=False)
    sale_date=Column(String(10))
    sale_type=Column(String(20), nullable=False, default='WHOLESALE', server_default='WHOLESALE')
    payment_at=Column(String(40))
    payment_method=Column(String(20), nullable=False, default='BANK', server_default='BANK')
    status=Column(String(40), nullable=False)
    admin_review_pending=Column(Boolean, nullable=False, default=False, server_default='false')
    version=Column(Integer, default=1, nullable=False)
    promotion=Column(String(20), default='STANDARD')
    policy_id=Column(Integer, ForeignKey('sales_policies.id'))
    policy_name=Column(String(200))
    terms=Column(Text, default='')
    special_7030=Column(Boolean, default=False)
    due=Column(String(10), nullable=False)
    total=Column(Integer, default=0, nullable=False)
    freight=Column(Integer, default=0, nullable=False)
    commission_bps=Column(Integer)
    partial_allowed=Column(Boolean, default=False)
    created=Column(String(40), default=now, nullable=False)
    confirmed=Column(String(40))
    source=Column(String(30), default='CURRENT')
    idempotency=Column(String(180), unique=True)
class OrderLine(Base):
    __tablename__='order_lines'
    id=Column(Integer, primary_key=True)
    order_id=Column(Integer, ForeignKey('orders.id'), nullable=False)
    product_id=Column(Integer, ForeignKey('products.id'), nullable=False)
    quantity=Column(Integer, nullable=False)
    product_sku=Column(String(100), nullable=False)
    product_name=Column(String(200), nullable=False)
    standard_price=Column(Integer, nullable=False)
    unit_price=Column(Integer, nullable=False)
    gift=Column(Boolean, default=False, nullable=False)
    demo_applied=Column(Boolean, default=False, nullable=False, server_default='false')
class ConfirmationToken(Base):
    __tablename__='order_confirmation_tokens'
    hash=Column(String(64), primary_key=True)
    order_id=Column(Integer, ForeignKey('orders.id'), nullable=False)
    version=Column(Integer, nullable=False)
    expires=Column(String(40), nullable=False)
    revoked=Column(Boolean, default=False)
    action=Column(String(20))
    acted_at=Column(String(40))
    ip=Column(String(100))
    device=Column(String(300))
class StockLedger(Base):
    __tablename__='stock_ledger'
    id=Column(Integer, primary_key=True)
    warehouse_id=Column(Integer, ForeignKey('warehouses.id'), nullable=False)
    product_id=Column(Integer, ForeignKey('products.id'), nullable=False)
    delta=Column(Integer, nullable=False)
    kind=Column(String(40), nullable=False)
    order_id=Column(Integer, ForeignKey('orders.id'))
    actor_id=Column(Integer, ForeignKey('users.id'), nullable=False)
    reason=Column(Text, nullable=False)
    batch=Column(String(100))
    created=Column(String(40), default=now, nullable=False)
class Reservation(Base):
    __tablename__='stock_reservations'
    id=Column(Integer, primary_key=True)
    order_id=Column(Integer, ForeignKey('orders.id'), nullable=False)
    line_id=Column(Integer, ForeignKey('order_lines.id'), nullable=False)
    warehouse_id=Column(Integer, ForeignKey('warehouses.id'), nullable=False)
    quantity=Column(Integer, nullable=False)
    expires=Column(String(40), nullable=False)
    active=Column(Boolean, default=True, nullable=False)
class Shipment(Base):
    __tablename__='shipments'
    id=Column(Integer, primary_key=True)
    order_id=Column(Integer, ForeignKey('orders.id'), nullable=False)
    warehouse_id=Column(Integer, ForeignKey('warehouses.id'), nullable=False)
    consignment_location_id=Column(Integer, ForeignKey('warehouses.id'))
    status=Column(String(20), default='PENDING_PICKUP')
    serials=Column(Text, default='')
    exception=Column(Text, default='')
    idempotency=Column(String(180), unique=True, nullable=False)
    created=Column(String(40), default=now)
    delivered=Column(String(40))
class ShipmentLine(Base):
    __tablename__='shipment_lines'
    id=Column(Integer, primary_key=True)
    shipment_id=Column(Integer, ForeignKey('shipments.id'), nullable=False)
    line_id=Column(Integer, ForeignKey('order_lines.id'), nullable=False)
    quantity=Column(Integer, nullable=False)
class ConsignmentReport(Base):
    __tablename__='consignment_records'
    id=Column(Integer, primary_key=True)
    warehouse_id=Column(Integer, ForeignKey('warehouses.id'), nullable=False)
    product_id=Column(Integer, ForeignKey('products.id'), nullable=False)
    quantity=Column(Integer, nullable=False)
    dealer_id=Column(Integer, ForeignKey('dealers.id'), nullable=False)
    status=Column(String(20), default='PENDING')
    order_id=Column(Integer, ForeignKey('orders.id'))
    source_order_id=Column(Integer, ForeignKey('orders.id'))
    idempotency=Column(String(180), unique=True, nullable=False)
class Receivable(Base):
    __tablename__='receivables'
    id=Column(Integer, primary_key=True)
    order_id=Column(Integer, ForeignKey('orders.id'), unique=True, nullable=False)
    amount=Column(Integer, nullable=False)
    due=Column(String(10), nullable=False)
    source=Column(String(40), default='CURRENT')
class Claim(Base):
    __tablename__='payment_claims'
    id=Column(Integer, primary_key=True)
    order_id=Column(Integer, ForeignKey('orders.id'), nullable=False)
    amount=Column(Integer, nullable=False)
    paid_date=Column(String(10), nullable=False)
    paid_at=Column(String(40))
    reference=Column(String(200), nullable=False)
    attachment=Column(String(150), nullable=False)
    media_type=Column(String(100), nullable=False)
    status=Column(String(20), default='PENDING')
    created=Column(String(40), default=now)
class BankAccount(Base):
    __tablename__='bank_accounts'
    id=Column(Integer, primary_key=True)
    name=Column(String(200), nullable=False, unique=True)
    aliases=Column(String(300), nullable=False, default='')
class Recharge(Base):
    __tablename__='dealer_recharges'
    id=Column(Integer, primary_key=True)
    dealer_id=Column(Integer, ForeignKey('dealers.id'), nullable=False)
    amount=Column(Integer, nullable=False)
    paid_date=Column(String(10), nullable=False)
    reference=Column(String(200), nullable=False, default='')
    attachment=Column(String(150), nullable=False)
    media_type=Column(String(100), nullable=False)
    status=Column(String(20), nullable=False, default='PENDING')
    note=Column(Text, nullable=False, default='')
    creator_id=Column(Integer, ForeignKey('users.id'), nullable=False)
    created=Column(String(40), nullable=False, default=now)
class Receipt(Base):
    __tablename__='bank_receipts'
    __table_args__=(UniqueConstraint('account_id','reference'),)
    id=Column(Integer, primary_key=True)
    dealer_id=Column(Integer, ForeignKey('dealers.id'), nullable=False)
    account_id=Column(Integer, ForeignKey('bank_accounts.id'), nullable=False)
    amount=Column(Integer, nullable=False)
    date=Column(String(10), nullable=False)
    reference=Column(String(200), nullable=False)
    claim_id=Column(Integer, ForeignKey('payment_claims.id'), unique=True)
    recharge_id=Column(Integer, ForeignKey('dealer_recharges.id'), unique=True)
    is_recharge=Column(Boolean, nullable=False, default=False, server_default='false')
    actor_id=Column(Integer, ForeignKey('users.id'), nullable=False)
    created=Column(String(40), default=now)
class Allocation(Base):
    __tablename__='payment_allocations'
    id=Column(Integer, primary_key=True)
    receipt_id=Column(Integer, ForeignKey('bank_receipts.id'))
    dealer_id=Column(Integer, ForeignKey('dealers.id'), nullable=False)
    order_id=Column(Integer, ForeignKey('orders.id'), nullable=False)
    amount=Column(Integer, nullable=False)
    actor_id=Column(Integer, ForeignKey('users.id'), nullable=False)
    idempotency=Column(String(180), unique=True, nullable=False)
    created=Column(String(40), default=now)
class OpeningCredit(Base):
    __tablename__='opening_credits'
    id=Column(Integer, primary_key=True)
    dealer_id=Column(Integer, ForeignKey('dealers.id'), nullable=False)
    amount=Column(Integer, nullable=False)
    source=Column(String(200), nullable=False)
class Adjustment(Base):
    __tablename__='adjustments'
    id=Column(Integer, primary_key=True)
    order_id=Column(Integer, ForeignKey('orders.id'), nullable=False)
    amount=Column(Integer, nullable=False)
    reason=Column(Text, nullable=False)
    actor_id=Column(Integer, ForeignKey('users.id'), nullable=False)
    created=Column(String(40), default=now)
class Followup(Base):
    __tablename__='collection_followups'
    id=Column(Integer, primary_key=True)
    dealer_id=Column(Integer, ForeignKey('dealers.id'), nullable=False)
    order_id=Column(Integer, ForeignKey('orders.id'))
    note=Column(Text, nullable=False)
    next_date=Column(String(10), nullable=False)
    actor_id=Column(Integer, ForeignKey('users.id'), nullable=False)
    created=Column(String(40), default=now)
class Commission(Base):
    __tablename__='commission_records'
    id=Column(Integer, primary_key=True)
    order_id=Column(Integer, ForeignKey('orders.id'), unique=True, nullable=False)
    sales_id=Column(Integer, ForeignKey('users.id'), nullable=False)
    base=Column(Integer, nullable=False)
    bps=Column(Integer, nullable=False)
    amount=Column(Integer, nullable=False)
    status=Column(String(20), default='APPROVED')
    reviewer_id=Column(Integer, ForeignKey('users.id'), nullable=False)
    approved_at=Column(String(40), default=now)
    paid_date=Column(String(10))
    paid_amount=Column(Integer)
    note=Column(Text, default='')
class ImportBatch(Base):
    __tablename__='import_batches'
    id=Column(Integer, primary_key=True)
    fingerprint=Column(String(64), unique=True, nullable=False)
    cutover=Column(String(10), nullable=False)
    source=Column(String(200), nullable=False)
    status=Column(String(20), default='PREVIEW')
    payload=Column(Text, nullable=False)
    errors=Column(Text, nullable=False)
    original=Column(String(150), nullable=False)
    created=Column(String(40), default=now)
class Audit(Base):
    __tablename__='audit_logs'
    id=Column(Integer, primary_key=True)
    actor_id=Column(Integer, ForeignKey('users.id'))
    action=Column(String(100), nullable=False)
    entity=Column(String(100), nullable=False)
    detail=Column(Text, nullable=False)
    created=Column(String(40), default=now)
class Operation(Base):
    __tablename__='operations'
    key=Column(String(200), primary_key=True)
    payload_hash=Column(String(64), nullable=False)
    result=Column(Text, nullable=False)

class CommissionAdjustment(Base):
    __tablename__='commission_adjustments'
    id=Column(Integer, primary_key=True)
    commission_id=Column(Integer, ForeignKey('commission_records.id'), nullable=False)
    amount=Column(Integer, nullable=False)
    reason=Column(Text, nullable=False)
    actor_id=Column(Integer, ForeignKey('users.id'), nullable=False)
    created=Column(String(40), default=now)
