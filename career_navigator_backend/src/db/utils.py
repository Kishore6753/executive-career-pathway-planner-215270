import os
import re
import json
from typing import Dict, Optional

import psycopg2
from psycopg2.extras import execute_batch


# PUBLIC_INTERFACE
def get_database_url() -> Optional[str]:
    """
    Return a Postgres DSN using the same precedence as direct seeding scripts:
      1) db_connection.txt at repository root (executive-career-pathway-planner-215270/db_connection.txt)
      2) DATABASE_URL environment variable
      3) db_connection.txt at workspace root (fallback)
      4) db_connection.txt inside backend folder (fallback)
    The first non-empty, non-comment line is used when reading db_connection.txt.
    """
    from pathlib import Path

    def _read_first_nonempty_line(p: Path) -> Optional[str]:
        if not p.exists():
            return None
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            return s
        return None

    here = Path(__file__).resolve()
    backend_root = here.parents[2]          # .../career_navigator_backend
    repo_root = here.parents[3]             # .../executive-career-pathway-planner-215270
    workspace_root = here.parents[4]        # .../code-generation

    # Prefer repo-root db_connection.txt
    primary_file = repo_root / "db_connection.txt"
    dsn = _read_first_nonempty_line(primary_file)
    if dsn:
        return dsn

    # Then environment variable
    env_dsn = os.getenv("DATABASE_URL")
    if env_dsn:
        return env_dsn

    # Fallback files
    for candidate in [
        workspace_root / "db_connection.txt",
        backend_root / "db_connection.txt",
    ]:
        dsn = _read_first_nonempty_line(candidate)
        if dsn:
            return dsn

    return None


# PUBLIC_INTERFACE
def get_conn():
    """
    Create and return a psycopg2 connection using the resolved DSN.

    Raises:
        RuntimeError: if no connection info is found via db_connection.txt or DATABASE_URL.
    """
    url = get_database_url()
    if not url:
        raise RuntimeError(
            "Missing database connection. Set DATABASE_URL or provide db_connection.txt at repo root."
        )
    return psycopg2.connect(url)


SLUG_RE = re.compile(r"[^a-z0-9]+")


# PUBLIC_INTERFACE
def slugify(value: str) -> str:
    """Create a lowercase slug from a role or competency name."""
    s = value.strip().lower()
    s = s.replace("&", "and").replace("+", "plus")
    s = SLUG_RE.sub("-", s)
    s = s.strip("-")
    return s


def normalize_role_slug(name: str) -> str:
    """Normalize well-known role codes and general names to slugs."""
    name = name.strip()
    special = {
        "CA": "ca",
        "CTO": "cto",
        "CIO": "cio",
        "CDAO": "cdao",
        "CInO": "cino",
        "CPTO": "cpto",
        "CTrO": "ctro",
        "CCTO": "ccto",
        "CDO": "cdo",
        "CAIO": "caio",
        "FCTO": "fcto",
        "Infra": "infra",
        "Ops": "ops",
        "PMO": "pmo",
        "AppDev": "appdev",
        "DigProd": "digprod",
    }
    if name in special:
        return special[name]
    return slugify(name)


def upsert_roles(conn, roles: Dict[str, Dict[str, str]]) -> int:
    """Upsert roles by id=slug and name, description."""
    rows = [(rid, data.get("name"), data.get("description")) for rid, data in roles.items()]
    with conn.cursor() as cur:
        execute_batch(
            cur,
            """
            insert into roles (id, name, description)
            values (%s, %s, %s)
            on conflict (id) do update set
                name = excluded.name,
                description = excluded.description,
                updated_at = now()
            """,
            rows,
            page_size=100,
        )
    conn.commit()
    return len(rows)


def upsert_competencies(conn, competencies: Dict[str, Dict[str, str]]) -> int:
    """Upsert competencies by id=slug."""
    rows = [(cid, data.get("name"), data.get("description"), data.get("category")) for cid, data in competencies.items()]
    with conn.cursor() as cur:
        execute_batch(
            cur,
            """
            insert into competencies (id, name, description, category)
            values (%s, %s, %s, %s)
            on conflict (id) do update set
                name = excluded.name,
                description = excluded.description,
                category = excluded.category,
                updated_at = now()
            """,
            rows,
            page_size=100,
        )
    conn.commit()
    return len(rows)


def upsert_role_competencies(conn, mappings):
    """Upsert role_competencies records."""
    rows = [(m["role_id"], m["competency_id"], m["required_level"], m.get("notes")) for m in mappings]
    with conn.cursor() as cur:
        execute_batch(
            cur,
            """
            insert into role_competencies (role_id, competency_id, required_level, notes)
            values (%s, %s, %s, %s)
            on conflict (role_id, competency_id) do update set
                required_level = excluded.required_level,
                notes = excluded.notes,
                updated_at = now()
            """,
            rows,
            page_size=200,
        )
    conn.commit()
    return len(rows)


def upsert_role_adjacency(conn, records):
    """Upsert role_adjacency records with computed JSONB gap list."""
    rows = [(r["source_role_id"], r["target_role_id"], json.dumps(r.get("gap", [])), r.get("weight")) for r in records]
    with conn.cursor() as cur:
        execute_batch(
            cur,
            """
            insert into role_adjacency (source_role_id, target_role_id, gap, weight)
            values (%s, %s, %s::jsonb, %s)
            on conflict (source_role_id, target_role_id) do update set
                gap = excluded.gap,
                weight = excluded.weight,
                updated_at = now()
            """,
            rows,
            page_size=100,
        )
    conn.commit()
    return len(rows)


def upsert_role_cards(conn, records):
    """Upsert role_cards text content."""
    rows = [(r["role_id"], r.get("source_doc"), r["content"]) for r in records]
    with conn.cursor() as cur:
        execute_batch(
            cur,
            """
            insert into role_cards (role_id, source_doc, content)
            values (%s, %s, %s)
            on conflict (role_id, source_doc) do update set
                content = excluded.content,
                updated_at = now()
            """,
            rows,
            page_size=50,
        )
    conn.commit()
    return len(rows)
