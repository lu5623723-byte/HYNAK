from sqlalchemy import select
from .core import *
from .brazil import normalize_cnpj, dealer_address

def save_user(db,actor,d):
    roles(actor,'admin');uid=d.get('id');u=get(db,User,uid) if uid else User()
    role=d['role'];require(role in ['admin','sales','warehouse','finance','dealer'],'角色无效')
    if uid:
        require(u.id!=actor.id or (role=='admin' and d.get('active',True)),'不能取消自己的管理员权限')
        require(u.role==role,'首版已有账号不可切换角色；请停用后新建')
    username=str(d['username']).strip().lower();require(1<=len(username)<=120,'用户名无效')
    u.username=username;u.name=str(d['name']).strip();require(u.name,'请填写姓名');u.role=role
    did=d.get('dealer_id')
    if role=='dealer':
        require(did,'经销商账号须关联经销商');get(db,Dealer,did);u.dealer_id=int(did)
    else:u.dealer_id=None
    if d.get('password'):u.password=hash_password(d['password'])
    require(u.password,'新账号须设置密码')
    u.commission_bps=integer(d.get('commission_bps',0),0,10000);u.active=bool(d.get('active',True))
    db.add(u);db.flush()
    if d.get('password') or not u.active:
        for s in db.scalars(select(LoginSession).where(LoginSession.user_id==u.id)).all():db.delete(s)
    audit(db,actor,'USER_SAVE',u.id,f'{u.username}; {u.role}; bps={u.commission_bps}')
    return {'id':u.id}
def save_dealer(db,actor,d):
    roles(actor,'admin');obj=get(db,Dealer,d['id']) if d.get('id') else Dealer()
    require(not any(k in d for k in ['credit_rating','credit_score','rating','rating_score']),'信用评级由系统自动计算，不能人工填写或修改')
    cnpj=normalize_cnpj(d.get('cnpj',obj.cnpj))
    if cnpj:
        duplicate=db.scalar(select(Dealer.id).where(Dealer.cnpj==cnpj,Dealer.id!=obj.id))
        require(duplicate is None,'该CNPJ已关联其他经销商 / CNPJ já cadastrado',409)
    address=dealer_address(d,obj)
    obj.code=str(d['code']).strip();obj.name=str(d['name']).strip();require(obj.code and obj.name,'代码和名称不能为空')
    obj.phone=str(d.get('phone',''));obj.language=d.get('language','pt');require(obj.language in ['zh','pt','en'],'语言无效')
    sid=d.get('sales_id') or None
    if sid:require(get(db,User,sid).role=='sales','责任人必须是销售')
    obj.sales_id=int(sid) if sid else None
    obj.cnpj=cnpj
    for key,value in address.items():setattr(obj,key,value)
    db.add(obj);db.flush();audit(db,actor,'DEALER_SAVE',obj.id,obj.name)
    return {'id':obj.id}
def save_product(db,actor,d):
    roles(actor,'admin');p=get(db,Product,d['id']) if d.get('id') else Product()
    p.sku=str(d['sku']).strip();p.name=str(d['name']).strip();require(p.sku and p.name,'SKU和名称不能为空')
    p.price=money(d['price']);p.active=bool(d.get('active',True));db.add(p);db.flush();audit(db,actor,'PRODUCT_SAVE',p.id,f'{p.sku}; cents={p.price}')
    return {'id':p.id}
def save_warehouse(db,actor,d):
    roles(actor,'admin','warehouse')
    w=get(db,Warehouse,d['id']) if d.get('id') else Warehouse()
    existing=bool(w.id);kind=d.get('kind',w.kind)
    require(kind in ['PRIMARY','SECONDARY','CONSIGNMENT'],'仓库类型无效')
    if existing:require(kind==w.kind,'已有仓库不能切换类型，请新增对应类型的仓库',409)
    code=str(d.get('code',w.code or '')).strip();name=str(d.get('name',w.name or '')).strip()
    require(1<=len(code)<=100 and 1<=len(name)<=200,'请填写有效仓库代码及名称')
    require(not db.scalar(select(Warehouse.id).where(func.lower(Warehouse.code)==code.lower(),Warehouse.id!=w.id)),'仓库代码已存在，请使用不同代码',409)
    w.code=code;w.name=name;w.kind=kind
    if kind=='CONSIGNMENT':
        did=get(db,Dealer,d.get('dealer_id',w.dealer_id)).id
        if existing:require(did==w.dealer_id,'已有寄售地点不能改为其他经销商',409)
        w.dealer_id=did
    else:require(not d.get('dealer_id'),'一级、二级仓库不关联经销商')
    for field,maximum in [('location',200),('address',2000),('contact',120),('phone',30)]:
        value=str(d.get(field,getattr(w,field) or '')).strip()
        require(len(value)<=maximum,'仓库信息过长：'+field)
        if field=='location' and field in d:require(value,'请填写仓库地点 / 城市')
        setattr(w,field,value)
    db.add(w);db.flush();audit(db,actor,'WAREHOUSE_UPDATE' if existing else 'WAREHOUSE_CREATE',w.id,f'{w.code}; {w.name}; {w.kind}; {w.location}; {w.address}; {w.contact}; {w.phone}')
    return {'id':w.id}
def save_account(db,actor,d):
    roles(actor,'admin','finance');name=str(d['name']).strip();require(name,'收款账户名称不能为空')
    aliases={a.strip().casefold() for a in str(d.get('aliases','')).split(',') if a.strip()};aliases.add(name.casefold())
    if aliases & {'tf','xt'}:aliases|={'tf','xt'}
    for existing in db.scalars(select(BankAccount)).all():
        old={existing.name.casefold()}|set(existing.aliases.split(','))
        require(not aliases & old,'账户/别名已存在；TF与xt应属于同一个账户')
    require(not {'xiaolu','卢'}<=aliases,'xiaolu与卢必须分别核实，首版禁止直接合并')
    a=BankAccount(name=name,aliases=','.join(sorted(aliases)));db.add(a);db.flush();audit(db,actor,'BANK_ACCOUNT_CREATE',a.id,name)
    return {'id':a.id}
