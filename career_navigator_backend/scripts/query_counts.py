"""
Counts verification utility for Career Navigator tables.

Usage:
  cd executive-career-pathway-planner-215270/career_navigator_backend
  python -m scripts.query_counts [--dsn "postgresql://..."]

Connection precedence when --dsn not provided:
  1) DATABASE_URL environment variable (after loading .env)
  2) db_connection.txt at repository root (executive-career-pathway-planner-215270/db_connection.txt)
     or workspace root, or backend folder (fallback)

Outputs:
  - Prints a JSON dict to stdout with table counts
  - Writes the same to:
      - executive-career-pathway-planner-215270/career_navigator_backend/logs/counts_report.json
      - executive-career-pathway-planner-215270/logs/counts_report.json
      - logs/counts_report.json at workspace root
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path
from typing import Optional, Dict
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

import psycopg2
from dotenv import load_dotenv

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

# Also write a counts report at the repository-level logs directory
REPO_LOG_DIR = REPO_ROOT / "logs"
REPO_LOG_DIR.mkdir(parents=True, exist_ok=True)
REPO_REPORT_PATH = REPO_LOG_DIR / "counts_report.json"


def read_first_nonempty_line(p: Path) -> Optional[str]:
    if not p.exists():
        return None
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        return line
    return None


def _load_env_files() -> None:
    """Load .env files from common locations."""
    try:
        # inherit defaults
        load_dotenv(override=False)
        # explicit locations
        for candidate in [REPO_ROOT / ".env", WORKSPACE_ROOT / ".env", BACKEND_ROOT / ".env"]:
            load_dotenv(dotenv_path=candidate, override=False)
    except Exception:
        # swallow malformed .env for robustness
        pass


def get_database_url() -> Optional[str]:
    """
    Precedence:
      1) DATABASE_URL environment variable (after loading .env)
      2) db_connection.txt at repository root
      3) db_connection.txt at workspace root (fallback)
      4) db_connection.txt inside backend folder (fallback)
    """
    _load_env_files()

    # Prefer environment
    env = os.getenv("DATABASE_URL")
    if env:
        return env

    # Then repo-root db_connection.txt
    primary_file = REPO_ROOT / "db_connection.txt"
    dsn = read_first_nonempty_line(primary_file)
    if dsn:
        return dsn

    # Fallback files
    for candidate in [
        WORKSPACE_ROOT / "db_connection.txt",
        BACKEND_ROOT / "db_connection.txt",
    ]:
        dsn = read_first_nonempty_line(candidate)
        if dsn:
            return dsn
    return None


def _is_supabase_host(host: Optional[str]) -> bool:
    if not host:
        return False
    h = str(host).lower()
    return h.endswith(".supabase.co") or ".supabase.co" in h


def _ensure_sslmode_required(dsn: str) -> str:
    """Ensure sslmode=require for Supabase hosts if not specified."""
    try:
        parsed = urlparse(dsn)
        host = parsed.hostname or ""
        q = dict(parse_qsl(parsed.query))
        if _is_supabase_host(host) and "sslmode" not in q:
            q["sslmode"] = "require"
            return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(q), parsed.fragment))
        return dsn
    except Exception:
        low = dsn.lower()
        if "sslmode=" not in low:
            dsn += " sslmode=require"
        return dsn


def connect(dsn_override: Optional[str] = None):
    """
    Create and return a psycopg2 connection using the resolved DSN.

    Precedence:
      - dsn_override argument
      - DATABASE_URL/.env, then db_connection.txt lookups

    For Supabase hosts, forces sslmode=require if not present.

    Enhancements:
    - Supports SUPABASE_DB_HOSTADDR/PGHOSTADDR/DB_HOSTADDR/POSTGRES_HOSTADDR env vars
      to bypass DNS and force IPv4 while keeping TLS hostname verification.
    - Attempts IPv4 fallback if default connect fails (avoiding IPv6 issues).
    """
    dsn = dsn_override or get_database_url()
    if not dsn:
        raise RuntimeError("Missing database connection. Provide --dsn or set DATABASE_URL or db_connection.txt at repo root.")
    dsn = _ensure_sslmode_required(dsn)

    # Prefer explicit hostaddr override if provided
    hostaddr_env = (
        os.environ.get("SUPABASE_DB_HOSTADDR")
        or os.environ.get("PGHOSTADDR")
        or os.environ.get("DB_HOSTADDR")
        or os.environ.get("POSTGRES_HOSTADDR")
    )
    if hostaddr_env:
        try:
            parsed = urlparse(dsn)
            host = parsed.hostname
            port = parsed.port or 5432
            dbname = (parsed.path[1:] if parsed.path.startswith("/") else parsed.path) or None
            q = dict(parse_qsl(parsed.query))
            params = {
                "host": host,               # keep hostname for TLS SNI/verification
                "hostaddr": hostaddr_env,   # direct IPv4 to bypass DNS
                "port": port,
                "dbname": dbname,
                "user": parsed.username,
                "password": parsed.password,
                "sslmode": q.get("sslmode", "require"),
            }
            return psycopg2.connect(**params)
        except Exception:
            # continue with normal attempts
            pass

    # First attempt: normal connection string
    try:
        return psycopg2.connect(dsn)
    except Exception:
        # IPv4 fallback via hostaddr while preserving TLS hostname verification
        try:
            parsed = urlparse(dsn)
            host = parsed.hostname
            port = parsed.port or 5432
            addrs = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
            if not addrs:
                raise RuntimeError("DNS resolution returned no IPv4 addresses.")
            ipv4 = addrs[0][4][0]
            dbname = (parsed.path[1:] if parsed.path.startswith("/") else parsed.path) or None
            q = dict(parse_qsl(parsed.query))
            params = {
                "host": host,
                "hostaddr": ipv4,
                "port": port,
                "dbname": dbname,
                "user": parsed.username,
                "password": parsed.password,
                "sslmode": q.get("sslmode", "require"),
            }
            return psycopg2.connect(**params)
        except Exception as e:
            # Re-raise last error for visibility
            raise e


POST_SEED_VERIFICATION = [
    "SELECT COUNT(*) AS roles_count FROM roles;",
    "SELECT COUNT(*) AS competencies_count FROM competencies;",
    "SELECT COUNT(*) AS role_competencies_count FROM role_competencies;",
    "SELECT COUNT(*) AS role_adjacency_count FROM role_adjacency;",
    "SELECT COUNT(*) AS role_cards_count FROM role_cards;",
]


# PUBLIC_INTERFACE
def main() -> None:
    """Run COUNT(*) queries for key tables and emit a JSON report.

    Arguments (CLI):
      --dsn: Optional DSN override "postgresql://user:pass@host:5432/db?sslmode=require"
    """
    parser = argparse.ArgumentParser(description="Run COUNT(*) verification on Career Navigator core tables.")
    parser.add_argument("--dsn", dest="dsn", default=None, help="Optional DSN override (postgresql://...)")
    args = parser.parse_args()

    results: Dict[str, int] = {}
    try:
        conn = connect(dsn_override=args.dsn)
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
        REPO_REPORT_PATH.write_text(text)
        print(text)
    except Exception as e:
        error = {"error": str(e)}
        text = json.dumps(error, indent=2)
        REPORT_PATH.write_text(text)
        TOP_REPORT_PATH.write_text(text)
        REPO_REPORT_PATH.write_text(text)
        print(text, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
