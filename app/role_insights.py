"""Role-specific read-only workbenches, built from permission-scoped snapshots."""
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .core import roles, now
from .models import Reservation
from .views import snapshot
from .insights import analyze, msg, instant, ACTIVE, OPEN, SETTINGS

PROFILES={
    'admin':dict(title=msg('管理员智能工作台','Central inteligente do administrador','Administrator smart workbench'),
        intro=msg('统筹订单、资金、库存与经营风险，按优先级安排处理。','Coordene pedidos, recursos, estoque e riscos por prioridade.','Coordinate orders, funds, stock, and operational risks by priority.'),
        scope=msg('管理员查看全部业务。订单可跟随已应用筛选，库存与政策为当前全局情况，评级与余额为当前值。','Visão completa: pedidos podem seguir filtros; estoque, políticas, notas e saldos são atuais.','Administrators see all operations. Orders can follow applied filters; stock, policies, ratings, and credit reflect current conditions.'),
        all_label=msg('全部当前业务','Toda a operação atual','All current operations'),
        categories=['orders','finance','stock','credit','delivery','policy','commissions'],rule_keys=list(SETTINGS)),
    'sales':dict(title=msg('销售智能工作台','Central inteligente de vendas','Sales smart workbench'),
        intro=msg('跟进责任客户的报价审批、付款、供货、确认及佣金进度。','Acompanhe aprovações, pagamentos, abastecimento e comissões dos seus clientes.','Follow your assigned dealers’ approvals, payments, supply, confirmation, and commission progress.'),
        scope=msg('仅分析责任经销商和订单。供货分析只计责任订单的未锁定需求，并扣除全仓已锁货；未锁定竞争需求仍由仓管统筹。','Somente seus clientes e pedidos. A análise usa sua demanda sem reserva e estoque livre; o estoque coordena as demais demandas.','Only assigned dealers and orders are analyzed. Supply checks use assigned unreserved demand and stock after all reservations; warehouse staff coordinate other unreserved demand.'),
        all_label=msg('全部责任业务','Todos os meus clientes e pedidos','All assigned operations'),
        categories=['orders','finance','stock','credit','delivery','policy','commissions'],rule_keys=['urgent_overdue_days','pending_days','due_soon_days','reservation_hours','policy_days']),
    'finance':dict(title=msg('财务智能工作台','Central inteligente financeira','Finance smart workbench'),
        intro=msg('集中处理到账核实、充值、申报差额、资金分配和佣金结算。','Confira recebimentos, recargas, diferenças, alocações e comissões.','Review bank verification, top-ups, claim differences, allocations, and commission settlements.'),
        scope=msg('财务查看有权限的订单、付款及充值。日期筛选订单提醒；账户余额与经销商评级为当前值。申报凭证仍须人工核实。','Pedidos, pagamentos e recargas autorizados; datas filtram pedidos, saldos e notas são atuais. Comprovantes exigem verificação.','Finance sees authorized orders, payments, and top-ups. Dates filter order alerts; balances and ratings are current. Payment claims still require verification.'),
        all_label=msg('全部财务待办','Todos os assuntos financeiros','All finance operations'),
        categories=['finance','credit','commissions'],rule_keys=['urgent_overdue_days','pending_days','due_soon_days']),
    'warehouse':dict(title=msg('仓管智能工作台','Central inteligente de estoque','Warehouse smart workbench'),
        intro=msg('安排待出库、补货调拨、车辆编号、实际交付与寄售报售。','Organize expedições, reposição, transferências, identificação, entregas e consignação.','Organize dispatches, replenishment, transfers, vehicle IDs, deliveries, and consignment reports.'),
        scope=msg('只展示交付职责相关的订单、数量和仓库信息。库存与缺货考虑全部待发竞争需求；财务核款仍由对应账号处理。','Pedidos, quantidades e depósitos do fluxo de entrega. Estoque considera a demanda total; o financeiro verifica pagamentos.','Only fulfillment-related orders, quantities, and warehouse information are shown. Stock considers all pending demand; payment verification stays with the appropriate role.'),
        all_label=msg('全部仓储交付待办','Todas as tarefas de estoque e entrega','All warehouse and delivery tasks'),
        categories=['stock','delivery'],rule_keys=['low_stock_units','pickup_days','reservation_hours']),
    'dealer':dict(title=msg('经销商智能工作台','Minha central inteligente','Dealer smart workbench'),
        intro=msg('查看自己的采购、付款、充值余额、提货及寄售进度。','Acompanhe suas compras, pagamentos, saldo, retiradas e consignação.','Track your own purchases, payments, prepaid credit, pickup, and consignment progress.'),
        scope=msg('仅展示当前经销商自己的资料、订单、余额和寄售点。订单可按已应用日期筛选，余额和评级始终为当前值。','Somente seus dados, pedidos, saldo e locais consignados. As datas filtram pedidos; saldo e nota são atuais.','Only your own records, orders, credit, and consignment sites are shown. Dates can filter orders; credit and ratings are current.'),
        all_label=msg('我的全部业务','Todas as minhas operações','All my operations'),
        categories=['orders','finance','credit','delivery','stock'],rule_keys=['urgent_overdue_days','pending_days','due_soon_days','pickup_days','low_stock_units']),
}

BASE_CODES={
    'admin':None,
    'sales':{'OVERDUE','DUE_SOON','ADMIN_REVIEW','CASH_REVIEW','BALANCE_ORDER','ORDER_STALLED','PAYMENT_PROOFS','STOCK_SHORTAGE','TRANSFER_NEEDED','RESERVATION_EXPIRING','CREDIT_RISK','RECHARGE_REVIEW','DELIVERY_FOLLOWUP','CONSIGNMENT_REPORT','POLICY_ENDING'},
    'finance':{'OVERDUE','DUE_SOON','CASH_REVIEW','BALANCE_ORDER','PAYMENT_PROOFS','UNALLOCATED_CREDIT','RECHARGE_REVIEW','CREDIT_RISK'},
    'warehouse':{'STOCK_SHORTAGE','TRANSFER_NEEDED','LOW_STOCK','NEGATIVE_STOCK','RESERVATION_EXPIRING','DELIVERY_FOLLOWUP','CONSIGNMENT_REPORT'},
    'dealer':{'OVERDUE','DUE_SOON','ADMIN_REVIEW','CASH_REVIEW','BALANCE_ORDER','ORDER_STALLED','PAYMENT_PROOFS','RECHARGE_REVIEW','CREDIT_RISK','DELIVERY_FOLLOWUP','CONSIGNMENT_REPORT'},
}
DESTINATIONS={
    'admin':{'order','credit','inventory','finance','wallet','collections','consignment','policies','commissions'},
    'sales':{'order','credit','inventory','finance','wallet','collections','consignment','policies','commissions'},
    'finance':{'order','credit','finance','wallet','collections','commissions'},
    'warehouse':{'order','inventory','consignment'},
    'dealer':{'order','credit','finance','wallet','consignment'},
}


def profile(role):return PROFILES[role]


def role_report(data,role,reservations=(),*,at=None,options=None,order_ids=None,dealer_ids=None):
    """data must already come from snapshot(db, actor), never from an admin snapshot."""
    at=at or now();day=instant(at).astimezone(ZoneInfo('America/Sao_Paulo')).date().isoformat()
    result=analyze(data,reservations,at=at,options=options,order_ids=order_ids,dealer_ids=dealer_ids)
    allowed=BASE_CODES[role];alerts=[a for a in result['alerts'] if allowed is None or a['code'] in allowed]
    orders={o['id']:o for o in data['orders']};dealers={d['id']:d for d in data['dealers']}
    oids=set(orders) if order_ids is None else set(order_ids)
    dids=set(dealers) if dealer_ids is None else set(dealer_ids)
    visible={oid:o for oid,o in orders.items() if oid in oids and o['status'] not in {'CANCELED','REJECTED'}}
    def action(kind,identity=None,**kw):return dict(kind=kind,id=identity,**kw)
    def add(code,identity,category,title,reason,advice,owner,*,priority='MEDIUM',amount=None,quantity=None,actions=(),**facts):
        alerts.append(dict(key=f'{code}:{identity}',code=code,category=category,priority=priority,title=title,reason=reason,
                           advice=advice,owner=owner,amount=amount,quantity=quantity,days=0,actions=list(actions),facts=facts))

    # Tailor responsibilities as well as text: an alert never grants an action.
    for a in alerts:
        code=a['code'];f=a['facts'];oid=f.get('order_id');o=orders.get(oid)
        if role=='sales':
            if code=='ADMIN_REVIEW':
                a['advice']=msg('核对报价、政策与备注是否完整，跟进管理员审批；价格例外仍逐单审核。','Confira cotação, política e notas e acompanhe a aprovação do administrador.','Check the quote, policy, and notes and follow up with the administrator; price exceptions still need per-order approval.')
            elif code in {'CASH_REVIEW','PAYMENT_PROOFS','RECHARGE_REVIEW'}:
                a['advice']=msg('查看客户已提交的凭证和进度，协助补充信息，由财务或管理员按原权限核实。','Acompanhe comprovantes e ajude o cliente a completar dados; a verificação segue as permissões existentes.','Track submitted proofs and help the dealer complete information; verification remains with authorized finance or administrator accounts.')
            elif code=='POLICY_ENDING':
                a['advice']=msg('核对责任订单使用的方案及有效期，与管理员确认下一期已发布政策。','Confira a validade das políticas e confirme a próxima com o administrador.','Check policy validity for assigned orders and coordinate upcoming published policies with the administrator.')
            elif code=='CONSIGNMENT_REPORT':
                a['advice']=msg('核对责任客户的报售数量和原订单，补充资料并跟进仓管或管理员确认。','Confira quantidades e pedido do seu cliente e acompanhe a confirmação do estoque ou administrador.','Check your assigned dealer’s reported quantities and original order, complete missing information, and follow up with warehouse staff or the administrator for confirmation.')
            elif code=='STOCK_SHORTAGE':
                f['scoped_remaining']=f.pop('global_remaining');f['scoped_reserved']=f.pop('global_reserved');f['scoped_unreserved']=f.pop('global_unreserved')
                a['title']=msg('责任订单供货需协调','Abastecimento dos seus pedidos requer coordenação','Assigned orders need supply coordination')
                a['reason']=msg(f'{f["sku"]}：责任订单未锁定需求 {f["scoped_unreserved"]} 台，供货可用 {f["global_available"]} 台，当前缺口 {a["quantity"]} 台。',f'{f["sku"]}: demanda sua sem reserva {f["scoped_unreserved"]}, livre {f["global_available"]}, falta {a["quantity"]}.',f'{f["sku"]}: assigned unreserved demand {f["scoped_unreserved"]}, free stock {f["global_available"]}, shortage {a["quantity"]}.')
                a['advice']=msg('与仓管核对责任订单及赠品的供货安排，其他客户尚未锁定的需求由仓管统一协调。','Coordene produtos e brindes com o estoque, que organiza a demanda dos demais clientes.','Coordinate products and gifts with warehouse staff, who manage other dealers’ unreserved demand.')
        if role=='warehouse':
            a['amount']=None
            if code=='RESERVATION_EXPIRING':
                a['reason']=msg(f'订单 H{oid:06d} 的有效锁货即将到期，请与销售核对后续出库安排。',f'A reserva do pedido H{oid:06d} está próxima do vencimento; coordene a expedição com vendas.',f'Order H{oid:06d} has a reservation nearing expiry; coordinate dispatch with sales.')
                a['advice']=msg('核对到期时间、车型数量和实际仓库，后续续配按原权限处理。','Confira prazo, quantidades e depósito; novas reservas seguem as permissões existentes.','Review expiry, model quantities, and warehouse; any reallocation follows existing permissions.')
                a['owner']=msg('仓管 / 销售','Estoque / vendas','Warehouse / sales')
        if role=='dealer':
            a['actions']=[action('order',oid)] if oid else a['actions']
            if code=='ADMIN_REVIEW':
                a['title']=msg('采购订单等待内部审核','Compra aguardando aprovação interna','Purchase order awaiting internal review')
                a['advice']=msg('核对自己的订单内容；如需变更或了解审核进度，请联系责任销售。','Confira sua compra e contate seu vendedor para alterações ou andamento.','Review your own order and contact your sales representative for changes or approval progress.')
            elif code=='CASH_REVIEW':
                a['title']=msg('采购付款等待财务核实','Pagamento da compra aguardando verificação','Purchase payment awaiting finance verification')
                a['advice']=msg('查看付款水单与核实进度，信息有误时补充资料或联系销售；确认到账后订单才会按原流程生效。','Acompanhe comprovantes e corrija os dados com seu vendedor; o pedido segue a verificação bancária.','Review proofs and verification progress and correct information with your sales contact; bank verification precedes activation.')
            elif code=='BALANCE_ORDER':
                a['advice']=msg('查看已核实可用余额及欠款抵扣明细；需补充余额时先充值并等待到账核实。','Confira saldo e alocações; novas recargas devem ser verificadas.','Review verified available credit and allocations; new top-ups must be verified before use.')
            elif code=='ORDER_STALLED':
                a['title']=msg('采购订单进度待确认','Confira o andamento da compra','Purchase order progress needs confirmation')
                a['advice']=msg('查看订单状态；待客户确认的订单请联系销售获取有效确认链接，需修订时由销售或管理员处理。','Confira o status; peça ao vendedor um link válido para confirmar ou revisar a compra.','Review the status; ask sales for a valid confirmation link or for an authorized order revision.')
            elif code in {'OVERDUE','DUE_SOON'}:
                a['advice']=msg('查看本单剩余未付金额和已核实付款，安排付款并上传对应日期与水单；有疑问请联系责任销售。','Confira o saldo e os pagamentos; programe o pagamento e envie data e comprovante.','Review the unpaid balance and verified payments, arrange payment, and upload the matching date and proof; contact sales if needed.')
            elif code=='PAYMENT_PROOFS':
                a['title']=msg('已提交的付款凭证等待核实','Comprovantes enviados aguardando verificação','Submitted payment proofs await verification')
                a['advice']=msg('可查看各次付款日期、水单及核实状态；已提交凭证由财务或管理员核实后才抵扣余额。','Veja datas e comprovantes; a alocação depende de verificação autorizada.','View installment dates and proofs; only authorized verification reduces the balance.')
            elif code=='RECHARGE_REVIEW':
                a['title']=msg('我的充值申报等待核实','Minha recarga aguardando verificação','My top-up is awaiting verification')
                a['advice']=msg('在充值记录查看水单与进度；核实后系统抵扣欠款并保留剩余可用余额。','Acompanhe a recarga; após verificação o sistema aloca dívidas e mantém o saldo restante.','Track the top-up and proof; after verification, the system allocates debts and retains remaining credit.')
            elif code=='CREDIT_RISK':
                a['title']=msg('我的信用评级需要关注','Minha avaliação requer atenção','My credit rating needs attention')
                a['advice']=msg('查看自己的付款及时度、金额履约和采购数量依据，核对尚欠及已提交付款的状态。','Confira pontualidade, valores e compras e acompanhe pagamentos pendentes.','Review your payment timing, amount fulfillment, and purchase-volume evidence and check outstanding or pending payments.')
            elif code=='CONSIGNMENT_REPORT':
                a['title']=msg('我的寄售报售等待确认','Minha venda consignada aguarda confirmação','My consignment sale report awaits confirmation')
                a['advice']=msg('查看自己寄售点的车型和已报售数量，等待仓管或管理员按原流程确认。','Confira os modelos e quantidades do seu local e aguarde a confirmação autorizada.','Review your site’s models and reported sales and await authorized warehouse or administrator confirmation.')
            elif code=='DELIVERY_FOLLOWUP':
                a['title']=msg('我的订单提货交付待跟进','A retirada da minha compra precisa de acompanhamento','My order pickup needs follow-up')
                a['advice']=msg('查看对应出库单状态，与销售或仓管确认提货时间；实际交付由仓管更新。','Confira a expedição e combine a retirada com vendas ou estoque.','Review dispatch status and arrange pickup with sales or warehouse staff; warehouse staff record actual delivery.')

    if role in {'admin','warehouse'}:
        for o in visible.values():
            if o['source']!='CURRENT' or o['status'] not in OPEN:continue
            remaining=sum(max(0,l['quantity']-l['shipped']) for l in o['lines'])
            if not remaining:continue
            gifts=sum(max(0,l['quantity']-l['shipped']) for l in o['lines'] if l['gift'])
            add('DISPATCH_TODO',o['id'],'delivery',msg('生效订单待出库','Pedido ativo aguardando expedição','Active order awaiting dispatch'),
                msg(f'{o["number"]} · {o["dealer"]}，待发 {remaining} 台，其中赠品 {gifts} 台。',f'{o["number"]} · {o["dealer"]}: pendentes {remaining}, incluindo {gifts} brindes.',f'{o["number"]} · {o["dealer"]}: {remaining} units pending, including {gifts} gifts.'),
                msg('核对可用库存、锁货、出库仓和车辆编号，按原流程创建出库单；缺货先协调补货或调拨。','Confira estoque, reservas, depósito e identificação antes de criar a expedição.','Verify stock, reservations, dispatch warehouse, and vehicle IDs before creating the dispatch; coordinate shortages first.'),
                msg('仓管','Estoque','Warehouse'),quantity=remaining,actions=[action('order',o['id']),action('inventory')],order_id=o['id'],number=o['number'],remaining_quantity=remaining,gift_quantity=gifts)
    if role in {'admin','warehouse','dealer','sales'}:
        alerted={a['facts'].get('shipment_id') for a in alerts if a['code']=='DELIVERY_FOLLOWUP'}
        for s in data['shipments']:
            o=visible.get(s['order_id'])
            if s['status']!='PENDING_PICKUP' or not o or s['id'] in alerted:continue
            add('PICKUP_TODO',s['id'],'delivery',msg('我的订单等待提货' if role=='dealer' else '已出库订单等待提货','Pedido expedido aguardando retirada','Dispatched order awaiting pickup'),
                msg(f'{o["number"]} · {o["dealer"]}，出库单 #{s["id"]} 当前等待实际提货。',f'{o["number"]}: expedição #{s["id"]} aguardando retirada.',f'{o["number"]}: dispatch #{s["id"]} awaits actual pickup.'),
                msg('联系销售或仓管确认提货安排，实际交付后由仓管更新状态。','Combine a retirada com vendas ou estoque; o estoque registra a entrega real.','Coordinate pickup with sales or warehouse staff, who record actual delivery.'),
                msg('经销商 / 仓管','Revendedor / estoque','Dealer / warehouse'),priority='LOW',actions=[action('order',o['id'])],order_id=o['id'],shipment_id=s['id'])

    if role in {'admin','finance'}:
        pending=defaultdict(list);claims={c['id']:c for c in data['claims']}
        for c in data['claims']:
            if c['status']=='PENDING':pending[c['order_id']].append(c)
        for oid,o in visible.items():
            if o['status']!='WAITING_FINANCE_REVIEW' or o.get('payment_method')=='BALANCE' or not pending[oid]:continue
            verified=sum(r['amount'] for r in data['receipts'] if claims.get(r.get('claim_id'),{}).get('order_id')==oid)
            expected=max(0,o['total']-verified);declared=sum(c['amount'] for c in pending[oid])
            if declared==expected:continue
            add('PAYMENT_AMOUNT_CHECK',oid,'finance',msg('付款申报与待核实金额有差异','Diferença entre comprovantes e valor pendente','Payment claims differ from the expected amount'),
                msg(f'{o["number"]} 的待核实申报合计与订单扣除已核实银行款后的金额不同。',f'{o["number"]}: os comprovantes pendentes diferem do valor restante após créditos verificados.',f'{o["number"]}: pending claims differ from the order amount remaining after verified bank receipts.'),
                msg('核对分次付款、已有银行入账和水单归属；差额由财务核实，避免重复确认同笔到账。','Confira parcelas, créditos existentes e comprovantes para evitar duplicação.','Check installments, existing bank receipts, and proof attribution to avoid duplicate verification.'),
                msg('财务','Financeiro','Finance'),amount=abs(declared-expected),actions=[action('order',oid),action('finance')],order_id=oid,expected_unverified=expected,pending_claim_amount=declared,verified_bank_amount=verified)

    if role in {'admin','finance','sales'}:
        latest={}
        for f in sorted(data['followups'],key=lambda f:(f['created'],f['id']),reverse=True):latest.setdefault((f['dealer_id'],f.get('order_id')),f)
        for (did,oid),f in latest.items():
            if did not in dids or f['next_date']>day or dealers[did]['outstanding']<=0:continue
            if oid and (oid not in visible or visible[oid]['outstanding']<=0):continue
            add('FOLLOWUP_DUE',f['id'],'finance',msg('回款跟进日期已到','Cobrança programada para acompanhamento','Scheduled collection follow-up is due'),
                msg(f'{dealers[did]["name"]}，最近一次约定跟进日期 {f["next_date"]}。',f'{dealers[did]["name"]}: próxima cobrança {f["next_date"]}.',f'{dealers[did]["name"]}: latest scheduled follow-up {f["next_date"]}.'),
                msg('查看最近跟进记录和已核实付款，联系客户后记录下一次日期。','Revise notas e pagamentos e registre a próxima data após contato.','Review the latest notes and verified payments and record the next follow-up after contact.'),
                msg('销售 / 财务','Vendas / financeiro','Sales / finance'),actions=[action('collections',did)],followup_id=f['id'],dealer_id=did,next_date=f['next_date'],**({'order_id':oid} if oid else {}))
        commissions=[c for c in data['commissions'] if c['order_id'] in visible and (role!='sales' or c['sales_id']==data['user']['id'])]
        for c in commissions:
            if c['status'] not in {'APPROVED','REVIEW_REQUIRED'}:continue
            o=orders[c['order_id']];changed=not o.get('commission_eligible',False);review=c['status']=='REVIEW_REQUIRED' or changed
            add('COMMISSION_PROGRESS',c['id'],'commissions',msg('佣金需要人工复核' if review else '已审核佣金等待结算','Comissão requer revisão' if review else 'Comissão aprovada aguardando liquidação','Commission requires review' if review else 'Approved commission awaiting settlement'),
                msg(f'{o["number"]} 的佣金'+('当前订单结算条件需要重新核对。' if changed else '已标记人工复核。' if review else '已经审核，尚未记录结算完成。'),f'{o["number"]}: comissão '+('requer conferência das condições atuais do pedido.' if changed else 'marcada para revisão.' if review else 'aprovada e ainda não liquidada.'),f'{o["number"]}: commission '+('needs a review of current order settlement conditions.' if changed else 'is marked for review.' if review else 'is approved but not yet recorded as settled.')),
                msg('查看自己的佣金审核、调整与结算进度，有疑问联系管理员或财务。','Acompanhe a revisão, os ajustes e a liquidação da sua comissão e consulte o administrador ou financeiro.','Track your own commission approval, adjustments, and settlement progress; contact the administrator or finance with questions.') if role=='sales' else msg('查看佣金审核、调整及结算记录；按原权限复核或记录实际结算。','Confira aprovações, ajustes e pagamentos e siga as permissões existentes.','Review approval, adjustments, and settlement records and use existing permissions for any action.'),
                msg('管理员 / 财务','Administrador / financeiro','Administrator / finance'),amount=max(0,c['amount']+c.get('adjustments',0)-(c.get('paid_amount') or 0)),actions=[action('commissions'),action('order',o['id'])],commission_id=c['id'],order_id=o['id'],commission_status=c['status'],eligibility_changed=changed)
        if role in {'admin','sales'}:
            existing={c['order_id'] for c in data['commissions']}
            for o in visible.values():
                if not o.get('commission_eligible') or o['id'] in existing or (role=='sales' and o['sales_id']!=data['user']['id']):continue
                rate=o.get('commission_bps')
                if rate is None:rate=next((u.get('commission_bps',0) for u in data['users'] if u['id']==o['sales_id']),0) if role=='admin' else data['user'].get('commission_bps',0)
                if rate<=0:continue
                add('COMMISSION_APPROVAL',o['id'],'commissions',msg('佣金条件待管理员核对','Condições da comissão aguardam administrador','Commission conditions await administrator review'),
                    msg(f'{o["number"]} 已满足系统交付和回款条件，尚无佣金审核记录。',f'{o["number"]}: atende às condições de entrega e pagamento, sem revisão da comissão.',f'{o["number"]}: system delivery and payment conditions are met, but commission approval is not yet recorded.'),
                    msg('由管理员在佣金界面核对计提基数和比例后审核。','O administrador confere base e percentual antes de aprovar.','The administrator reviews the commission basis and rate before approval.'),
                    msg('管理员','Administrador','Administrator'),priority='LOW',actions=[action('commissions'),action('order',o['id'])],order_id=o['id'])
    if role=='dealer':
        active_products={p['id'] for p in data['products'] if p['active']}
        for i in data['inventory']:
            if i['kind']!='CONSIGNMENT' or i.get('dealer_id') not in dids or i['product_id'] not in active_products or i['available']>result['rules']['low_stock_units']:continue
            add('MY_CONSIGNMENT_STOCK',f'{i["warehouse_id"]}-{i["product_id"]}','stock',msg('我的寄售点库存需关注','Estoque do meu local consignado requer atenção','My consignment site needs stock attention'),
                msg(f'{i["warehouse"]} · {i["sku"]}，当前可用 {i["available"]} 台。',f'{i["warehouse"]} · {i["sku"]}: disponível {i["available"]}.',f'{i["warehouse"]} · {i["sku"]}: {i["available"]} available.'),
                msg('核对本寄售点的实物和报售记录，需补货时联系责任销售。','Confira estoque físico e vendas e contate seu vendedor para reposição.','Review your site’s physical stock and sale reports and contact your sales representative for replenishment.'),
                msg('经销商 / 销售','Revendedor / vendas','Dealer / sales'),priority='LOW',quantity=i['available'],actions=[action('consignment',i['dealer_id'],warehouse_id=i['warehouse_id'])],warehouse_id=i['warehouse_id'],product_id=i['product_id'],sku=i['sku'])

    # Defense in depth: destinations and nested IDs are checked against this scoped data.
    safe=[]
    for a in alerts:
        a['actions']=[x for x in a['actions'] if x['kind'] in DESTINATIONS[role]
            and (x['kind']!='order' or x['id'] in orders)
            and (x['kind'] not in {'credit','wallet','collections','consignment'} or x['id'] in dealers)
            and (x['kind']!='consignment' or any(w['id']==x.get('warehouse_id') and w.get('dealer_id')==x['id'] for w in data['warehouses']))]
        if role=='warehouse':
            a['amount']=None
            fields={'order_id','number','dealer_id','dealer','product_id','sku','global_remaining','global_reserved','global_unreserved','global_available','related_order_ids','warehouse_id','expires','shipment_id','missing_serial','report_id','needed','remaining_quantity','gift_quantity'}
            a['facts']={k:v for k,v in a['facts'].items() if k in fields}
        safe.append(a)
    safe.sort(key=lambda a:({'HIGH':0,'MEDIUM':1,'LOW':2}[a['priority']],-a['days'],-(a['amount'] or 0),a['key']))
    counts=Counter(a['priority'] for a in safe)
    result.update(version='HYNAK-WORKBENCH-v2',role=role,profile=PROFILES[role],alerts=safe,
                  rules={k:v for k,v in result['rules'].items() if k in PROFILES[role]['rule_keys']})
    s=dict(total=len(safe),high=counts['HIGH'],medium=counts['MEDIUM'],low=counts['LOW'],categories=dict(Counter(a['category'] for a in safe)))
    if role!='warehouse':s.update(overdue_amount=sum(a['amount'] for a in safe if a['code']=='OVERDUE'),recharge_claimed=sum(a['amount'] for a in safe if a['code']=='RECHARGE_REVIEW'))
    if role in {'admin','sales','warehouse'}:s['shortage_units']=sum(a['quantity'] for a in safe if a['code']=='STOCK_SHORTAGE')
    result['summary']=s
    cards=[dict(label=msg('高优先级','Alta prioridade','High priority'),value=s['high'],format='count'),dict(label=msg('全部待办','Todos os alertas','All alerts'),value=s['total'],format='count')]
    def card(label,value,fmt='count'):cards.append(dict(label=label,value=value,format=fmt))
    if role=='admin':
        card(msg('实际逾期欠款','Saldo vencido','Actual overdue balance'),s['overdue_amount'],'money');card(msg('待核实充值申报','Recargas não verificadas','Unverified top-up claims'),s['recharge_claimed'],'money');card(msg('待发全局缺口','Falta global para expedição','Global dispatch shortage'),s['shortage_units'],'units')
    elif role=='sales':
        card(msg('责任订单逾期欠款','Saldo vencido dos meus pedidos','Assigned overdue balance'),s['overdue_amount'],'money');card(msg('待客户确认订单','Pedidos aguardando cliente','Orders awaiting dealer confirmation'),sum(o['status']=='WAITING_DEALER_CONFIRMATION' for o in visible.values()));card(msg('供货协调事项','Assuntos de abastecimento','Supply coordination items'),sum(a['code'] in {'STOCK_SHORTAGE','TRANSFER_NEEDED'} for a in safe))
    elif role=='finance':
        card(msg('待核实现付订单','Pedidos à vista a verificar','Cash orders to verify'),sum(a['code']=='CASH_REVIEW' for a in safe));card(msg('待核实充值申报','Recargas não verificadas','Unverified top-up claims'),s['recharge_claimed'],'money');card(msg('实际逾期欠款','Saldo vencido','Actual overdue balance'),s['overdue_amount'],'money')
    elif role=='warehouse':
        pending=[a for a in safe if a['code']=='DISPATCH_TODO'];card(msg('待出库订单','Pedidos a expedir','Orders awaiting dispatch'),len(pending));card(msg('待发台数（含赠品）','Unidades a expedir (com brindes)','Pending units including gifts'),sum(a['quantity'] for a in pending),'units');card(msg('当前缺口','Falta atual','Current shortage'),s['shortage_units'],'units')
    else:
        card(msg('我的订单尚欠','Meu saldo devedor','My outstanding orders'),sum(o['outstanding'] for o in visible.values() if o['status'] in ACTIVE),'money');card(msg('我的可用余额','Meu crédito disponível','My available prepaid credit'),sum(w['available'] for w in data['wallets'] if w['dealer_id'] in dids),'money');card(msg('待提货出库单','Expedições aguardando retirada','Dispatches awaiting pickup'),sum(x['status']=='PENDING_PICKUP' and x['order_id'] in visible for x in data['shipments']))
    result['cards']=cards
    return result


def summary(db,actor,options=None,cohort=None):
    roles(actor,*PROFILES)
    data=snapshot(db,actor)
    lines={line['id']:line for o in data['orders'] for line in o['lines']}
    rows=[dict(order_id=r.order_id,warehouse_id=r.warehouse_id,product_id=lines[r.line_id]['product_id'],quantity=r.quantity,expires=r.expires,active=r.active)
          for r in db.scalars(select(Reservation).where(Reservation.active==True)).all() if r.line_id in lines]
    options={k:v for k,v in (options or {}).items() if k in PROFILES[actor.role]['rule_keys']}
    return role_report(data,actor.role,rows,options=options,order_ids={o['id'] for o in cohort['orders']} if cohort is not None else None,
                       dealer_ids={d['id'] for d in cohort['dealers']} if cohort is not None else None)
