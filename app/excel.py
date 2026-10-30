from pathlib import Path
import io, json, zipfile
from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from .core import *
from .views import snapshot
from .db import DATA_DIR

SHEETS={'products':['sku','name','price','verified'],'dealers':['code','name','phone','language','sales_username','verified'],'stock':['warehouse_code','warehouse_name','warehouse_kind','sku','quantity','verified'],'consignment':['dealer_code','location_code','location_name','sku','quantity','verified'],'receivables':['reference','dealer_code','amount','due','verified'],'credit':['reference','dealer_code','amount','verified']}
def workbook_bytes(wb):
    for ws in wb:
        ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
        for cell in ws[1]:
            from openpyxl.styles import Font, PatternFill
            cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='143F35')
        for col in ws.columns:ws.column_dimensions[col[0].column_letter].width=min(52,max(16,max(len(str(c.value or '')) for c in col)+2))
    out=io.BytesIO();wb.save(out);return out.getvalue()
def template():
    static=Path(__file__).resolve().parent.parent/'templates'/'opening-balances.xlsx'
    if static.exists():return static.read_bytes()
    wb=Workbook();wb.remove(wb.active)
    for sheet,headers in SHEETS.items():wb.create_sheet(sheet).append(headers)
    return workbook_bytes(wb)
def preview_import(db,actor,raw,cutover,source):
    roles(actor,'admin');cutover=date_value(cutover);require(len(raw)<=10*1024*1024,'文件最多10MB')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            require(sum(f.file_size for f in z.infolist())<50*1024*1024,'解压后文件过大')
        wb=load_workbook(io.BytesIO(raw),data_only=False,read_only=True)
    except (zipfile.BadZipFile,ValueError,KeyError):fail('请上传有效XLSX模板')
    payload={};errors=[];pending=0
    for sheet,headers in SHEETS.items():
        if sheet not in wb.sheetnames:errors.append(f'{sheet}: 缺少工作表');payload[sheet]=[];continue
        ws=wb[sheet];iterator=ws.iter_rows(values_only=True)
        actual=next(iterator,())
        if list(actual[:len(headers)])!=headers:errors.append(f'{sheet}: 表头必须为 {headers}');payload[sheet]=[];continue
        rows=[]
        for i,values in enumerate(iterator,2):
            if i>10002:errors.append(f'{sheet}: 最多10000行');break
            if not any(v is not None for v in values):continue
            row={k:cell_text(values[j]) if j<len(values) and values[j] is not None else '' for j,k in enumerate(headers)}
            if any(v.startswith('=') for v in row.values()):errors.append(f'{sheet}!{i}: 不接受公式，请粘贴核实数值')
            row['_row']=i;rows.append(row)
            if row['verified'] not in ['1','0','yes','no','是','否','待核实','已核实']:errors.append(f'{sheet}!{i}: verified 必须为1/0')
            row['_verified']=row['verified'] in ['1','yes','是','已核实']
            if not row['_verified']:pending+=1
        payload[sheet]=rows
    wb.close()
    fingerprint=digest(json.dumps({'cutover':cutover,'payload':payload},sort_keys=True,ensure_ascii=False))
    old=db.scalar(select(ImportBatch).where(ImportBatch.fingerprint==fingerprint))
    if old:return {'id':old.id,'status':old.status,'errors':json.loads(old.errors),'pending':pending,'repeated':True,'rows':json.loads(old.payload)}
    # Validate references against existing data and verified rows of this batch.
    pmap={p.sku for p in db.scalars(select(Product)).all()};dmap={d.code for d in db.scalars(select(Dealer)).all()}
    seen={k:set() for k in SHEETS}
    for sheet,rows in payload.items():
        for row in rows:
            if not row['_verified']:continue
            try:
                if sheet=='products':
                    require(row['sku'] and row['name'],'SKU/名称缺失');money(row['price']);key=row['sku'];pmap.add(key)
                    require(not db.scalar(select(Product).where(Product.sku==key)),'SKU已存在；期初模板不覆盖已有标准价')
                elif sheet=='dealers':
                    require(row['code'] and row['name'],'代码/名称缺失');require(row['language'] in ['zh','pt','en'],'语言无效')
                    u=db.scalar(select(User).where(User.username==row['sales_username'],User.role=='sales'));require(u,'销售用户名不存在')
                    key=row['code'];dmap.add(key);require(not db.scalar(select(Dealer).where(Dealer.code==key)),'经销商已存在，避免覆盖销售归属')
                elif sheet in ['stock','consignment']:
                    require(row['sku'] in pmap,'SKU未核实/不存在');integer(row['quantity'],0)
                    if sheet=='stock':
                        require(row['warehouse_kind'] in ['PRIMARY','SECONDARY'],'仓库类型无效');require(row['warehouse_code'] and row['warehouse_name'],'仓库缺失');key=(row['warehouse_code'],row['sku'])
                        w=db.scalar(select(Warehouse).where(Warehouse.code==row['warehouse_code']))
                        if w:require(w.kind==row['warehouse_kind'],'已有仓库类型不同')
                    else:
                        require(row['dealer_code'] in dmap and row['location_code'] and row['location_name'],'经销商/寄售位置未核实');key=(row['location_code'],row['sku'])
                        w=db.scalar(select(Warehouse).where(Warehouse.code==row['location_code']))
                        if w:
                            dealer=db.get(Dealer,w.dealer_id)
                            require(w.kind=='CONSIGNMENT' and dealer and dealer.code==row['dealer_code'],'已有寄售位置所属不同')
                else:
                    require(row['dealer_code'] in dmap,'经销商未核实/不存在');require(row['reference'],'来源单号缺失');require(money(row['amount'])>0,'金额须大于0');key=row['reference']
                    if sheet=='receivables':date_value(row['due']);require(not db.scalar(select(Order).where(Order.idempotency=='opening:'+key)),'期初单号已导入')
                    else:require(not db.scalar(select(OpeningCredit).where(OpeningCredit.source=='opening:'+key)),'预存款单号已导入')
                require(key not in seen[sheet],'同表存在重复记录');seen[sheet].add(key)
            except HTTPException as e:errors.append(f'{sheet}!{row["_row"]}: {e.detail}')
    filename=secrets_file()+'.xlsx';(DATA_DIR/'attachments'/filename).write_bytes(raw)
    batch=ImportBatch(fingerprint=fingerprint,cutover=cutover,source=source[:200],payload=json.dumps(payload,ensure_ascii=False),errors=json.dumps(errors,ensure_ascii=False),original=filename)
    db.add(batch);db.flush();audit(db,actor,'IMPORT_PREVIEW',batch.id,f'{source}; errors={len(errors)}; pending={pending}')
    return {'id':batch.id,'status':batch.status,'errors':errors,'pending':pending,'rows':payload}
def secrets_file():
    import secrets
    return secrets.token_hex(24)
def commit_import(db,actor,d):
    roles(actor,'admin');b=get(db,ImportBatch,d['batch_id'])
    if b.status=='COMMITTED':return {'id':b.id,'repeated':True}
    require(not json.loads(b.errors),'导入存在错误，请修正后重新预览')
    payload=json.loads(b.payload)
    # Replay validation with a savepoint to protect against reference changes since preview.
    source=f'import:{b.id}; cutover:{b.cutover}; {b.source}'
    for row in payload['products']:
        if row['_verified']:
            require(not db.scalar(select(Product).where(Product.sku==row['sku'])),'SKU已存在，请重新预览')
            db.add(Product(sku=row['sku'],name=row['name'],price=money(row['price'])));db.flush()
    for row in payload['dealers']:
        if row['_verified']:
            require(not db.scalar(select(Dealer).where(Dealer.code==row['code'])),'经销商已存在，请重新预览')
            u=db.scalar(select(User).where(User.username==row['sales_username'],User.role=='sales'));require(u,'责任销售不存在')
            db.add(Dealer(code=row['code'],name=row['name'],phone=row['phone'],language=row['language'],sales_id=u.id));db.flush()
    for sheet in ['stock','consignment']:
        for row in payload[sheet]:
            if not row['_verified']:continue
            code=row['warehouse_code'] if sheet=='stock' else row['location_code']
            w=db.scalar(select(Warehouse).where(Warehouse.code==code))
            did=None if sheet=='stock' else db.scalar(select(Dealer.id).where(Dealer.code==row['dealer_code']))
            kind=row['warehouse_kind'] if sheet=='stock' else 'CONSIGNMENT'
            if w:require(w.kind==kind and w.dealer_id==did,'地点类型/归属变更，重新预览')
            else:
                w=Warehouse(code=code,name=row['warehouse_name'] if sheet=='stock' else row['location_name'],kind=kind,dealer_id=did);db.add(w);db.flush()
            p=db.scalar(select(Product).where(Product.sku==row['sku']));require(p,'SKU不存在')
            qty=integer(row['quantity'],0)
            if qty:stock_entry(db,actor,w.id,p.id,qty,'OPENING',source,batch=str(b.id))
    for row in payload['receivables']:
        if not row['_verified']:continue
        dealer=db.scalar(select(Dealer).where(Dealer.code==row['dealer_code']));require(dealer,'经销商不存在')
        require(not db.scalar(select(Order).where(Order.idempotency=='opening:'+row['reference'])),'期初应收已存在')
        amount=money(row['amount']);o=Order(dealer_id=dealer.id,sales_id=dealer.sales_id,creator_id=actor.id,mode='OPENING',status='CONFIRMED',due=row['due'],total=amount,source='OPENING',terms=source+'; '+row['reference'],idempotency='opening:'+row['reference'])
        db.add(o);db.flush();db.add(Receivable(order_id=o.id,amount=amount,due=o.due,source=source))
    for row in payload['credit']:
        if not row['_verified']:continue
        dealer=db.scalar(select(Dealer).where(Dealer.code==row['dealer_code']));require(dealer,'经销商不存在')
        require(not db.scalar(select(OpeningCredit).where(OpeningCredit.source=='opening:'+row['reference'])),'期初预存已存在')
        db.add(OpeningCredit(dealer_id=dealer.id,amount=money(row['amount']),source='opening:'+row['reference']))
    b.status='COMMITTED';audit(db,actor,'IMPORT_COMMIT',b.id,source)
    return {'id':b.id}
def export_xlsx(db,actor,module,filters):
    require(module in ['orders','inventory','receivables','receipts','followups','commissions'],'导出模块无效')
    if module in ['receipts','followups','commissions']:roles(actor,'admin','finance','sales')
    data=snapshot(db,actor);key='orders' if module=='receivables' else module;rows=data[key]
    order_map={o['id']:o for o in data['orders']};dealer_map={d['id']:d for d in data['dealers']}
    if module=='inventory':
        rows=[];allowed={w['id'] for w in data['warehouses']}
        for w in data['warehouses']:
            for p in data['products']:
                movements=db.scalars(select(StockLedger).where(StockLedger.warehouse_id==w['id'],StockLedger.product_id==p['id'])).all()
                if not movements:continue
                end=date_value(filters['to']) if filters.get('to') else '9999-12-31'
                begin=date_value(filters['from']) if filters.get('from') else '0001-01-01'
                opening=sum(m.delta for m in movements if m.created[:10]<begin)
                delta=sum(m.delta for m in movements if begin<=m.created[:10]<=end)
                rows.append({'warehouse_id':w['id'],'warehouse':w['name'],'dealer_id':w['dealer_id'],'product_id':p['id'],'sku':p['sku'],'opening_quantity':opening,'period_change':delta,'ending_quantity':opening+delta})
    def matches(row):
        oid=row.get('order_id') or (row.get('id') if module in ['orders','receivables'] else None)
        order=order_map.get(oid,{})
        did=row.get('dealer_id') or order.get('dealer_id')
        dealer=dealer_map.get(did,{})
        for name in ['dealer_id','warehouse_id','product_id','sales_id']:
            value=filters.get(name)
            if not value:continue
            if name=='dealer_id': actual=did
            elif name=='sales_id':actual=row.get('sales_id') or order.get('sales_id') or dealer.get('sales_id')
            elif name=='product_id' and order:
                if not any(str(l['product_id'])==value for l in order['lines']):return False
                continue
            elif name=='warehouse_id' and order:
                if not any(str(s['warehouse_id'])==value and s['order_id']==order['id'] for s in data['shipments']):return False
                continue
            else:actual=row.get(name)
            if str(actual)!=value:return False
        if module!='inventory':
            created=(row.get('created') or row.get('date') or row.get('approved_at') or '')[:10]
            if filters.get('from') and created<date_value(filters['from']):return False
            if filters.get('to') and created>date_value(filters['to']):return False
        return True
    rows=[r for r in rows if matches(r)]
    if module=='receivables':rows=[{k:r[k] for k in ['id','number','dealer_id','dealer','sales_id','sales','due','receivable','received','outstanding','payment_status','source']} for r in rows if r['receivable'] or r['received']]
    wb=Workbook();ws=wb.active;ws.title=module
    if rows:
        headers=[k for k,v in rows[0].items() if not isinstance(v,(list,dict))];ws.append(headers)
        for row in rows:
            values=[]
            for k in headers:
                v=row.get(k)
                if isinstance(v,str) and v.startswith(('=','+','-','@')):v="'"+v
                values.append(v)
            ws.append(values)
    else:ws.append(['No records'])
    if module=='orders':
        details=wb.create_sheet('order_lines');details.append(['order_id','number','dealer_id','sku','name','quantity','standard_price_cents','unit_price_cents','gift','shipped','delivered'])
        for order in rows:
            for line in order['lines']:
                details.append([order['id'],order['number'],order['dealer_id'],safe_cell(line['sku']),safe_cell(line['name']),line['quantity'],line['standard_price'],line['unit_price'],line['gift'],line['shipped'],line['delivered']])
    if module=='inventory' and actor.role!='dealer':
        details=wb.create_sheet('stock_ledger');details.append(['id','created','warehouse_id','product_id','delta','kind','order_id','reason','actor_id'])
        valid={(r['warehouse_id'],r['product_id']) for r in rows}
        for m in db.scalars(select(StockLedger).order_by(StockLedger.id)).all():
            if (m.warehouse_id,m.product_id) not in valid:continue
            if filters.get('from') and m.created[:10]<filters['from']:continue
            if filters.get('to') and m.created[:10]>filters['to']:continue
            details.append([m.id,m.created,m.warehouse_id,m.product_id,m.delta,m.kind,m.order_id,safe_cell(m.reason),m.actor_id])
    return workbook_bytes(wb)

def safe_cell(value):
    return "'"+value if isinstance(value,str) and value.startswith(('=','+','-','@')) else value

def cell_text(value):
    if isinstance(value,datetime):return value.date().isoformat()
    if isinstance(value,date):return value.isoformat()
    return str(value).strip()
