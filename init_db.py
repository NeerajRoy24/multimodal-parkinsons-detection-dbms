"""
init_db.py
==========
PostgreSQL Database Bootstrap & Schema Migration Utility.
Connects to the database server, provisions the 'parkinsons' database,
and applies the complete 3NF normalized schema from schema.sql.
"""

import os
import sys
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres@localhost:5432/parkinsons")
ROOT_DSN = os.environ.get("ROOT_DSN", "postgresql://postgres@localhost:5432/postgres")


def init_database(schema_path: str = "schema.sql"):
    print("[1/2] Connecting to PostgreSQL root cluster...")
    try:
        conn = psycopg2.connect(ROOT_DSN)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname = 'parkinsons';")
        if not cur.fetchone():
            cur.execute("CREATE DATABASE parkinsons;")
            print("      Created database 'parkinsons' successfully.")
        else:
            print("      Database 'parkinsons' already exists.")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"      [Warning/Error connecting to root DSN]: {e}")

    print(f"[2/2] Applying schema from '{schema_path}' to 'parkinsons' database...")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    with open(schema_path, "r", encoding="utf-8") as f:
        sql_content = f.read()
    cur.execute(sql_content)
    print("      Schema, indexes, views, and stored procedures applied successfully!")
    cur.close()
    conn.close()


if __name__ == "__main__":
    init_database()
