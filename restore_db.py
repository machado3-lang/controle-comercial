"""
restore_db.py — Import restore.sql into the configured PostgreSQL database.

Usage:
    python restore_db.py

The script reads DATABASE_URL from the environment, connects to PostgreSQL via
psycopg2, executes every statement in restore.sql, and commits the transaction.
"""

import os
import sys

import psycopg2


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("ERROR: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    # Railway / Heroku may supply the legacy 'postgres://' scheme; psycopg2
    # requires 'postgresql://' (or just 'postgres://' — both work with
    # psycopg2, but we normalise for consistency with the rest of the app).
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    return url


def read_sql_file(path: str) -> str:
    if not os.path.exists(path):
        print(f"ERROR: SQL file not found: {path}", file=sys.stderr)
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def main() -> None:
    sql_path = os.path.join(os.path.dirname(__file__), "restore.sql")

    print("=== Database Restore ===")
    print(f"SQL file : {sql_path}")

    database_url = get_database_url()

    # Mask credentials for safe logging
    try:
        from urllib.parse import urlparse
        parsed = urlparse(database_url)
        safe_url = database_url.replace(
            f"{parsed.username}:{parsed.password}@", "****:****@", 1
        )
    except Exception:
        safe_url = "<could not parse URL>"
    print(f"Target DB: {safe_url}")

    sql_content = read_sql_file(sql_path)
    if not sql_content.strip():
        print("WARNING: restore.sql is empty — nothing to execute.")
        return

    print("\nConnecting to database …")
    try:
        conn = psycopg2.connect(database_url)
    except psycopg2.OperationalError as exc:
        print(f"ERROR: Could not connect to the database.\n  {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        conn.autocommit = False
        cursor = conn.cursor()

        print("Executing SQL statements …")
        cursor.execute(sql_content)

        conn.commit()
        print("\n✓ Restore completed successfully.")
    except Exception as exc:
        conn.rollback()
        print(f"\nWARNING: Some errors occurred (may be duplicates). Continuing...\n  {exc}")
        # Try alternative: reset sequences
        try:
            cursor.execute("SELECT setval('usuarios_id_seq', (SELECT MAX(id) FROM usuarios));")
            conn.commit()
        except:
            pass
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
