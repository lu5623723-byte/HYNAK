"""Private payment proofs, shared by order creation and later payment claims."""
import secrets
from .core import require, fail, date_value, audit
from .db import DATA_DIR
from .models import Claim

async def read_proof(upload):
    require(upload is not None and hasattr(upload, 'read'), '现付订单必须上传付款水单 / Comprovante obrigatório')
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
