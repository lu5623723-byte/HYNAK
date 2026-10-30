"""Official IBGE locality snapshot and Receita Federal CNPJ check digits.

These validations operate locally. No dealer data is sent to third parties.
"""
import json
import re
from functools import lru_cache
from pathlib import Path
from .core import require

@lru_cache(maxsize=1)
def localities():
    return json.loads((Path(__file__).parent/'data'/'brazil-localities.json').read_text(encoding='utf-8'))

def normalize_cnpj(value):
    if value is None or not str(value).strip():return None
    raw=re.sub(r'[.\s/\-]', '', str(value)).upper()
    require(re.fullmatch(r'[A-Z0-9]{12}[0-9]{2}',raw) is not None,
            'CNPJ格式无效，应为12位数字/字母加2位数字校验码 / CNPJ inválido')
    require(len(set(raw))!=1,'CNPJ校验码不正确 / Dígitos verificadores inválidos')
    calculated=raw[:12]
    for weights in [(5,4,3,2,9,8,7,6,5,4,3,2),(6,5,4,3,2,9,8,7,6,5,4,3,2)]:
        remainder=sum((ord(char)-48)*weight for char,weight in zip(calculated,weights))%11
        calculated+=str(0 if remainder<2 else 11-remainder)
    require(calculated==raw,'CNPJ校验码不正确 / Dígitos verificadores do CNPJ inválidos')
    return raw

ADDRESS_FIELDS={'address_state':2,'address_city_id':7,'address_street':200,
                'address_number':30,'address_district':150,'address_complement':200,'address_cep':9}

def dealer_address(data,existing):
    values={key:str((data[key] if key in data else getattr(existing,key,'')) or '').strip()
            for key in ADDRESS_FIELDS}
    for key,limit in ADDRESS_FIELDS.items():
        require(len(values[key])<=limit,f'地址字段过长 / Campo muito longo: {key}')
    values['address_state']=values['address_state'].upper()
    if not any(values.values()):return dict(values,address_city='')
    states={s['uf'] for s in localities()['states']}
    require(values['address_state'] in states,'请先选择巴西州（UF） / Selecione a UF')
    city=next((c for c in localities()['cities'][values['address_state']] if c['id']==values['address_city_id']),None)
    require(city is not None,'城市不属于所选州，请重新选择 / Município não pertence à UF selecionada')
    require(values['address_street'] and values['address_number'] and values['address_district'],
            '请完整填写街道、门牌号（无号码可填S/N）和区 / Preencha logradouro, número (ou S/N) e bairro')
    cep=values['address_cep']
    require(re.fullmatch(r'[0-9]{5}-?[0-9]{3}',cep) is not None,'CEP应为8位数字，例如01310-100 / CEP inválido')
    values['address_cep']=cep.replace('-','')
    return dict(values,address_city=city['name'])
