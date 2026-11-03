"""Dashboard cohorts use order dates; bank receipts use their actual bank date.

Balances are current balances of the selected cohort, not historical snapshots.
All input rows come from the same permission-scoped snapshot as the application.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .core import date_value, require, integer, today
from .models import Receipt
from .views import snapshot

ACTIVE = {'CONFIRMED', 'PARTIALLY_SHIPPED', 'SHIPPED', 'CLOSED'}
PENDING = {'DRAFT', 'WAITING_INTERNAL_REVIEW', 'WAITING_DEALER_CONFIRMATION', 'WAITING_FINANCE_REVIEW'}
STATES = ACTIVE | PENDING | {'CANCELED', 'REJECTED', 'EXPIRED'}
PAYMENTS = {'UNPAID', 'PARTIAL', 'PAID', 'OVERDUE', 'VERIFYING'}


def local_day(value):
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(ZoneInfo('America/Sao_Paulo')).date().isoformat()


def sales_rankings(orders, products, dealers, basis='confirmed', start='', end=''):
    """Rank the same effective sales cohort as the dashboard order metrics.

    A purchased unit counts once; gift lines and order freight do not count.
    Sets prevent a multi-SKU order from counting as several dealer orders.
    """
    catalog = {p['id']: p for p in products}
    customers = {d['id']: d for d in dealers}
    models, buyers = {}, {}
    dated_days, undated_orders = [], 0
    for o in orders:
        lines = [line for line in o['lines'] if not line['gift']]
        if not lines:
            continue
        at = local_day(o.get(basis)) if basis in {'created', 'confirmed'} else o.get(basis)
        if at:
            dated_days.append(at)
        else:
            undated_orders += 1
        dealer = customers[o['dealer_id']]
        buyer = buyers.setdefault(o['dealer_id'], dict(dealer_id=o['dealer_id'], name=dealer['name'], code=dealer['code'],
                                                        quantity=0, amount=0, orders=set(), days={}))
        buyer['orders'].add(o['id'])
        for line in lines:
            product = catalog.get(line['product_id'], {})
            model = models.setdefault(line['product_id'], dict(product_id=line['product_id'],
                sku=product.get('sku', line['sku']), name=product.get('name', line['name']),
                quantity=0, amount=0, orders=set(), dealers=set(), days={}))
            quantity, amount = line['quantity'], line['quantity'] * line['unit_price']
            model['quantity'] += quantity
            model['amount'] += amount
            model['orders'].add(o['id'])
            model['dealers'].add(o['dealer_id'])
            buyer['quantity'] += quantity
            buyer['amount'] += amount
            if at:
                for group in [model, buyer]:
                    point = group['days'].setdefault(at, dict(quantity=0, amount=0, orders=set()))
                    point['quantity'] += quantity
                    point['amount'] += amount
                    point['orders'].add(o['id'])
    model_rows = [dict(product_id=r['product_id'], sku=r['sku'], name=r['name'], quantity=r['quantity'],
                       amount=r['amount'], order_count=len(r['orders']), dealer_count=len(r['dealers'])) for r in models.values()]
    dealer_rows = [dict(dealer_id=r['dealer_id'], name=r['name'], code=r['code'], quantity=r['quantity'],
                        amount=r['amount'], order_count=len(r['orders'])) for r in buyers.values()]
    model_rows.sort(key=lambda r: (-r['quantity'], -r['amount'], r['sku'], r['product_id']))
    dealer_rows.sort(key=lambda r: (-r['amount'], -r['quantity'], r['code'], r['dealer_id']))
    def points(group):
        return [dict(date=day, quantity=p['quantity'], amount=p['amount'], order_count=len(p['orders']))
                for day, p in sorted(group['days'].items())]
    trends = dict(basis=basis, start=start or min(dated_days, default=''), end=end or max(dated_days, default=''),
                  undated_order_count=undated_orders,
                  models=[dict(id=key, points=points(group)) for key, group in models.items()],
                  dealers=[dict(id=key, points=points(group)) for key, group in buyers.items()])
    return dict(rankings=dict(models=model_rows, dealers=dealer_rows), ranking_trends=trends)


def summary(db, actor, start='', end='', basis='confirmed', dealer_id='', sales_id='', sale_type='', status='', payment_status=''):
    require(bool(start) == bool(end), '请同时填写开始和结束日期 / Informe as duas datas')
    if start:
        start, end = date_value(start), date_value(end)
        require(start <= end, '开始日期不能晚于结束日期 / Intervalo de datas inválido')
    require(basis in {'confirmed', 'created', 'sale_date', 'due'}, '日期口径无效 / Critério de data inválido')
    require(not sale_type or sale_type in {'CASH', 'CONSIGNMENT', 'OTHER', 'WHOLESALE'}, '销售类型无效')
    require(not status or status in STATES, '订单状态无效')
    require(not payment_status or payment_status in PAYMENTS, '付款状态无效')
    data = snapshot(db, actor)
    dealers = data['dealers']
    if dealer_id:
        dealer_id = integer(dealer_id)
        require(any(d['id'] == dealer_id for d in dealers), '无权限 / Sem permissão', 403)
        dealers = [d for d in dealers if d['id'] == dealer_id]
    if sales_id:
        sales_id = integer(sales_id)
        require(any(u['id'] == sales_id and u['role'] == 'sales' for u in data['users']), '无权限 / Sem permissão', 403)
        dealers = [d for d in dealers if d['sales_id'] == sales_id]
    dids = {d['id'] for d in dealers}

    def in_period(value):
        return not start or bool(value and start <= value <= end)

    def dimensions(o):
        return (o['dealer_id'] in dids and (not sales_id or o['sales_id'] == sales_id)
                and (not sale_type or o['sale_type'] == sale_type)
                and (not status or o['status'] == status)
                and (not payment_status or o['payment_status'] == payment_status))

    dimension_orders = [o for o in data['orders'] if dimensions(o)]
    dimension_ids = {o['id'] for o in dimension_orders}
    orders = [o for o in dimension_orders if in_period(local_day(o.get(basis)) if basis in {'confirmed', 'created'} else o.get(basis))]
    oids = {o['id'] for o in orders}
    # Keep the existing sales/purchase statistic: opening balances and consignment
    # dispatches are not sales. Sold consignment settlement orders remain sales.
    effective = [o for o in orders if o['source'] == 'CURRENT' and o['confirmed'] and o['status'] in ACTIVE
                 and (o['sale_type'] != 'CONSIGNMENT' or o['mode'] == 'CONSIGNMENT')]
    order_specific = bool(sale_type or status or payment_status)
    claim_orders = {c['id']: c['order_id'] for c in data['claims']}
    receipt_allocations = {}
    # The public snapshot hides claim IDs from sales/dealers. Use the same
    # scoped receipt set internally so every role gets the same attribution.
    receipt_claims = dict(db.execute(select(Receipt.id, Receipt.claim_id).where(Receipt.id.in_([r['id'] for r in data['receipts']]))).all())
    for a in data['allocations']:
        if a['receipt_id'] is not None:
            receipt_allocations.setdefault(a['receipt_id'], []).append(a)
    receipts = []
    for r in data['receipts']:
        if r['dealer_id'] not in dids or not in_period(r['date']):
            continue
        amount = r['amount']
        if order_specific:
            allocations = receipt_allocations.get(r['id'], [])
            amount = sum(a['amount'] for a in allocations if a['order_id'] in dimension_ids)
            if claim_orders.get(receipt_claims.get(r['id'])) in dimension_ids:
                amount += r['unallocated']
        if amount:
            receipts.append(dict(r, selected_amount=amount, dealer=next(d['name'] for d in dealers if d['id'] == r['dealer_id'])))
    metrics = dict(outstanding=sum(o['outstanding'] for o in orders),
                   overdue=sum(o['outstanding'] for o in orders if o['overdue_days'] > 0),
                   receivable=sum(o['receivable'] for o in orders),
                   allocated=sum(o['received'] for o in orders),
                   verified=sum(r['selected_amount'] for r in receipts),
                   order_count=len(effective), order_amount=sum(o['total'] for o in effective),
                   matched_order_count=len(orders), waiting=sum(o['status'] in PENDING for o in orders),
                   claims_pending=sum(c['amount'] for c in data['claims'] if c['order_id'] in oids and c['status'] == 'PENDING'))
    dealer_rows = []
    for d in dealers:
        cohort = [o for o in orders if o['dealer_id'] == d['id']]
        dealer_rows.append(dict(id=d['id'], name=d['name'], sales=d['sales'],
                                outstanding=sum(o['outstanding'] for o in cohort)))
    sales_rows = []
    for u in data['users']:
        if u['role'] != 'sales' or (sales_id and u['id'] != sales_id):
            continue
        cohort = [o for o in orders if o['sales_id'] == u['id']]
        if cohort:
            sales_rows.append(dict(sales_id=u['id'], name=u['name'],
                                   **{key: sum(o[key] for o in cohort) for key in ['receivable', 'received', 'outstanding']}))
    return dict(filters=dict(start=start, end=end, basis=basis, dealer_id=dealer_id, sales_id=sales_id,
                             sale_type=sale_type, status=status, payment_status=payment_status),
                as_of=today().isoformat(), checked_at=data['stock_checked_at'], metrics=metrics,
                orders=orders, receipts=receipts, dealers=dealer_rows, sales_summary=sales_rows,
                **sales_rankings(effective, data['products'], dealers, basis, start, end),
                receipt_scope='order_attributed' if order_specific else 'dealer_receipts')
