# 经销商CNPJ与巴西店铺地址

新增CNPJ、UF、IBGE城市ID及名称、Logradouro、Número、Bairro、Complemento、CEP。界面按州→城市→街道等资料填写，支持城市名称搜索与地址预览；更换州时清空原城市。无门牌号可填写S/N，补充信息可留空。

CNPJ和完整地址可稍后补充，避免要求旧客户编造资料。一旦开始填写地址，必须补齐州、城市、街道、号码、区和CEP。CNPJ保存时去除常见格式字符并转为大写，兼容传统数字与现行字母数字格式，使用官方Módulo 11/ASCII-48算法校验。重复CNPJ由后端与数据库唯一索引共同拦截。空CNPJ存NULL，允许多个历史客户待补。

CEP存8位数字，页面显示00000-000；不假定CEP与某条街道的对应关系。城市由所选州及IBGE城市ID在后端校验并生成规范名称，不能提交另一个州的城市或伪造显示名称。没有将客户CNPJ、地址、电话上传到任何第三方查询服务。

数据库版本3新增字段，保留已有经销商ID、账号关系、销售归属与金额。未提供的新字段保持空值；旧API调用省略这些字段时保留已保存内容，明确传空值时可清空。

## 官方来源及本地目录

- [IBGE Localidades API](https://servicodados.ibge.gov.br/api/docs/localidades)：州和城市ID/名称。本地快照保存于app/data/brazil-localities.json，获取日期2026-10-30，共27个UF、5571个城市/地区记录。运行时不依赖外网；行政区划变化时须核对后更新快照。
- [Receita Federal CNPJ官方说明](https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/acoes-e-programas/programas-e-atividades/cnpj-alfanumerico/cnpj-alfa)：数字/字母数字格式兼容。
- [官方CNPJ校验位算法](https://www.gov.br/receitafederal/pt-br/centrais-de-conteudo/publicacoes/documentos-tecnicos/cnpj/manual-dv-cnpj.pdf)：12位数字字母主体、2位数字DV，ASCII值减48后按权重计算。
- [Correios地址指南](https://www.correios.com.br/enviar/precisa-de-ajuda/guia-de-enderecamento/guia-de-enderecamento)：Logradouro、Número、Complemento、Bairro、Localidade、UF、CEP。

## 验证

66项自动测试通过，含传统/字母数字CNPJ、错误校验位、重复ID、可选空字段、州城市不匹配、非法CEP、长度/必填、保存重读、旧客户端保留字段、资料清空、权限及旧库重复升级。原始输出见DEALER_ADDRESS_TEST_RESULTS.txt。浏览器实测州→城市联动、城市搜索及更换州后清空原城市；没有向现有客户资料填入虚构CNPJ或地址。
