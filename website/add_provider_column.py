"""
DB migration: add ``provider`` column to subscriptions and search_history tables.

Run once:
    PYTHONPATH=. python website/add_provider_column.py
"""

import os
import sys
from sqlalchemy import create_engine, text, inspect
from dotenv import load_dotenv

load_dotenv()

DATABASE_URI = os.environ.get("DATABASE_URI", "sqlite:///camping.db")
engine = create_engine(DATABASE_URI)

MIGRATIONS = [
    ("subscriptions", "provider", "VARCHAR(30)", "'RecreationGov'"),
    ("search_history", "provider", "VARCHAR(30)", "'RecreationGov'"),
]


def run():
    inspector = inspect(engine)
    with engine.connect() as conn:
        for table, column, col_type, default in MIGRATIONS:
            if table not in inspector.get_table_names():
                print(f"  Table '{table}' does not exist — skipping")
                continue
            existing = [c["name"] for c in inspector.get_columns(table)]
            if column in existing:
                print(f"  {table}.{column} already exists — skipping")
                continue
            sql = f"ALTER TABLE {table} ADD COLUMN {column} {col_type} DEFAULT {default}"
            conn.execute(text(sql))
            conn.commit()
            print(f"  Added {table}.{column}")

    print("Migration complete.")


if __name__ == "__main__":
    run()
