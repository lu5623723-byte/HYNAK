"""Private payment proofs, shared by order creation and later payment claims."""
import secrets, hashlib
from .core import require, fail, date_value, audit, money, today
from .db import DATA_DIR
from .models import Claim, Recharge

async def read_proof(upload):
    require(upload is not None and hasattr(upload, 'read'), '请上传付款或充值水单 / Comprovante obrigatório')
    raw = await upload.read(5 * 1024 * 1024 + 1)
    require(0 < len(raw) <= 5 * 1024 * 1024, '付款水单最多5MB / Máximo 5MB')
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        ext, mime = '.png', 'image/png'
    elif raw.startswith(b'\xff\xd8\xff'):
        ext, mime = '.jpg', 'image/jpeg'
    elif raw.startswith(b'%PDF-'):
        ext, mime = '.pdf', 'application/pdf'
    else:
        fail('付款水单仅支持PNG/JPEG/PDF / Use PNG, JPEG ou PDF')
    return {'raw': raw, 'extension': ext, 'mime': mime}

async def read_order_payments(payload, form):
    """Validate every dated consignment proof before writing any order/files."""
    rows=payload.get('payments',[])
    require(isinstance(rows,list) and len(rows)<=20,'寄售付款记录最多20笔 / Máximo de 20 pagamentos')
    require(not rows or payload.get('sale_type')=='CONSIGNMENT','仅寄售订单可在此添加分次付款记录')
    expected={f'payment_proof_{i}' for i in range(len(rows))}
    for name,value in form.multi_items():
        if name.startswith('payment_proof_'):
            require(name in expected,'付款水单缺少对应的付款日期和金额')
    result=[];size=0;seen=set()
    for i,row in enumerate(rows):
        require(isinstance(row,dict),'付款记录格式无效')
        amount=money(row.get('amount'));require(amount>0,'每笔付款金额须大于零')
        paid_date=date_value(row.get('paid_date'))
        require(paid_date<=today().isoformat(),'请填写已实际付款的日期；付款计划请填写在备注中')
        reference=str(row.get('reference','')).strip()
        require(len(reference)<=200,'付款参考号最多200字符')
        require(len(form.getlist(f'payment_proof_{i}'))==1,'每笔付款须上传一份对应水单')
        proof=await read_proof(form.get(f'payment_proof_{i}'))
        size+=len(proof['raw']);require(size<=10*1024*1024,'一次提交的水单合计最多10MB，可在订单详情继续补传')
        file_hash=hashlib.sha256(proof['raw']).hexdigest()
        identity=(amount,paid_date,reference,file_hash)
        require(identity not in seen,'请勿重复添加相同的付款记录和水单');seen.add(identity)
        result.append(dict(amount=amount,paid_date=paid_date,reference=reference,proof=proof,file_hash=file_hash))
    return result

def save_claim(db, actor, order, proof, amount, paid_date, paid_at=None, reference=''):
    require(amount > 0, '申报金额须大于零')
    path = DATA_DIR / 'attachments' / (secrets.token_hex(24) + proof['extension'])
    try:
        path.write_bytes(proof['raw'])
        claim = Claim(order_id=order.id, amount=amount, paid_date=date_value(paid_date),
                      paid_at=paid_at, reference=str(reference).strip()[:200],
                      attachment=path.name, media_type=proof['mime'])
        db.add(claim)
        db.flush()
        audit(db, actor, 'PAYMENT_CLAIM', claim.id,
              f'order {order.id}; {amount}; paid_at={paid_at}; awaiting bank verification')
        return claim, path
    except Exception:
        path.unlink(missing_ok=True)
        raise

def save_recharge(db,actor,did,proof,amount,paid_date,reference=''):
    require(amount>0,'充值金额须大于零')
    path=DATA_DIR/'attachments'/(secrets.token_hex(24)+proof['extension'])
    try:
        path.write_bytes(proof['raw'])
        r=Recharge(dealer_id=did,amount=amount,paid_date=date_value(paid_date),reference=str(reference).strip()[:200],attachment=path.name,media_type=proof['mime'],creator_id=actor.id)
        db.add(r);db.flush();audit(db,actor,'RECHARGE_CLAIM',r.id,f'dealer {did}; {amount} cents; awaiting verification')
        return r,path
    except Exception:
        path.unlink(missing_ok=True)
        raise
