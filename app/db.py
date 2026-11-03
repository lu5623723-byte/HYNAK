import os
from pathlib import Path
from sqlalchemy import create_engine, event, text, inspect
from sqlalchemy.orm import declarative_base, sessionmaker

DATA_DIR = Path(os.environ.get('HYNAK_DATA_DIR', '~/.hynak')).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / 'attachments').mkdir(exist_ok=True)
if os.name == 'posix':
    DATA_DIR.chmod(0o700)
    (DATA_DIR / 'attachments').chmod(0o700)
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///' + str(DATA_DIR / 'hynak.sqlite3'))
engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False, 'timeout': 30} if DATABASE_URL.startswith('sqlite') else {}, pool_pre_ping=True)
if DATABASE_URL.startswith('sqlite'):
    @event.listens_for(engine, 'connect')
    def configure_sqlite(dbapi_connection, _):
        dbapi_connection.execute('PRAGMA foreign_keys=ON')
        dbapi_connection.execute('PRAGMA journal_mode=WAL')
        dbapi_connection.execute('PRAGMA busy_timeout=30000')
Session = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()

def write_lock(db):
    # All writes serialize in a transaction. PostgreSQL lock is transaction-scoped.
    if db.bind.dialect.name == 'sqlite':
        db.execute(text('BEGIN IMMEDIATE'))
    else:
        db.execute(text('SELECT pg_advisory_xact_lock(78761234)'))

def migrate():
    from . import models  # noqa
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE IF NOT EXISTS schema_versions (version INTEGER PRIMARY KEY)'))
        if not conn.execute(text('SELECT version FROM schema_versions WHERE version=1')).first():
            conn.execute(text('INSERT INTO schema_versions(version) VALUES (1)'))
        if not conn.execute(text('SELECT version FROM schema_versions WHERE version=2')).first():
            additions = {
                'orders': {
                    'sale_date': 'VARCHAR(10)',
                    'sale_type': "VARCHAR(20) NOT NULL DEFAULT 'WHOLESALE'",
                    'payment_at': 'VARCHAR(40)',
                },
                'payment_claims': {'paid_at': 'VARCHAR(40)'},
                'shipments': {'consignment_location_id': 'INTEGER REFERENCES warehouses(id)'},
            }
            for table, columns in additions.items():
                existing = {c['name'] for c in inspect(conn).get_columns(table)}
                for column, ddl in columns.items():
                    if column not in existing:
                        conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}'))
            # The legacy CONSIGNMENT mode already identifies a confirmed sold report.
            conn.execute(text("UPDATE orders SET sale_type='CONSIGNMENT' WHERE mode='CONSIGNMENT' AND sale_type='WHOLESALE'"))
            conn.execute(text('INSERT INTO schema_versions(version) VALUES (2)'))
        if not conn.execute(text('SELECT version FROM schema_versions WHERE version=3')).first():
            existing={c['name'] for c in inspect(conn).get_columns('dealers')}
            columns={'cnpj':'VARCHAR(14)', 'address_state':"VARCHAR(2) NOT NULL DEFAULT ''",
                     'address_city_id':"VARCHAR(7) NOT NULL DEFAULT ''", 'address_city':"VARCHAR(160) NOT NULL DEFAULT ''",
                     'address_street':"VARCHAR(200) NOT NULL DEFAULT ''", 'address_number':"VARCHAR(30) NOT NULL DEFAULT ''",
                     'address_district':"VARCHAR(150) NOT NULL DEFAULT ''", 'address_complement':"VARCHAR(200) NOT NULL DEFAULT ''",
                     'address_cep':"VARCHAR(8) NOT NULL DEFAULT ''"}
            for column,ddl in columns.items():
                if column not in existing:conn.execute(text(f'ALTER TABLE dealers ADD COLUMN {column} {ddl}'))
            conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_dealers_cnpj ON dealers(cnpj)'))
            conn.execute(text('INSERT INTO schema_versions(version) VALUES (3)'))
        if not conn.execute(text('SELECT version FROM schema_versions WHERE version=4')).first():
            existing={c['name'] for c in inspect(conn).get_columns('orders')}
            if 'admin_review_pending' not in existing:
                conn.execute(text('ALTER TABLE orders ADD COLUMN admin_review_pending BOOLEAN NOT NULL DEFAULT false'))
            conn.execute(text('INSERT INTO schema_versions(version) VALUES (4)'))
        if not conn.execute(text('SELECT version FROM schema_versions WHERE version=5')).first():
            existing={c['name'] for c in inspect(conn).get_columns('orders')}
            for column,ddl in {'policy_id':'INTEGER REFERENCES sales_policies(id)','policy_name':'VARCHAR(200)'}.items():
                if column not in existing:conn.execute(text(f'ALTER TABLE orders ADD COLUMN {column} {ddl}'))
            conn.execute(text('INSERT INTO schema_versions(version) VALUES (5)'))
        if not conn.execute(text('SELECT version FROM schema_versions WHERE version=6')).first():
            existing={c['name'] for c in inspect(conn).get_columns('warehouses')}
            for column,ddl in {'location':"VARCHAR(200) NOT NULL DEFAULT ''",'address':"TEXT NOT NULL DEFAULT ''",'contact':"VARCHAR(120) NOT NULL DEFAULT ''",'phone':"VARCHAR(30) NOT NULL DEFAULT ''"}.items():
                if column not in existing:conn.execute(text(f'ALTER TABLE warehouses ADD COLUMN {column} {ddl}'))
            conn.execute(text('INSERT INTO schema_versions(version) VALUES (6)'))
        if not conn.execute(text('SELECT version FROM schema_versions WHERE version=7')).first():
            existing={c['name'] for c in inspect(conn).get_columns('order_lines')}
            if 'demo_applied' not in existing:
                conn.execute(text('ALTER TABLE order_lines ADD COLUMN demo_applied BOOLEAN NOT NULL DEFAULT false'))
            # Preserve prior Demo usage without changing historical prices.
            conn.execute(text("UPDATE order_lines SET demo_applied=true WHERE gift=false AND order_id IN (SELECT id FROM orders WHERE promotion='DEMO')"))
            conn.execute(text('INSERT INTO schema_versions(version) VALUES (7)'))
        if not conn.execute(text('SELECT version FROM schema_versions WHERE version=8')).first():
            existing={c['name'] for c in inspect(conn).get_columns('consignment_records')}
            if 'source_order_id' not in existing:
                conn.execute(text('ALTER TABLE consignment_records ADD COLUMN source_order_id INTEGER REFERENCES orders(id)'))
            # Historical sold-report settlements are retained, never charged again.
            conn.execute(text('INSERT INTO schema_versions(version) VALUES (8)'))
        if not conn.execute(text('SELECT version FROM schema_versions WHERE version=9')).first():
            additions={'dealers':{'auto_apply_credit':'BOOLEAN NOT NULL DEFAULT false'},
                       'orders':{'payment_method':"VARCHAR(20) NOT NULL DEFAULT 'BANK'"},
                       'bank_receipts':{'recharge_id':'INTEGER REFERENCES dealer_recharges(id)', 'is_recharge':'BOOLEAN NOT NULL DEFAULT false'}}
            for table,columns in additions.items():
                existing={c['name'] for c in inspect(conn).get_columns(table)}
                for column,ddl in columns.items():
                    if column not in existing:conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}'))
            conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_receipt_recharge ON bank_receipts(recharge_id)'))
            conn.execute(text('INSERT INTO schema_versions(version) VALUES (9)'))
        if not conn.execute(text('SELECT version FROM schema_versions WHERE version=10')).first():
            # PolicyDraft is created above by metadata.create_all; no existing ledger is modified.
            conn.execute(text('INSERT INTO schema_versions(version) VALUES (10)'))
        if engine.dialect.name == 'sqlite':
            for table in ['stock_ledger', 'audit_logs', 'bank_receipts', 'payment_allocations', 'adjustments', 'opening_credits', 'commission_adjustments']:
                for action in ['UPDATE', 'DELETE']:
                    conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'append-only ledger'); END"))
        else:
            conn.execute(text("CREATE OR REPLACE FUNCTION hynak_immutable() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'append-only ledger'; END; $$ LANGUAGE plpgsql"))
            for table in ['stock_ledger', 'audit_logs', 'bank_receipts', 'payment_allocations', 'adjustments', 'opening_credits', 'commission_adjustments']:
                conn.execute(text(f'DROP TRIGGER IF EXISTS immutable_{table} ON {table}'))
                conn.execute(text(f'CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION hynak_immutable()'))
