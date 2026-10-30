"""Add sale date/type, cash payment time and consignment dispatch destination.

Legacy orders remain WHOLESALE; their unknown sale dates are left NULL.
The canonical idempotent upgrade is run by app.db.migrate on startup.
"""
from app.db import migrate

if __name__ == '__main__':
    migrate()
