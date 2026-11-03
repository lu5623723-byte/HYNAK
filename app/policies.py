"""Administrator-published policies; eligibility is rechecked under the write lock."""
from sqlalchemy import select, or_
from .core import *


def available_policy(p):
    at=today().isoformat()
    return p.active and p.starts<=at and (not p.ends or at<=p.ends)


def save_policy(db,actor,d):
    roles(actor,'admin')
    p=get(db,SalesPolicy,d['id']) if d.get('id') else SalesPolicy(creator_id=actor.id)
    name=str(d.get('name',p.name or '')).strip()
    kind=d.get('kind',p.kind)
    require(1<=len(name)<=200,'请填写销售政策名称（最多200字）')
    require(kind in ['STANDARD','DEMO','TEN_PLUS_ONE'],'请选择标准价、Demo或买10送1政策规则')
    starts=date_value(d.get('starts',p.starts or today().isoformat()))
    end_value=d.get('ends',p.ends)
    ends=date_value(end_value) if end_value else None
    require(not ends or starts<=ends,'政策结束日期不能早于开始日期')
    if p.id and kind!=p.kind:
        require(not db.scalar(select(Order.id).where(Order.policy_id==p.id).limit(1)),'已有订单使用该规则，请创建新方案',409)
    p.name=name;p.kind=kind;p.starts=starts;p.ends=ends
    p.description=str(d.get('description',p.description or '')).strip()
    require(len(p.description)<=2000,'方案说明最多2000字')
    p.active=bool(d.get('active',p.active if p.id else True))
    db.add(p);db.flush()
    audit(db,actor,'SALES_POLICY_SAVE',p.id,f'{p.name}; {p.kind}; active={p.active}; {p.starts}..{p.ends or ""}; {p.description}')
    return {'id':p.id}


def resolve_policy(db,actor,d,existing=None):
    pid=d.get('policy_id',existing.policy_id if existing else None) or None
    if pid:
        require(actor.role=='admin' or (actor.role=='sales' and d.get('sale_type',existing.sale_type if existing else None)=='OTHER'),'销售只能在其他类型订单选择政策方案',403)
        p=get(db,SalesPolicy,pid)
        require(available_policy(p),'销售政策已停用、未开始或已过期，请选择最新方案',409)
        requested=d.get('promotion','STANDARD')
        require(requested in ['STANDARD',p.kind],'销售政策与促销参数不一致')
        return p.kind,p
    return d.get('promotion',existing.promotion if existing and 'policy_id' not in d else 'STANDARD'),None


def demo_product_ids(db,o):
    return set(db.scalars(select(OrderLine.product_id).where(
        OrderLine.order_id==o.id,OrderLine.gift==False,
        or_(OrderLine.demo_applied==True, o.promotion=='DEMO'))).all())


def check_demo(db,o,product_ids=None,*,include_pending=False):
    if product_ids is None:product_ids=demo_product_ids(db,o)
    if not product_ids:return
    used=set(db.scalars(select(OrderLine.product_id).join(Order,Order.id==OrderLine.order_id).where(
        Order.dealer_id==o.dealer_id,or_(OrderLine.demo_applied==True,Order.promotion=='DEMO'),
        True if include_pending else Order.confirmed.is_not(None),
        Order.status.notin_(['CANCELED','REJECTED','EXPIRED']),Order.id!=o.id,
        OrderLine.gift==False,OrderLine.product_id.in_(product_ids))).all())
    require(not used,'该经销商以下车型已享受或已在待处理订单勾选Demo计划，不能重复勾选：'+', '.join(get(db,Product,pid).sku for pid in sorted(used)),409)


def validate_activation(db,o):
    if o.policy_id:require(available_policy(get(db,SalesPolicy,o.policy_id)),'政策已失效，请修订后重新审核',409)
    check_demo(db,o)


def price_exception(db,o):
    return any(l.unit_price!=((l.standard_price*80+50)//100 if l.demo_applied or o.promotion=='DEMO' else l.standard_price)
               for l in db.scalars(select(OrderLine).where(OrderLine.order_id==o.id,OrderLine.gift==False)).all())
