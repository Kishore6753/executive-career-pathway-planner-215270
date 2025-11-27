from __future__ import annotations

"""
DB connection verification utility with IPv4 fallback and hostaddr override.

- Loads .env automatically (repo root, workspace root, backend root).
- Prefers --dsn argument, then DATABASE_URL; else builds DSN from common env var names (case-insensitive).
- Enforces sslmode=require when connecting to Supabase hosts if not explicitly provided.
- Supports hostaddr override via SUPABASE_DB_HOSTADDR/PGHOSTADDR/DB_HOSTADDR/POSTGRES_HOSTADDR to bypass DNS and force IPv4 while keeping TLS hostname verification.
- Falls back to IPv4 resolution if default connect fails (avoiding IPv6-only and DNS issues).
- Prints concise JSON diagnostics on success/failure, including host, port, db, user, sslmode, and password hints.

Usage:
  cd executive-career-pathway-planner-215270/career_navigator_backend
  python -m scripts.db_connect_check [--dsn "postgresql://..."]
"""

import argparse
import json
import os
import sys
import socket
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse, quote_plus

import psycopg2
from dotenv import load_dotenv


HERE = Path(__file__).resolve()
BACKEND_ROOT = HERE.parents[1]
REPO_ROOT = HERE.parents[2]
WORKSPACE_ROOT = HERE.parents[3]


def _load_env_files() -> None:
    """Load .env from common locations, without overriding existing env."""
    # default search (walks up from CWD)
    load_dotenv(override=False)
    # explicit paths
    for candidate in [
        REPO_ROOT / ".env",
        WORKSPACE_ROOT / ".env",
        BACKEND_ROOT / ".env",
    ]:
        try:
            load_dotenv(dotenv_path=candidate, override=False)
        except Exception:
            # ignore malformed .env files silently for robustness
            pass


def _env_map() -> Dict[str, str]:
    """Return a case-insensitive view of environment variables."""
    return {k.upper(): v for k, v in os.environ.items() if isinstance(v, str)}


def _build_dsn_from_env(env: Dict[str, str]) -> Tuple[Optional[str], Dict[str, Optional[str]], list]:
    """
    Build a DSN from individual env vars if DATABASE_URL not set.

    Recognized synonyms (case-insensitive):
      - host: DB_HOST, PGHOST, POSTGRES_HOST
      - port: DB_PORT, PGPORT, POSTGRES_PORT (default 5432)
      - dbname: DB_NAME, DBNAME, PGDATABASE, POSTGRES_DB
      - user: DB_USER, PGUSER, POSTGRES_USER, USER, USERNAME
      - password: DB_PASSWORD, PGPASSWORD, POSTGRES_PASSWORD, PASSWORD
      - sslmode: SSLMODE, SSL_MODE, PGSSLMODE, POSTGRES_SSLMODE

    Returns (dsn, dsn_info, missing_vars)
    """
    def pick(*names: str) -> Optional[str]:
        for n in names:
            if n in env and str(env[n]).strip():
                return str(env[n]).strip()
        return None

    host = pick("DB_HOST", "PGHOST", "POSTGRES_HOST")
    port = pick("DB_PORT", "PGPORT", "POSTGRES_PORT") or "5432"
    dbname = pick("DB_NAME", "DBNAME", "PGDATABASE", "POSTGRES_DB")
    user = pick("DB_USER", "PGUSER", "POSTGRES_USER", "USER", "USERNAME")
    password = pick("DB_PASSWORD", "PGPASSWORD", "POSTGRES_PASSWORD", "PASSWORD")
    sslmode = pick("SSLMODE", "SSL_MODE", "PGSSLMODE", "POSTGRES_SSLMODE")

    missing = []
    for key, val in [("DB_HOST/PGHOST/POSTGRES_HOST", host),
                     ("DB_NAME/DBNAME/PGDATABASE/POSTGRES_DB", dbname),
                     ("DB_USER/PGUSER/POSTGRES_USER", user),
                     ("DB_PASSWORD/PGPASSWORD/POSTGRES_PASSWORD", password)]:
        if not val:
            missing.append(key)

    dsn_info = {
        "host": host,
        "port": port,
        "dbname": dbname,
        "user": user,
        "sslmode": sslmode,
    }

    if missing:
        return None, dsn_info, missing

    # URL-encode password for safety (e.g., '@' -> %40)
    pw_enc = quote_plus(password) if password is not None else ""
    dsn = f"postgresql://{user}:{pw_enc}@{host}:{port}/{dbname}"
    if sslmode:
        # append sslmode only if provided
        sep = "&" if "?" in dsn else "?"
        dsn = f"{dsn}{sep}sslmode={sslmode}"

    return dsn, dsn_info, []


def _is_supabase_host(hostname: Optional[str]) -> bool:
    if not hostname:
        return False
    h = hostname.lower()
    return h.endswith(".supabase.co") or ".supabase.co" in h


def _ensure_sslmode_required(dsn: str) -> str:
    """If DSN points to a Supabase host and sslmode not present, append sslmode=require."""
    if "://" in dsn:
        parsed = urlparse(dsn)
        hostname = parsed.hostname or ""
        query = dict(parse_qsl(parsed.query))
        if _is_supabase_host(hostname) and "sslmode" not in query:
            query["sslmode"] = "require"
            new_qs = urlencode(query)
            dsn = urlunparse((
                parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_qs, parsed.fragment
            ))
        return dsn
    else:
        # keyword DSN format: host=... user=... etc.
        lower = dsn.lower()
        # naive parse
        kv = {part.split("=", 1)[0].strip().lower(): part.split("=", 1)[1].strip()
              for part in dsn.split() if "=" in part}
        hostname = kv.get("host") or kv.get("hostname") or ""
        if _is_supabase_host(hostname) and "sslmode=" not in lower:
            dsn += " sslmode=require"
        return dsn


def _parse_dsn_info(dsn: str) -> Dict[str, Optional[str]]:
    """Best-effort parse DSN info for diagnostics."""
    info = {"host": None, "port": None, "dbname": None, "user": None, "sslmode": None}
    if "://" in dsn:
        p = urlparse(dsn)
        info["host"] = p.hostname
        info["port"] = str(p.port) if p.port else None
        info["dbname"] = (p.path[1:] if p.path.startswith("/") else p.path) or None
        info["user"] = p.username
        q = dict(parse_qsl(p.query))
        info["sslmode"] = q.get("sslmode")
    else:
        # keyword format
        try:
            kv = {part.split("=", 1)[0].strip().lower(): part.split("=", 1)[1].strip()
                  for part in dsn.split() if "=" in part}
            info["host"] = kv.get("host") or kv.get("hostname")
            info["port"] = kv.get("port")
            info["dbname"] = kv.get("dbname") or kv.get("database")
            info["user"] = kv.get("user") or kv.get("username")
            info["sslmode"] = kv.get("sslmode")
        except Exception:
            pass
    return info


def _detect_unescaped_at_in_password(dsn: str) -> bool:
    """
    Heuristic: if URL DSN contains more than one '@' after the scheme,
    it's likely due to an unescaped '@' in the password.
    """
    if "://" not in dsn:
        return False
    try:
        after_scheme = dsn.split("://", 1)[1]
        # part before the first '/' is netloc
        netloc = after_scheme.split("/", 1)[0]
        return netloc.count("@") > 1
    except Exception:
        return False


def resolve_dsn(cli_dsn: Optional[str]) -> Tuple[Optional[str], Dict[str, Optional[str]], str, list]:
    """
    Resolve DSN with precedence:
      1) cli_dsn (if provided)
      2) DATABASE_URL
      3) build from env vars (normalized)
      4) db_connection.txt at repo root (fallback), then workspace root, then backend root

    Returns (dsn, dsn_info, source, missing_env)
    """
    _load_env_files()
    env = _env_map()

    if cli_dsn:
        dsn = _ensure_sslmode_required(cli_dsn.strip())
        return dsn, _parse_dsn_info(dsn), "argv", []

    # Prefer DATABASE_URL
    db_url = env.get("DATABASE_URL")
    if db_url and str(db_url).strip():
        dsn = _ensure_sslmode_required(db_url.strip())
        return dsn, _parse_dsn_info(dsn), "DATABASE_URL", []

    # Build from individual envs
    built_dsn, dsn_info, missing = _build_dsn_from_env(env)
    if built_dsn:
        dsn = _ensure_sslmode_required(built_dsn)
        return dsn, _parse_dsn_info(dsn), "env_parts", []

    # Fallback files
    for candidate in [REPO_ROOT / "db_connection.txt", WORKSPACE_ROOT / "db_connection.txt", BACKEND_ROOT / "db_connection.txt"]:
        if candidate.exists():
            # use first non-empty, non-comment line
            for line in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                dsn = _ensure_sslmode_required(s)
                return dsn, _parse_dsn_info(dsn), f"file:{candidate.name}", []
    return None, dsn_info, "missing", []


# PUBLIC_INTERFACE
def main() -> None:
    """
    Try connecting to the database and print concise JSON diagnostics.

    Arguments:
      --dsn: Optional DSN override (takes precedence over env/file)
    """
    parser = argparse.ArgumentParser(description="Verify database connectivity with concise diagnostics.")
    parser.add_argument("--dsn", dest="dsn", default=None, help="Optional DSN override (postgresql://...)")
    args = parser.parse_args()

    dsn, info, source, missing = resolve_dsn(args.dsn)

    if not dsn:
        out = {
            "status": "error",
            "error": "No database connection info found. Provide --dsn or set DATABASE_URL or individual env vars, or db_connection.txt.",
            "dsn_source": source,
            "env_missing": missing,
            "diagnostics": info,
        }
        print(json.dumps(out, indent=2), file=sys.stderr)
        sys.exit(2)

    # Enforce sslmode=require for Supabase hostnames
    dsn = _ensure_sslmode_required(dsn)

    unescaped_at = _detect_unescaped_at_in_password(dsn)
    try:
        # Prefer explicit hostaddr override if provided (bypass DNS, keep TLS hostname verification)
        hostaddr_env = (
            os.environ.get("SUPABASE_DB_HOSTADDR")
            or os.environ.get("PGHOSTADDR")
            or os.environ.get("DB_HOSTADDR")
            or os.environ.get("POSTGRES_HOSTADDR")
        )
        if hostaddr_env and "://" in dsn:
            p = urlparse(dsn)
            host = p.hostname
            port = p.port or 5432
            dbname = (p.path[1:] if p.path.startswith("/") else p.path) or None
            q = dict(parse_qsl(p.query))
            params = {
                "host": host,              # keep hostname for TLS SNI/verification
                "hostaddr": hostaddr_env,  # direct IPv4 to bypass DNS/IPv6
                "port": port,
                "dbname": dbname,
                "user": p.username,
                "password": p.password,
                "sslmode": q.get("sslmode", "require"),
            }
            conn = psycopg2.connect(**params)
        else:
            try:
                # Default connection attempt (may try IPv6 first)
                conn = psycopg2.connect(dsn)
            except Exception:
                # IPv4 fallback: resolve hostname to IPv4 and connect via hostaddr
                if "://" not in dsn:
                    raise  # cannot safely parse keyword DSN for fallback
                p = urlparse(dsn)
                host = p.hostname
                port = p.port or 5432
                addrs = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
                if not addrs:
                    raise
                ipv4 = addrs[0][4][0]
                dbname = (p.path[1:] if p.path.startswith("/") else p.path) or None
                q = dict(parse_qsl(p.query))
                params = {
                    "host": host,          # keep hostname for TLS SNI
                    "hostaddr": ipv4,      # direct IPv4 connect
                    "port": port,
                    "dbname": dbname,
                    "user": p.username,
                    "password": p.password,
                    "sslmode": q.get("sslmode", "require"),
                }
                conn = psycopg2.connect(**params)

        with conn.cursor() as cur:
            cur.execute("SELECT version(), current_setting('server_version_num'), current_database(), NOW();")
            version, server_version_num, current_db, now_ts = cur.fetchone()
        conn.close()
        out = {
            "status": "ok",
            "dsn_source": source,
            "server_version": str(version),
            "server_version_num": str(server_version_num),
            "now": str(now_ts),
            "dsn_info": info,
            "ssl_note": "sslmode=require enforced" if (info.get("sslmode") == "require" or _is_supabase_host(info.get("host"))) else None,
        }
        print(json.dumps(out, indent=2))
    except Exception as e:
        hint = None
        if "No address associated with hostname" in str(e) or "Name or service not known" in str(e):
            hint = "DNS/IPv6 issue suspected. Set SUPABASE_DB_HOSTADDR to the IPv4 address of the DB host and retry."
        out = {
            "status": "error",
            "error": str(e),
            "dsn_source": source,
            "diagnostics": {
                **info,
                "password_hint": "Password contains '@' that may be unescaped. Encode as %40." if unescaped_at else None,
                "hostaddr_override_hint": hint,
            },
            "env_missing": missing or None,
        }
        print(json.dumps(out, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
