"""
Counts verification utility for Career Navigator tables.

Usage:
  cd executive-career-pathway-planner-215270/career_navigator_backend
  python -m scripts.query_counts

Connection precedence:
  1) DATABASE_URL environment variable
  2) db_connection.txt at repository root (executive-career-pathway-planner-215270/db_connection.txt)
     or workspace root, or backend folder (fallback)

Outputs:
  - Prints a JSON dict to stdout with table counts
  - Writes the same to logs/counts_report.json
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional, Dict

import psycopg2

HERE = Path(__file__).resolve()
BACKEND_ROOT = HERE.parents[1]
REPO_ROOT = HERE.parents[2]
WORKSPACE_ROOT = HERE.parents[3]
LOG_DIR = BACKEND_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
REPORT_PATH = LOG_DIR / "counts_report.json"

# Also emit a counts report at the workspace-level logs directory
TOP_LOG_DIR = WORKSPACE_ROOT / "logs"
TOP_LOG_DIR.mkdir(parents=True, exist_ok=True)
TOP_REPORT_PATH = TOP_LOG_DIR / "counts_report.json"


def read_first_nonempty_line(p: Path) -> Optional[str]:
    if not p.exists():
        return None
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        return line
    return None


def get_database_url() -> Optional[str]:
    """
    Precedence:
      1) db_connection.txt at repository root
      2) DATABASE_URL environment variable
      3) db_connection.txt at workspace root (fallback)
      4) db_connection.txt inside backend folder (fallback)
    """
    # Prefer repo-root db_connection.txt
    primary_file = REPO_ROOT / "db_connection.txt"
    dsn = read_first_nonempty_line(primary_file)
    if dsn:
        return dsn

    # Then environment
    env = os.getenv("DATABASE_URL")
    if env:
        return env

    # Fallback files
    for candidate in [
        WORKSPACE_ROOT / "db_connection.txt",
        BACKEND_ROOT / "db_connection.txt",
    ]:
        dsn = read_first_nonempty_line(candidate)
        if dsn:
            return dsn
    return None


def connect():
    dsn = get_database_url()
    if not dsn:
        raise RuntimeError("Missing database connection. Set DATABASE_URL or provide db_connection.txt at repo root.")
    return psycopg2.connect(dsn)


POST_SEED_VERIFICATION = [
    "SELECT COUNT(*) AS roles_count FROM roles;",
    "SELECT COUNT(*) AS competencies_count FROM competencies;",
    "SELECT COUNT(*) AS role_competencies_count FROM role_competencies;",
    "SELECT COUNT(*) AS role_adjacency_count FROM role_adjacency;",
    "SELECT COUNT(*) AS role_cards_count FROM role_cards;",
]


# PUBLIC_INTERFACE
def main() -> None:
    """Run COUNT(*) queries for key tables and emit a JSON report."""
    results: Dict[str, int] = {}
    try:
        conn = connect()
        with conn.cursor() as cur:
            for q in POST_SEED_VERIFICATION:
                cur.execute(q)
                row = cur.fetchone()
                colname = cur.description[0].name
                results[colname] = int(row[0])
        conn.commit()
        conn.close()
        text = json.dumps(results, indent=2)
        REPORT_PATH.write_text(text)
        TOP_REPORT_PATH.write_text(text)
        print(text)
    except Exception as e:
        error = {"error": str(e)}
        text = json.dumps(error, indent=2)
        REPORT_PATH.write_text(text)
        TOP_REPORT_PATH.write_text(text)
        print(text, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
