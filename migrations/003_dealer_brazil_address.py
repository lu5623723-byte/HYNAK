"""Preserve existing dealers; add nullable CNPJ and optional Brazilian addresses.

Canonical upgrade implementation lives in app.db.migrate, schema version 3.
Existing dealer IDs, sales ownership, accounts and balances are unchanged.
"""
from app.db import migrate

if __name__=='__main__':
    migrate()
