import os
import sqlite3
import subprocess
import sys
from pathlib import Path
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from app.brazil import normalize_cnpj, localities
from app.db import Session, engine
from app.models import Dealer
from app.main import app

def dealer_payload(e,**changes):
    return dict(id=e.did,code='D1',name='Dealer 1',sales_id=e.sid,phone='+5511991234567',language='pt',**changes)

def address(**changes):
    data=dict(address_state='SP',address_city_id='3550308',address_street='Avenida de Teste',
              address_number='S/N',address_district='Bairro de Teste',address_complement='Loja 2',address_cep='01310-100')
    data.update(changes)
    return data

@pytest.mark.parametrize('value,expected',[
    ('11.222.333/0001-81','11222333000181'),
    ('12.ABC.345/01DE-35','12ABC34501DE35'),
    ('12.abc.345/01de-35','12ABC34501DE35'),
    ('',''),(None,''),
])
def test_numeric_and_current_alphanumeric_cnpj(value,expected):
    assert (normalize_cnpj(value) or '')==expected

@pytest.mark.parametrize('value',['00000000000000','11111111111111','11.222.333/0001-80','12.ABC.345/01DE-36','12ABC34501DEXY','12ABC34501DÉ35','garbage','11222333000181<script>'])
def test_bad_cnpj_is_rejected(value):
    with pytest.raises(HTTPException):normalize_cnpj(value)

def test_state_city_catalog_is_offline_and_authenticated(env):
    e=env
    with TestClient(app) as anonymous:assert anonymous.get('/api/brazil/locations').status_code==401
    r=e.admin.get('/api/brazil/locations');assert r.status_code==200
    data=r.json();assert len(data['states'])==27
    assert sum(len(cities) for cities in data['cities'].values())>=5570
    assert {'id':'3550308','name':'São Paulo'} in data['cities']['SP']
    assert {'id':'3304557','name':'Rio de Janeiro'} in data['cities']['RJ']
    assert not any(c['id']=='3304557' for c in data['cities']['SP'])

def test_save_normalize_and_reopen_dealer_address(env):
    e=env;d=dealer_payload(e,cnpj='12.abc.345/01de-35',**address())
    e.act(e.admin,'dealer.save',d)
    saved=next(d for d in e.data()['dealers'] if d['id']==e.did)
    assert saved['cnpj']=='12ABC34501DE35'
    assert saved['address_city']=='São Paulo' and saved['address_state']=='SP'
    assert saved['address_cep']=='01310100' and saved['address_number']=='S/N'
    with Session() as db:
        db.expire_all();dealer=db.get(Dealer,e.did)
        assert dealer.address_street=='Avenida de Teste' and dealer.address_complement=='Loja 2'
    # Legacy callers that do not send the new fields must not erase them.
    e.act(e.admin,'dealer.save',dealer_payload(e))
    assert next(d for d in e.data()['dealers'] if d['id']==e.did)['cnpj']=='12ABC34501DE35'

@pytest.mark.parametrize('changes',[
    {'address_state':'XX'}, {'address_city_id':'3304557'}, {'address_city_id':''},
    {'address_cep':'1234'}, {'address_cep':'ABCDE-100'}, {'address_street':''},
    {'address_number':''}, {'address_district':''}, {'address_complement':'X'*201},
])
def test_invalid_address_rolls_back(env,changes):
    e=env
    e.act(e.admin,'dealer.save',dealer_payload(e,**address(**changes)),expected=400)
    assert next(d for d in e.data()['dealers'] if d['id']==e.did)['address_state']==''

def test_cnpj_uniqueness_and_optional_blank_fields(env):
    e=env;e.act(e.admin,'dealer.save',dealer_payload(e,cnpj='11.222.333/0001-81'))
    e.act(e.admin,'dealer.save',dict(id=e.did2,code='D2',name='Dealer 2',sales_id=e.other_sid,cnpj='11222333000181'),expected=409)
    with Session() as db:
        assert db.get(Dealer,e.did2).cnpj is None
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):
            db.get(Dealer,e.did2).cnpj='11222333000181';db.flush()
    e.act(e.admin,'dealer.save',dealer_payload(e,cnpj=''))
    assert next(d for d in e.data()['dealers'] if d['id']==e.did)['cnpj'] is None

def test_dealer_data_scope_and_address_edit_permissions(env):
    e=env;e.act(e.admin,'dealer.save',dealer_payload(e,cnpj='12ABC34501DE35',**address()))
    e.act(e.dealer,'dealer.save',dealer_payload(e,**address()),expected=403)
    e.act(e.sales,'dealer.save',dealer_payload(e,**address()),expected=403)
    own=e.data(e.dealer)['dealers'];assert len(own)==1 and own[0]['address_city']=='São Paulo'
    assert all(d['id']!=e.did for d in e.data(e.dealer2)['dealers'])

def test_address_city_is_derived_from_official_id_and_can_be_cleared(env):
    e=env;e.act(e.admin,'dealer.save',dealer_payload(e,address_city='Forged City',**address()))
    assert next(d for d in e.data()['dealers'] if d['id']==e.did)['address_city']=='São Paulo'
    empty={k:'' for k in address()}
    e.act(e.admin,'dealer.save',dealer_payload(e,**empty))
    assert next(d for d in e.data()['dealers'] if d['id']==e.did)['address_city']==''

def test_migrate_legacy_dealers_without_changing_existing_accounts(env,tmp_path):
    from sqlalchemy.schema import CreateTable
    data=tmp_path/'old-dealers';data.mkdir()
    ddl=str(CreateTable(Dealer.__table__).compile(engine))
    ddl='\n'.join(line for line in ddl.splitlines() if 'cnpj VARCHAR' not in line and 'address_' not in line)
    with sqlite3.connect(data/'hynak.sqlite3') as db:
        db.execute('CREATE TABLE schema_versions (version INTEGER PRIMARY KEY)');db.execute('INSERT INTO schema_versions VALUES (1)')
        db.execute(ddl);db.execute("INSERT INTO dealers(id,code,name,phone,language,sales_id) VALUES(91,'OLD','Original dealer','+5511000000000','pt',NULL)")
    subprocess.run([sys.executable,'-c','from app.db import migrate; migrate(); migrate()'],env={**os.environ,'HYNAK_DATA_DIR':str(data),'PYTHONPATH':str(Path(__file__).resolve().parents[1])},check=True)
    with sqlite3.connect(data/'hynak.sqlite3') as db:
        assert db.execute('SELECT id,code,name,cnpj,address_state,address_city FROM dealers').fetchone()==(91,'OLD','Original dealer',None,'','')
        assert db.execute('SELECT COUNT(*) FROM schema_versions WHERE version=3').fetchone()[0]==1
