# 期初导入 / 导出

先由业务、仓库和财务确认切换日。模板可在“数据导入/导出→期初导入”下载，也附带在 `templates/opening-balances.xlsx`。不提供9张原表的全历史重建器。

| Sheet | 固定列 | 说明 |
|---|---|---|
| products | sku, name, price, verified | 标准价格以BRL十进制金额填写，不是分。 |
| dealers | code, name, phone, language, sales_username, verified | 先创建销售账号；language=zh/pt/en。电话含国家码。 |
| stock | warehouse_code, warehouse_name, warehouse_kind, sku, quantity, verified | kind=PRIMARY/SECONDARY。数量非负整数。不存在的地点自动创建。 |
| consignment | dealer_code, location_code, location_name, sku, quantity, verified | 位置绑定经销商，期初属于HYNAK，不形成应收。 |
| receivables | reference, dealer_code, amount, due, verified | 只填切换日未收余额。reference为唯一来源单号。due=YYYY-MM-DD。明确标记OPENING，不计本期业绩。 |
| credit | reference, dealer_code, amount, verified | 财务核实的客户预存款，不计本期银行实收。reference为唯一来源单号。 |

verified填1（已核实）或0（待核实）。待核实行随批次原始文件保存，不进入正式库存、应收或预存款。后续核实请单独提取这些行作为新批次导入，勿重复提交已经入账的期初行。

流程：选择切换日和来源说明→上传XLSX（≤10MB）→校验/预览所有行与错误→无错误才可点击“确认批次导入”。确认在单个事务中执行，任一错误全部回滚。校验商品/客户引用、数量、金额、销售归属、地点类型、重复行和来源单号。拒绝公式，先在可信Excel内复制并粘贴数值。已有SKU/经销商不通过期初模板覆盖，可在管理页修改。只导库存等数据时，其余工作表保留表头即可。

相同切换日+标准化内容得到相同指纹，重复导入不叠加。请不要通过修改批次内容重新导入同一真实库存快照；内容不同会被视为新批次，管理员须核对来源。原9表尚未提供，应作为原始只读文件另行保存；本系统保留每个导入文件的私有原始副本与来源说明。

## 金额及导出

数据库/API/导出金额字段均为**整数分**；页面显示BRL，例如580000=R$5,800.00。XLSX期初模板输入的是BRL金额，例如5800.00。所有日期按巴西圣保罗业务日期解读；操作时间保存UTC。

导出支持订单（另含订单明细sheet）、库存（期初/期间变动/期末数量及不可变流水sheet）、应收、实收、催收、佣金。由后端应用经销商/销售归属限制。经销商不能导出银行实收/佣金/催收明细，也不能借用过滤参数读取其他经销商数据。

库存日期过滤是业务期间数量汇总：开始日期前的流水=opening_quantity，期间变化=period_change，截至结束日期=ending_quantity；不提供SKU历史成本。订单仓库过滤匹配实际出库仓；未出库单不匹配仓库条件。收款按dealer/sales筛选；跨订单实收不强行归到一个SKU。

金额核对：有效应收 = 应收原额 + 审计调整；尚欠 = 有效应收 − 已分配实收。银行核实实收仅累计Receipt一次，拆分Allocation不重复累计。期初预存款 + 银行实收 − 全部分配 = 客户剩余信用。界面“应收”“银行核实实收”“已分配实收”“截图待核实”“预存款”分别展示，不能混为一项。
