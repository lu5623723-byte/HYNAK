"""Initial versioned migration. Run from project root: python manage.py migrate.

Metadata is the canonical SQLite/PostgreSQL DDL. Never use create_all to alter
existing columns in future versions; add an explicit numbered migration instead.
"""
from app.db import migrate
if __name__=='__main__':migrate()
