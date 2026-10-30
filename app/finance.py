from urllib.parse import urlencode
from sqlalchemy import select
from .core import *
from .inventory import refresh_shipping

def receipt_create(db,actor,d):
    roles(actor,'admin','finance');dealer=get(db,Dealer,d['dealer_id']);account=get(db,BankAccount,d['account_id'])
    amount=money(d['amount']);require(amount>0,'到账金额须大于零')
    ref=str(d['reference']).strip().upper();require(1<=len(ref)<=200,'请填写银行交易参考号')
    require(not db.scalar(select(Receipt).where(Receipt.account_id==account.id,Receipt.reference==ref)),'该银行交易已入账，禁止重复',409)
    cid=d.get('claim_id') or None
    if cid:
        claim=get(db,Claim,cid);o=get(db,Order,claim.order_id)
        require(o.dealer_id==dealer.id and claim.status=='PENDING','申报已处理或所属经销商不同',409)
        claim.status='VERIFIED'
    r=Receipt(dealer_id=dealer.id,account_id=account.id,amount=amount,date=date_value(d['date']),reference=ref,claim_id=int(cid) if cid else None,actor_id=actor.id)
    db.add(r);db.flush();audit(db,actor,'BANK_VERIFY',r.id,f'{amount} cents; {account.name}; {ref}')
    return {'id':r.id}
def allocate_receipt(db,actor,d):
    roles(actor,'admin','finance');did=integer(d['dealer_id']);get(db,Dealer,did)
    rid=d.get('receipt_id') or None
    if rid:
        r=get(db,Receipt,rid);require(r.dealer_id==did,'到账所属经销商不同')
        remaining=r.amount-total_sum(db,Allocation.amount,Allocation.receipt_id==r.id)
    else:
        remaining=total_sum(db,OpeningCredit.amount,OpeningCredit.dealer_id==did)-total_sum(db,Allocation.amount,Allocation.dealer_id==did,Allocation.receipt_id==None)
    items=d.get('items');require(isinstance(items,list) and 1<=len(items)<=100,'请填写分配明细')
    total=0;seen=set()
    for i,row in enumerate(items):
        o=get(db,Order,row['order_id']);require(o.dealer_id==did and o.id not in seen,'订单归属错误或重复');seen.add(o.id)
        amount=money(row['amount']);require(0<amount<=outstanding(db,o.id),'分配不得超过订单未收金额')
        total+=amount;require(total<=remaining,'不能超额分配到账/期初预存款')
        db.add(Allocation(receipt_id=int(rid) if rid else None,dealer_id=did,order_id=o.id,amount=amount,actor_id=actor.id,idempotency=d['_operation_key']+':'+str(i)))
        db.flush();refresh_shipping(db,o)
    audit(db,actor,'RECEIPT_ALLOCATE',rid or 'opening',f'{did}; {total} cents')
    return {'ok':True}
def adjust_receivable(db,actor,d):
    roles(actor,'admin');o=order_access(db,actor,d['order_id']);reason=str(d.get('reason','')).strip();require(reason,'人工调整必须填写原因')
    r=db.scalar(select(Receivable).where(Receivable.order_id==o.id));require(r,'尚未产生应收')
    amount=money(d['amount'],True);require(amount!=0,'调整金额不能为零')
    require(adjusted_amount(db,o.id)+amount>=allocated(db,o.id),'冲销后应收不得低于已分配款；须先人工处理退款')
    require(not db.scalar(select(Commission).where(Commission.order_id==o.id)),'已审核佣金的订单须先人工复核佣金调整')
    db.add(Adjustment(order_id=o.id,amount=amount,reason=reason,actor_id=actor.id));audit(db,actor,'AR_ADJUST',o.id,f'{amount}; {reason}')
    return {'ok':True}
def due_change(db,actor,d):
    roles(actor,'admin','finance');o=order_access(db,actor,d['order_id']);reason=str(d.get('reason','')).strip();require(reason,'调整到期日须填写原因')
    r=db.scalar(select(Receivable).where(Receivable.order_id==o.id));require(r,'应收不存在')
    r.due=date_value(d['due']);audit(db,actor,'DUE_CHANGE',o.id,f'{r.due}; {reason}')
    return {'ok':True}
def followup_create(db,actor,d):
    roles(actor,'admin','finance','sales');did=integer(d['dealer_id']);dealer_access(db,actor,did)
    oid=d.get('order_id') or None
    if oid:require(order_access(db,actor,oid).dealer_id==did,'订单归属错误')
    note=str(d.get('note','')).strip();require(note,'催收备注不能为空')
    f=Followup(dealer_id=did,order_id=int(oid) if oid else None,note=note,next_date=date_value(d['next_date']),actor_id=actor.id)
    db.add(f);db.flush();audit(db,actor,'FOLLOWUP_CREATE',f.id,note)
    return {'id':f.id}
def whatsapp_link(db,actor,oid):
    roles(actor,'admin','finance','sales');o=order_access(db,actor,oid);d=get(db,Dealer,o.dealer_id)
    phone=''.join(c for c in d.phone if c.isdigit());require(8<=len(phone)<=15,'请填写带国家码的WhatsApp号码')
    amount=f'{Decimal(outstanding(db,o.id))/100:,.2f}'
    r=db.scalar(select(Receivable).where(Receivable.order_id==o.id));require(r,'订单未生效')
    texts={'zh':f'您好，HYNAK 订单 H{o.id:06d} 尚欠 R$ {amount}，到期日 {r.due}。请核对并安排付款，谢谢。','en':f'Hello, HYNAK order H{o.id:06d} has R$ {amount} outstanding, due {r.due}. Please review and arrange payment. Thank you.','pt':f'Olá! O pedido HYNAK H{o.id:06d} tem saldo pendente de R$ {amount}, com vencimento em {r.due}. Por favor, confira e programe o pagamento. Obrigado.'}
    return 'https://wa.me/'+phone+'?'+urlencode({'text':texts.get(d.language,texts['pt'])})
def commission_approve(db,actor,d):
    roles(actor,'admin');o=order_access(db,actor,d['order_id']);require(commission_eligible(db,o),'整单未全部交付/全额实收，或为期初应收')
    require(not db.scalar(select(Commission).where(Commission.order_id==o.id)),'佣金已审核',409)
    sales=get(db,User,o.sales_id);bps=o.commission_bps if o.commission_bps is not None else sales.commission_bps
    require(bps>0,'尚未设置已批准的佣金比例')
    # All current line items represent vehicles. Freight and zero-value gifts excluded.
    base=total_sum(db,OrderLine.quantity*OrderLine.unit_price,OrderLine.order_id==o.id,OrderLine.gift==False)
    base+=min(0,total_sum(db,Adjustment.amount,Adjustment.order_id==o.id))
    amount=int((Decimal(base)*Decimal(bps)/10000).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    c=Commission(order_id=o.id,sales_id=o.sales_id,base=max(0,base),bps=bps,amount=max(0,amount),reviewer_id=actor.id)
    db.add(c);db.flush();audit(db,actor,'COMMISSION_APPROVE',c.id,f'{base} * {bps}/10000 = {amount}')
    return {'id':c.id}
def commission_pay(db,actor,d):
    roles(actor,'admin','finance');c=get(db,Commission,d['commission_id']);require(c.status=='APPROVED','佣金已结算',409)
    o=get(db,Order,c.order_id);require(commission_eligible(db,o),'订单已发生变更，需人工复核')
    amount=money(d['amount']);require(amount==c.amount,'首版须按审核金额整笔结算')
    c.status='PAID';c.paid_date=date_value(d['date']);c.paid_amount=amount;c.note=str(d.get('note',''))
    audit(db,actor,'COMMISSION_PAY',c.id,f'{amount}; {c.paid_date}; {c.note}')
    return {'ok':True}

def commission_adjust(db,actor,d):
    roles(actor,'admin');c=get(db,Commission,d['commission_id']);reason=str(d.get('reason','')).strip();require(reason,'必须说明退款或比例调整原因')
    amount=money(d['amount'],True);require(amount<0,'此入口只记录负数调整，跨期付款须人工复核')
    require(c.amount+total_sum(db,CommissionAdjustment.amount,CommissionAdjustment.commission_id==c.id)+amount>=0,'累计调整不能超过原佣金')
    db.add(CommissionAdjustment(commission_id=c.id,amount=amount,reason=reason,actor_id=actor.id));c.status='REVIEW_REQUIRED'
    audit(db,actor,'COMMISSION_MANUAL_REVIEW',c.id,f'{amount}; {reason}; no automatic payment or clawback')
    return {'id':c.id,'status':c.status}
