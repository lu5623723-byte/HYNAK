import asyncio, json, logging, os, secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from .db import Session, migrate, write_lock, DATA_DIR
from .core import *
from . import orders, inventory, finance, catalog, excel, policies, balances, policy_assistant
from .views import snapshot, purchase_summary
from .dashboard import summary as dashboard_summary
from .dealer_credit import ratings as dealer_ratings
from .backup import backup
from .uploads import read_proof, save_claim, save_recharge, read_order_payments
from .brazil import localities
from .frontend import frontend_version
from .insights import summary as insights_summary, SETTINGS as INSIGHT_SETTINGS
from .role_insights import summary as role_insights_summary, PROFILES as WORKBENCH_PROFILES

BASE_URL=os.environ.get('PUBLIC_BASE_URL','http://127.0.0.1:8765').rstrip('/')
SECURE=os.environ.get('SECURE_COOKIES','0')=='1'
TEST_MODE=os.environ.get('HYNAK_TEST_MODE','0')=='1'
ROOT=Path(__file__).parent
async def maintenance():
    while True:
        try:
            with Session() as db:
                write_lock(db);expire_reservations(db);db.commit()
            await asyncio.to_thread(backup)
        except Exception:logging.exception('Maintenance failed')
        await asyncio.sleep(300)
@asynccontextmanager
async def lifespan(app):
    if urlparse(BASE_URL).hostname not in ['localhost','127.0.0.1']:
        require(BASE_URL.startswith('https://') and SECURE,'云端必须使用HTTPS和安全Cookie',500)
    if TEST_MODE:
        require(urlparse(BASE_URL).hostname in ['localhost','127.0.0.1'],'测试数据仅允许本机环境',500)
    migrate()
    if TEST_MODE:
        from .demo_seed import seed_demo
        seed_demo()
    with Session() as db:write_lock(db);expire_reservations(db);db.commit()
    backup()
    task=asyncio.create_task(maintenance())
    yield
    task.cancel()
app=FastAPI(title='HYNAK Operations',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
templates=Jinja2Templates(directory=ROOT/'templates')
templates.env.globals['test_mode']=TEST_MODE
templates.env.globals['asset_version']=frontend_version

@app.middleware('http')
async def security_headers(request,call_next):
    if request.method not in ['GET','HEAD','OPTIONS']:
        origin=request.headers.get('origin')
        expected=BASE_URL
        if origin and origin!=expected:return JSONResponse({'detail':'来源不受信任 / Origem inválida'},403)
        if request.headers.get('sec-fetch-site')=='cross-site':return JSONResponse({'detail':'Cross-site request rejected'},403)
        length=request.headers.get('content-length')
        if length and (not length.isdigit() or int(length)>12*1024*1024):return JSONResponse({'detail':'Upload limit 12MB'},413)
    response=await call_next(request)
    response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','X-Frame-Options':'DENY','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
    if SECURE:response.headers['Strict-Transport-Security']='max-age=31536000'
    return response
@app.exception_handler(IntegrityError)
async def conflict(request,exc):return JSONResponse({'detail':'记录重复或关联不正确 / Registro duplicado ou referência inválida'},409)
@app.exception_handler(KeyError)
async def missing(request,exc):return JSONResponse({'detail':'缺少必填字段 / Campo obrigatório ausente: '+str(exc)},400)

def authenticate(db,request,csrf=False):
    raw=request.cookies.get('hynak_session','');s=db.get(LoginSession,digest(raw))
    require(s is not None and s.expires>now(),'请登录 / Entre na sua conta',401)
    u=get(db,User,s.user_id);require(u.active,'账号已停用',401)
    if csrf:require(hmac.compare_digest(s.csrf,request.headers.get('x-csrf-token','')),'安全校验失败，请刷新页面 / Atualize a página',403)
    return u,s
async def json_body(request):
    require(request.headers.get('content-type','').split(';')[0]=='application/json','Content-Type must be application/json',415)
    raw=await request.body();require(len(raw)<1024*1024,'请求过大',413)
    try:d=json.loads(raw)
    except (ValueError,TypeError):fail('JSON格式无效')
    require(isinstance(d,dict),'JSON对象无效');return d
@app.get('/',response_class=HTMLResponse)
async def home(request:Request):
    with Session() as db:
        if not db.scalar(select(func.count(User.id))):page='setup'
        else:
            try:authenticate(db,request);page='app'
            except HTTPException:page='login'
    return templates.TemplateResponse(request=request,name='index.html',context={'page':page})
@app.get('/confirm/{raw}',response_class=HTMLResponse)
async def confirmation(request:Request,raw:str):
    return templates.TemplateResponse(request=request,name='index.html',context={'page':'confirm'})
@app.get('/health')
def health():return {'status':'ok'}
@app.post('/api/setup')
async def setup(request:Request):
    d=await json_body(request)
    with Session() as db:
        write_lock(db);require(db.scalar(select(func.count(User.id)))==0,'已初始化',409)
        local=request.client and request.client.host in ['127.0.0.1','::1','testclient']
        expected=os.environ.get('SETUP_TOKEN','')
        require((urlparse(BASE_URL).hostname in ['127.0.0.1','localhost'] and local) or (expected and secrets.compare_digest(str(d.get('setup_token','')),expected)),'云端初始化需要SETUP_TOKEN',403)
        u=User(username=str(d['username']).strip().lower(),name=str(d.get('name','Administrator')).strip(),password=hash_password(d['password']),role='admin')
        require(u.username and u.name,'请填写用户名和姓名');db.add(u);db.flush();audit(db,u,'SETUP',u.id,'First administrator created');db.commit()
    return {'ok':True}
@app.post('/api/login')
async def login(request:Request):
    d=await json_body(request);username=str(d.get('username','')).strip().lower();password=str(d.get('password',''))[:200]
    with Session() as db:
        write_lock(db)
        key=digest((request.client.host if request.client else '')+username)
        cutoff=(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat(timespec='seconds')
        require(db.scalar(select(func.count(LoginAttempt.id)).where(LoginAttempt.key==key,LoginAttempt.at>cutoff))<8,'尝试过多，请10分钟后重试',429)
        u=db.scalar(select(User).where(User.username==username))
        valid=check_password(password,u.password) if u else check_password(password,'0'*32+':'+'0'*64)
        if not u or not valid or not u.active:
            db.add(LoginAttempt(key=key));db.commit();fail('用户名或密码错误 / Usuário ou senha inválidos',401)
        raw=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        db.add(LoginSession(token=digest(raw),user_id=u.id,csrf=csrf,expires=future(12)));audit(db,u,'LOGIN',u.id,'password login');db.commit()
    response=JSONResponse({'ok':True});response.set_cookie('hynak_session',raw,httponly=True,secure=SECURE,samesite='strict',max_age=43200,path='/');return response
@app.post('/api/logout')
async def logout(request:Request):
    with Session() as db:
        write_lock(db);u,s=authenticate(db,request,True);db.delete(s);db.commit()
    res=JSONResponse({'ok':True});res.delete_cookie('hynak_session');return res
@app.get('/api/data')
def data(request:Request):
    with Session() as db:
        write_lock(db);actor,s=authenticate(db,request);expire_reservations(db);result=snapshot(db,actor);result.update(csrf=s.csrf,base_url=BASE_URL,app_version=frontend_version(),server_time=now());db.commit();return result

@app.get('/api/dealers/{dealer_id}/credit-rating')
def dealer_credit_rating(request:Request,dealer_id:int):
    with Session() as db:
        u,_=authenticate(db,request);roles(u,'admin','finance','sales','dealer')
        d=dealer_access(db,u,dealer_id)
        return dict(dealer_id=d.id,dealer=d.name,**dealer_ratings(db,[d.id],details=True)[d.id])

@app.get('/api/brazil/locations')
def brazil_locations(request:Request):
    with Session() as db:authenticate(db,request)
    return localities()

@app.get('/api/purchase-summary')
def dealer_purchase_summary(request:Request,start:str='',end:str=''):
    with Session() as db:
        actor,_=authenticate(db,request)
        return purchase_summary(db,actor,start,end)

@app.get('/api/dashboard-summary')
def dashboard_data(request:Request,start:str='',end:str='',basis:str='confirmed',dealer_id:str='',sales_id:str='',sale_type:str='',status:str='',payment_status:str=''):
    with Session() as db:
        actor,_=authenticate(db,request)
        return dashboard_summary(db,actor,start,end,basis,dealer_id,sales_id,sale_type,status,payment_status)

@app.get('/api/admin/insights')
def admin_insights(request:Request,scope:str='all',start:str='',end:str='',basis:str='confirmed',dealer_id:str='',sales_id:str='',sale_type:str='',status:str='',payment_status:str=''):
    with Session() as db:
        write_lock(db);actor,_=authenticate(db,request);roles(actor,'admin')
        require(scope in ['all','selected'],'分析范围无效')
        expire_reservations(db)
        cohort=dashboard_summary(db,actor,start,end,basis,dealer_id,sales_id,sale_type,status,payment_status) if scope=='selected' else None
        options={key:request.query_params[key] for key in INSIGHT_SETTINGS if key in request.query_params}
        result=insights_summary(db,actor,options,cohort);result['scope']=scope
        result['filters']=cohort['filters'] if cohort is not None else None
        db.commit();return result

@app.get('/api/insights')
def role_insights(request:Request,scope:str='all',start:str='',end:str='',basis:str='confirmed',dealer_id:str='',sales_id:str='',sale_type:str='',status:str='',payment_status:str=''):
    with Session() as db:
        write_lock(db);actor,_=authenticate(db,request);roles(actor,*WORKBENCH_PROFILES)
        require(scope in ['all','selected'],'分析范围无效')
        expire_reservations(db)
        cohort=dashboard_summary(db,actor,start,end,basis,dealer_id,sales_id,sale_type,status,payment_status) if scope=='selected' else None
        options={key:request.query_params[key] for key in WORKBENCH_PROFILES[actor.role]['rule_keys'] if key in request.query_params}
        result=role_insights_summary(db,actor,options,cohort);result['scope']=scope
        result['filters']=cohort['filters'] if cohort is not None else None
        db.commit();return result

@app.post('/api/policies/assistant/generate')
async def policy_ai_generate(request:Request):
    d=await json_body(request);key=request.headers.get('idempotency-key','')
    require(8<=len(key)<=100,'必须提供8–100位幂等键')
    idea=d.get('idea');require(isinstance(idea,str) and 5<=len(idea.strip())<=4000,'请用5–4000字描述政策想法')
    fingerprint=digest('policy.ai.generate:'+idea)
    with Session() as db:
        write_lock(db);actor,_=authenticate(db,request,True);roles(actor,'admin');opkey=f'{actor.id}:{key}'
        old=db.get(Operation,opkey)
        if old:
            require(old.payload_hash==fingerprint,'同一幂等键不能用于不同请求',409)
            return json.loads(old.result)
        require(policy_assistant.ai_status()['configured'],'AI服务尚未配置，请在服务器设置OPENAI_API_KEY和HYNAK_AI_MODEL；可先手动配置并校验',503)
        cutoff=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat(timespec='seconds')
        require(db.scalar(select(func.count(Audit.id)).where(Audit.actor_id==actor.id,Audit.action=='POLICY_AI_REQUEST',Audit.created>=cutoff))<5,'每分钟最多生成5次，请稍后重试',429)
        audit(db,actor,'POLICY_AI_REQUEST','assistant','AI policy draft requested; no inventory or customer data sent');db.commit()
    proposal=await policy_assistant.generate(idea)
    with Session() as db:
        write_lock(db);actor,_=authenticate(db,request,True);roles(actor,'admin')
        old=db.get(Operation,opkey)
        if old:
            require(old.payload_hash==fingerprint,'同一幂等键不能用于不同请求',409)
            return json.loads(old.result)
        result=policy_assistant.save_draft(db,actor,dict(idea=idea,proposal=proposal),source='OPENAI',model=os.environ['HYNAK_AI_MODEL'])
        db.add(Operation(key=opkey,payload_hash=fingerprint,result=json.dumps(result)));db.commit()
        return result

ASSISTANT_ACTIONS={'policy-draft.save':policy_assistant.save_draft,'policy-draft.preview':policy_assistant.preview_draft,'policy-draft.publish':policy_assistant.publish_draft}
ACTIONS={'recharge.reject':balances.reject_recharge,'order.demo':orders.set_demo,'policy.save':policies.save_policy,'user.save':catalog.save_user,'dealer.save':catalog.save_dealer,'product.save':catalog.save_product,'warehouse.save':catalog.save_warehouse,'account.save':catalog.save_account,'order.create':orders.create_order,'order.cancel':orders.cancel_order,'order.submit':orders.submit_order,'order.review':orders.review_order,'order.revise':orders.revise_order,'order.token':orders.issue_token,'order.revoke':orders.revoke_token,'order.reserve':orders.reallocate,'stock.move':inventory.stock_move,'shipment.create':inventory.ship_order,'shipment.action':inventory.shipment_action,'consignment.report':inventory.report_consignment,'consignment.confirm':inventory.confirm_consignment,'receipt.create':finance.receipt_create,'receipt.allocate':finance.allocate_receipt,'receivable.adjust':finance.adjust_receivable,'receivable.due':finance.due_change,'followup.create':finance.followup_create,'commission.approve':finance.commission_approve,'commission.pay':finance.commission_pay,'commission.adjust':finance.commission_adjust,'import.commit':excel.commit_import}
ACTIONS.update(ASSISTANT_ACTIONS)
@app.post('/api/action/{action}')
async def action(request:Request,action:str):
    require(action in ACTIONS,'操作不存在',404);d=await json_body(request)
    key=request.headers.get('idempotency-key','');require(8<=len(key)<=100,'必须提供8–100位幂等键 / Idempotency-Key obrigatório')
    with Session() as db:
        write_lock(db);actor,_=authenticate(db,request,True);expire_reservations(db)
        if action=='order.create':
            orders.sale_type_access(actor,d.get('sale_type','WHOLESALE'))
            orders.purchase_options_access(actor,d)
        opkey=f'{actor.id}:{key}';fingerprint=digest(action+json.dumps(d,sort_keys=True,ensure_ascii=False))
        old=db.get(Operation,opkey)
        if old:
            require(old.payload_hash==fingerprint,'同一幂等键不能用于不同请求',409)
            require(action!='order.token','链接已生成；请重新生成，旧链接将撤销',409)
            return json.loads(old.result)
        d['_operation_key']=opkey
        result=ACTIONS[action](db,actor,d)
        db.add(Operation(key=opkey,payload_hash=fingerprint,result=json.dumps({'issued':True} if action=='order.token' else result)))
        db.commit()
        if action=='order.token':result['url']=BASE_URL+result.pop('path')
        return result

async def order_form_write(request, order_id=None):
    """Create/revise an order and its proof in one idempotent transaction."""
    key=request.headers.get('idempotency-key','')
    require(8<=len(key)<=100, '必须提供8–100位幂等键')
    written_paths=[]
    try:
        with Session() as db:
            write_lock(db)
            actor,_=authenticate(db,request,True)
            if order_id is None:roles(actor,'admin','sales','dealer')
            else:
                roles(actor,'admin')
                order_access(db,actor,order_id)
            form=await request.form(max_files=21,max_fields=200)
            try:payload=json.loads(str(form.get('payload','')))
            except (ValueError,TypeError):fail('订单数据格式无效 / Dados do pedido inválidos')
            require(isinstance(payload,dict),'订单数据格式无效')
            if order_id is None:
                orders.sale_type_access(actor,payload.get('sale_type','WHOLESALE'))
                orders.purchase_options_access(actor,payload)
                require(payload.get('sale_type') in ['CONSIGNMENT','CASH','OTHER'],'请选择现付、寄售或其他')
                require(payload.get('sale_date'),'请填写销售日期')
            upload=form.get('file')
            proof=await read_proof(upload) if upload is not None and getattr(upload,'filename','') else None
            kind=payload.get('sale_type')
            if kind=='CONSIGNMENT':require(proof is None,'寄售订单请填写付款到期日，无需上传现付水单')
            payments=await read_order_payments(payload,form)
            action_name='order.revise.form' if order_id is not None else 'order.create.form'
            file_hash=(hashlib.sha256(proof['raw']).hexdigest() if proof else '')+''.join(p['file_hash'] for p in payments)
            fingerprint=digest(action_name+str(order_id)+json.dumps(payload,sort_keys=True,ensure_ascii=False)+file_hash)
            opkey=f'{actor.id}:{key}'
            old=db.get(Operation,opkey)
            if old:
                require(old.payload_hash==fingerprint,'同一幂等键不能用于不同请求',409)
                return json.loads(old.result)
            order_payload={k:v for k,v in payload.items() if k!='payments'}
            if order_id is None:
                result=orders.create_order(db,actor,order_payload,cash_proof=proof is not None)
            else:
                order_payload['order_id']=order_id
                result=orders.revise_order(db,actor,order_payload,cash_proof=proof is not None)
            order=get(db,Order,result['id'])
            pending=db.scalars(select(Claim).where(Claim.order_id==order.id,Claim.status=='PENDING')).all()
            if order.sale_type!='CONSIGNMENT' and (proof is not None or order.sale_type!='CASH' or order.payment_method=='BALANCE'):
                for claim in pending:
                    claim.status='SUPERSEDED'
                    audit(db,actor,'PAYMENT_CLAIM_SUPERSEDED',claim.id,f'order revision v{order.version}')
            if proof is not None:
                require(order.sale_type=='CASH','仅现付订单在创建时上传付款水单')
                require(order.payment_method=='BANK','余额付款使用已核实余额，无需重复上传银行水单')
                claim,path=save_claim(db,actor,order,proof,order.total,order.payment_at[:10],order.payment_at,'')
                written_paths.append(path)
                result['claim_id']=claim.id
            if payments:
                result['claim_ids']=[]
                for payment in payments:
                    claim,path=save_claim(db,actor,order,payment['proof'],payment['amount'],payment['paid_date'],reference=payment['reference'])
                    written_paths.append(path);result['claim_ids'].append(claim.id)
            db.add(Operation(key=opkey,payload_hash=fingerprint,result=json.dumps(result)))
            db.commit()
            return result
    except Exception:
        for path in written_paths:path.unlink(missing_ok=True)
        raise

@app.post('/api/orders')
async def order_create_form(request:Request):
    return await order_form_write(request)

@app.post('/api/orders/{order_id}/revise')
async def order_revise_form(request:Request,order_id:int):
    return await order_form_write(request,order_id)
@app.get('/api/quote/{raw}')
def quote(raw:str):
    with Session() as db:
        t,o=orders.token_order(db,raw)
        lines=[]
        for l in db.scalars(select(OrderLine).where(OrderLine.order_id==o.id)).all():
            p=get(db,Product,l.product_id);lines.append({'sku':l.product_sku,'name':l.product_name,'quantity':l.quantity,'standard_price':l.standard_price,'unit_price':l.unit_price,'gift':l.gift})
        return {'number':f'H{o.id:06d}','version':o.version,'status':o.status,'sale_date':o.sale_date,'sale_type':o.sale_type,'payment_at':o.payment_at,'total':o.total,'freight':o.freight,'due':o.due,'terms':o.terms,'promotion':o.promotion,'expires':t.expires,'action':t.action,'lines':lines}
@app.post('/api/quote/{raw}')
async def accept(request:Request,raw:str):
    d=await json_body(request)
    with Session() as db:
        write_lock(db);result=orders.confirm_order(db,raw,d.get('action'),request.client.host if request.client else '',request.headers.get('user-agent',''));db.commit();return result
@app.post('/api/claims')
async def upload_claim(request:Request):
    with Session() as db:
        write_lock(db);u,_=authenticate(db,request,True);roles(u,'admin','dealer','sales')
        form=await request.form();o=order_access(db,u,form.get('order_id'))
        pending_consignment=o.sale_type=='CONSIGNMENT' and o.status in ['DRAFT','WAITING_INTERNAL_REVIEW','WAITING_DEALER_CONFIRMATION']
        require(db.scalar(select(Receivable).where(Receivable.order_id==o.id)) or orders.cash_pending(o) or pending_consignment,'订单状态不允许上传凭证')
        if o.sale_type=='CONSIGNMENT':
            require(o.status not in ['CANCELED','REJECTED','EXPIRED'],'寄售订单已撤销、拒绝或过期，不能追加付款水单',409)
            require(date_value(form.get('paid_date'))<=today().isoformat(),'请填写已实际付款的日期')
        require(o.payment_method!='BALANCE' or o.confirmed,'余额付款订单请通过经销商充值补足余额',409)
        proof=await read_proof(form.get('file'))
        amount=money(form.get('amount'));require(amount>0,'申报金额须大于零')
        path=None
        try:
            c,path=save_claim(db,u,o,proof,amount,form.get('paid_date'),paid_at=o.payment_at if orders.cash_pending(o) else None,reference=form.get('reference',''));db.commit()
        except Exception:
            if path is not None:path.unlink(missing_ok=True)
            raise
        return {'id':c.id,'status':'PENDING'}
@app.post('/api/recharges')
async def upload_recharge(request:Request):
    key=request.headers.get('idempotency-key','');require(8<=len(key)<=100,'必须提供8–100位幂等键')
    path=None
    try:
        with Session() as db:
            write_lock(db);actor,_=authenticate(db,request,True);roles(actor,'admin','sales','dealer')
            form=await request.form()
            did=actor.dealer_id if actor.role=='dealer' else integer(form.get('dealer_id'))
            if actor.role=='dealer' and form.get('dealer_id'):require(integer(form.get('dealer_id'))==did,'不能为其他经销商提交充值',403)
            dealer_access(db,actor,did)
            proof=await read_proof(form.get('file'))
            amount=money(form.get('amount'));paid=date_value(form.get('paid_date'));reference=str(form.get('reference','')).strip()[:200]
            fingerprint=digest(json.dumps([did,amount,paid,reference,hashlib.sha256(proof['raw']).hexdigest()]))
            opkey=f'{actor.id}:{key}';old=db.get(Operation,opkey)
            if old:
                require(old.payload_hash==fingerprint,'同一幂等键不能用于不同请求',409)
                return json.loads(old.result)
            recharge,path=save_recharge(db,actor,did,proof,amount,paid,reference)
            result={'id':recharge.id,'status':'PENDING'}
            db.add(Operation(key=opkey,payload_hash=fingerprint,result=json.dumps(result)));db.commit();return result
    except Exception:
        if path is not None:path.unlink(missing_ok=True)
        raise
@app.get('/api/recharges/{rid}/attachment')
def recharge_attachment(request:Request,rid:int):
    with Session() as db:
        actor,_=authenticate(db,request);roles(actor,'admin','finance','sales','dealer')
        r=get(db,Recharge,rid);dealer_access(db,actor,r.dealer_id)
        return FileResponse(DATA_DIR/'attachments'/r.attachment,media_type=r.media_type,filename='recharge-proof'+Path(r.attachment).suffix,content_disposition_type='attachment')
@app.get('/api/claims/{cid}/attachment')
def attachment(request:Request,cid:int):
    with Session() as db:
        u,_=authenticate(db,request);roles(u,'admin','finance','sales','dealer');c=get(db,Claim,cid);order_access(db,u,c.order_id)
        return FileResponse(DATA_DIR/'attachments'/c.attachment,media_type=c.media_type,filename='payment-proof'+Path(c.attachment).suffix,content_disposition_type='attachment')
@app.get('/api/whatsapp/{oid}')
def whatsapp(request:Request,oid:int):
    with Session() as db:
        u,_=authenticate(db,request);return {'url':finance.whatsapp_link(db,u,oid),'status':'draft_only'}
@app.get('/api/import-template')
def import_template(request:Request):
    with Session() as db:u,_=authenticate(db,request);roles(u,'admin')
    return download(excel.template(),'hynak-opening-template.xlsx')
@app.post('/api/import-preview')
async def import_preview(request:Request):
    with Session() as db:
        write_lock(db);u,_=authenticate(db,request,True);roles(u,'admin');f=await request.form();upload=f.get('file');require(upload and hasattr(upload,'read'),'请上传XLSX');raw=await upload.read(10*1024*1024+1)
        result=excel.preview_import(db,u,raw,f.get('cutover'),str(f.get('source','Opening snapshot')));db.commit();return result
@app.get('/api/imports/{bid}/source')
def import_source(request:Request,bid:int):
    with Session() as db:
        u,_=authenticate(db,request);roles(u,'admin');b=get(db,ImportBatch,bid)
        return FileResponse(DATA_DIR/'attachments'/b.original,filename=f'import-{b.id}-source.xlsx')
def download(raw,name):return Response(raw,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="{name}"'})
@app.get('/api/export/{module}')
def export(request:Request,module:str):
    with Session() as db:
        u,_=authenticate(db,request);raw=excel.export_xlsx(db,u,module,dict(request.query_params));return download(raw,'hynak-'+module+'.xlsx')
@app.post('/api/backup')
async def manual_backup(request:Request):
    await json_body(request)
    with Session() as db:u,_=authenticate(db,request,True);roles(u,'admin')
    return await asyncio.to_thread(backup,True)
