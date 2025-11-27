"""
Direct SQL Schema Creation and Data Seeding for Career Navigator.

This script:
- Uses DATABASE_URL or db_connection.txt to connect to Postgres/Supabase.
- Applies the provided DDL statements one-by-one (ensuring pgcrypto extension first).
- Parses the Excel/text attachments to produce UPSERTs aligned to the requested schema:
  - roles
  - competencies
  - role_competencies
  - role_adjacency
  - role_cards
- Runs verification COUNT(*) queries and writes a concise report to logs/direct_seed_report.json.

Usage:
  cd executive-career-pathway-planner-215270/career_navigator_backend
  python -m scripts.run_direct_sql_seed

Connection precedence:
  1) DATABASE_URL environment variable
  2) db_connection.txt in the repository root (first non-empty line as DSN)

Note:
- This script performs direct SQL and does not rely on Alembic or the ORM.
- It aligns with the schema requested in the task (slightly different from the Alembic-managed schema).
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import psycopg2
from psycopg2.extras import execute_batch


# ---------- Paths and constants ----------
HERE = Path(__file__).resolve()
BACKEND_ROOT = HERE.parents[1]
# Repository root (e.g., executive-career-pathway-planner-215270)
REPO_ROOT = HERE.parents[2]
# Workspace root (top-level code-generation workspace)
WORKSPACE_ROOT = HERE.parents[3]
ATTACH_DIR = WORKSPACE_ROOT / "attachments"
LOG_DIR = BACKEND_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
REPORT_PATH = LOG_DIR / "direct_seed_report.json"

# Top-level logs path for counts report
TOP_LOG_DIR = WORKSPACE_ROOT / "logs"
TOP_LOG_DIR.mkdir(parents=True, exist_ok=True)
COUNTS_REPORT_TOP = TOP_LOG_DIR / "counts_report.json"
COUNTS_REPORT_BACKEND = LOG_DIR / "counts_report.json"

# Provided by request details (reordered to ensure pgcrypto is created first)
DDL_STATEMENTS: List[str] = [
    # Extensions needed for UUID gen (Supabase usually has it)
    "CREATE EXTENSION IF NOT EXISTS pgcrypto;",
    # SCHEMA: roles
    """
    CREATE TABLE IF NOT EXISTS roles (
        id TEXT PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        description TEXT,
        family TEXT,
        seniority TEXT,
        mission TEXT,
        scope JSONB,
        key_decisions JSONB,
        conversations JSONB,
        readiness_signals JSONB,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    """,
    # SCHEMA: competencies
    """
    CREATE TABLE IF NOT EXISTS competencies (
        id TEXT PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        description TEXT,
        category TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    """,
    # SCHEMA: role_competencies
    """
    CREATE TABLE IF NOT EXISTS role_competencies (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
        competency_id TEXT NOT NULL REFERENCES competencies(id) ON DELETE CASCADE,
        required_level TEXT NOT NULL CHECK (required_level IN ('F','P','A','Au')),
        weight DOUBLE PRECISION DEFAULT 1.0,
        CONSTRAINT uq_role_comp UNIQUE(role_id, competency_id)
    );
    """,
    # SCHEMA: role_adjacency
    """
    CREATE TABLE IF NOT EXISTS role_adjacency (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        source_role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
        target_role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
        adjacency_score DOUBLE PRECISION NOT NULL,
        overlap_percent DOUBLE PRECISION,
        gaps JSONB,
        CONSTRAINT uq_role_adj UNIQUE(source_role_id, target_role_id)
    );
    """,
    # SCHEMA: role_cards
    """
    CREATE TABLE IF NOT EXISTS role_cards (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        role_id TEXT NOT NULL UNIQUE REFERENCES roles(id) ON DELETE CASCADE,
        content TEXT,
        source_doc TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );
    """,
    # SCHEMA: profiles
    """
    CREATE TABLE IF NOT EXISTS profiles (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id UUID NOT NULL,
        current_role TEXT REFERENCES roles(id),
        target_roles TEXT[],
        career_statement TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );
    """,
    # SCHEMA: user_competencies
    """
    CREATE TABLE IF NOT EXISTS user_competencies (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id UUID NOT NULL,
        competency_id TEXT NOT NULL REFERENCES competencies(id) ON DELETE CASCADE,
        level TEXT CHECK (level IN ('Beginner','Intermediate','Advanced')),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );
    """,
    # SCHEMA: learning_resources
    """
    CREATE TABLE IF NOT EXISTS learning_resources (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        competency_id TEXT NOT NULL REFERENCES competencies(id) ON DELETE CASCADE,
        title TEXT,
        url TEXT,
        type TEXT,
        provider TEXT,
        tags TEXT[],
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    """,
    # SCHEMA: progress_events
    """
    CREATE TABLE IF NOT EXISTS progress_events (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id UUID NOT NULL,
        competency_id TEXT NOT NULL REFERENCES competencies(id) ON DELETE CASCADE,
        from_level TEXT,
        to_level TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    """,
    # SCHEMA: assessments
    """
    CREATE TABLE IF NOT EXISTS assessments (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        profile_id UUID NOT NULL REFERENCES profiles(id),
        assessment_data JSONB,
        weighted_scores JSONB,
        completed_at TIMESTAMPTZ
    );
    """,
    # SCHEMA: self_competency_ratings
    """
    CREATE TABLE IF NOT EXISTS self_competency_ratings (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        profile_id UUID NOT NULL REFERENCES profiles(id),
        competency_id TEXT NOT NULL REFERENCES competencies(id),
        rating TEXT CHECK (rating IN ('F','P','A','Au')),
        date_rated TIMESTAMPTZ DEFAULT NOW()
    );
    """,
    # SCHEMA: development_plans
    """
    CREATE TABLE IF NOT EXISTS development_plans (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        profile_id UUID NOT NULL REFERENCES profiles(id),
        target_role TEXT REFERENCES roles(id),
        phase_1 JSONB,
        phase_2 JSONB,
        phase_3 JSONB,
        phase_4 JSONB,
        action_plan_months JSONB,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );
    """,
    # SCHEMA: sponsors
    """
    CREATE TABLE IF NOT EXISTS sponsors (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        profile_id UUID NOT NULL REFERENCES profiles(id),
        sponsor_name TEXT,
        sponsor_role TEXT,
        sponsor_type TEXT CHECK (sponsor_type IN ('operator','external_signaler','mentor')),
        visibility_cadence TEXT CHECK (visibility_cadence IN ('weekly','monthly','quarterly','on_demand')),
        notes TEXT,
        added_at TIMESTAMPTZ DEFAULT NOW()
    );
    """,
    # SCHEMA: evidence
    """
    CREATE TABLE IF NOT EXISTS evidence (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        profile_id UUID NOT NULL REFERENCES profiles(id),
        category TEXT CHECK (category IN ('por','kpi','artifact','leadership','external_signal','other')),
        title TEXT,
        description TEXT,
        link TEXT,
        file_path TEXT,
        date_added TIMESTAMPTZ DEFAULT NOW(),
        evidence_date DATE,
        associated_competencies TEXT[],
        associated_phase TEXT
    );
    """,
    # Indexes
    "CREATE INDEX IF NOT EXISTS idx_role_comp_role ON role_competencies(role_id);",
    "CREATE INDEX IF NOT EXISTS idx_role_comp_comp ON role_competencies(competency_id);",
    "CREATE INDEX IF NOT EXISTS idx_role_adj_src ON role_adjacency(source_role_id);",
    "CREATE INDEX IF NOT EXISTS idx_role_adj_tgt ON role_adjacency(target_role_id);",
]

RLS_AND_POLICIES: List[str] = [
    # Enable RLS on system tables
    "ALTER TABLE roles ENABLE ROW LEVEL SECURITY;",
    "ALTER TABLE competencies ENABLE ROW LEVEL SECURITY;",
    "ALTER TABLE role_competencies ENABLE ROW LEVEL SECURITY;",
    "ALTER TABLE role_adjacency ENABLE ROW LEVEL SECURITY;",
    "ALTER TABLE role_cards ENABLE ROW LEVEL SECURITY;",
    "ALTER TABLE learning_resources ENABLE ROW LEVEL SECURITY;",

    # Enable RLS on user-owned tables
    "ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;",
    "ALTER TABLE user_competencies ENABLE ROW LEVEL SECURITY;",
    "ALTER TABLE progress_events ENABLE ROW LEVEL SECURITY;",
    "ALTER TABLE assessments ENABLE ROW LEVEL SECURITY;",
    "ALTER TABLE self_competency_ratings ENABLE ROW LEVEL SECURITY;",
    "ALTER TABLE development_plans ENABLE ROW LEVEL SECURITY;",
    "ALTER TABLE sponsors ENABLE ROW LEVEL SECURITY;",
    "ALTER TABLE evidence ENABLE ROW LEVEL SECURITY;",

    # Read policies for system tables (authenticated users)
    "DROP POLICY IF EXISTS roles_read_auth ON roles;",
    "CREATE POLICY roles_read_auth ON roles FOR SELECT USING (auth.role() IN ('authenticated','service_role','admin'));",
    "DROP POLICY IF EXISTS competencies_read_auth ON competencies;",
    "CREATE POLICY competencies_read_auth ON competencies FOR SELECT USING (auth.role() IN ('authenticated','service_role','admin'));",
    "DROP POLICY IF EXISTS role_competencies_read_auth ON role_competencies;",
    "CREATE POLICY role_competencies_read_auth ON role_competencies FOR SELECT USING (auth.role() IN ('authenticated','service_role','admin'));",
    "DROP POLICY IF EXISTS role_adjacency_read_auth ON role_adjacency;",
    "CREATE POLICY role_adjacency_read_auth ON role_adjacency FOR SELECT USING (auth.role() IN ('authenticated','service_role','admin'));",
    "DROP POLICY IF EXISTS role_cards_read_auth ON role_cards;",
    "CREATE POLICY role_cards_read_auth ON role_cards FOR SELECT USING (auth.role() IN ('authenticated','service_role','admin'));",
    "DROP POLICY IF EXISTS learning_resources_read_auth ON learning_resources;",
    "CREATE POLICY learning_resources_read_auth ON learning_resources FOR SELECT USING (auth.role() IN ('authenticated','service_role','admin'));",

    # Admin/service_role write policies for system tables
    "DROP POLICY IF EXISTS roles_admin_write ON roles;",
    "CREATE POLICY roles_admin_write ON roles FOR ALL USING (auth.role() IN ('service_role','admin')) WITH CHECK (auth.role() IN ('service_role','admin'));",
    "DROP POLICY IF EXISTS competencies_admin_write ON competencies;",
    "CREATE POLICY competencies_admin_write ON competencies FOR ALL USING (auth.role() IN ('service_role','admin')) WITH CHECK (auth.role() IN ('service_role','admin'));",
    "DROP POLICY IF EXISTS role_competencies_admin_write ON role_competencies;",
    "CREATE POLICY role_competencies_admin_write ON role_competencies FOR ALL USING (auth.role() IN ('service_role','admin')) WITH CHECK (auth.role() IN ('service_role','admin'));",
    "DROP POLICY IF EXISTS role_adjacency_admin_write ON role_adjacency;",
    "CREATE POLICY role_adjacency_admin_write ON role_adjacency FOR ALL USING (auth.role() IN ('service_role','admin')) WITH CHECK (auth.role() IN ('service_role','admin'));",
    "DROP POLICY IF EXISTS role_cards_admin_write ON role_cards;",
    "CREATE POLICY role_cards_admin_write ON role_cards FOR ALL USING (auth.role() IN ('service_role','admin')) WITH CHECK (auth.role() IN ('service_role','admin'));",
    "DROP POLICY IF EXISTS learning_resources_admin_write ON learning_resources;",
    "CREATE POLICY learning_resources_admin_write ON learning_resources FOR ALL USING (auth.role() IN ('service_role','admin')) WITH CHECK (auth.role() IN ('service_role','admin'));",

    # Owner read/write policies for user-owned tables (user_id = auth.uid())
    "DROP POLICY IF EXISTS profiles_owner_rw ON profiles;",
    "CREATE POLICY profiles_owner_rw ON profiles USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());",
    "DROP POLICY IF EXISTS user_competencies_owner_rw ON user_competencies;",
    "CREATE POLICY user_competencies_owner_rw ON user_competencies USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());",
    "DROP POLICY IF EXISTS progress_events_owner_rw ON progress_events;",
    "CREATE POLICY progress_events_owner_rw ON progress_events USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());",
    # assessments table (direct schema uses profile_id); allow read for authenticated
    "DROP POLICY IF EXISTS assessments_read_auth ON assessments;",
    "CREATE POLICY assessments_read_auth ON assessments FOR SELECT USING (auth.role() IN ('authenticated','service_role','admin'));",
    "DROP POLICY IF EXISTS self_competency_ratings_owner_rw ON self_competency_ratings;",
    "CREATE POLICY self_competency_ratings_owner_rw ON self_competency_ratings USING (profile_id IN (SELECT id FROM profiles WHERE user_id = auth.uid())) WITH CHECK (profile_id IN (SELECT id FROM profiles WHERE user_id = auth.uid()));",
    "DROP POLICY IF EXISTS development_plans_owner_rw ON development_plans;",
    "CREATE POLICY development_plans_owner_rw ON development_plans USING (profile_id IN (SELECT id FROM profiles WHERE user_id = auth.uid())) WITH CHECK (profile_id IN (SELECT id FROM profiles WHERE user_id = auth.uid()));",
    "DROP POLICY IF EXISTS sponsors_owner_rw ON sponsors;",
    "CREATE POLICY sponsors_owner_rw ON sponsors USING (profile_id IN (SELECT id FROM profiles WHERE user_id = auth.uid())) WITH CHECK (profile_id IN (SELECT id FROM profiles WHERE user_id = auth.uid()));",
    "DROP POLICY IF EXISTS evidence_owner_rw ON evidence;",
    "CREATE POLICY evidence_owner_rw ON evidence USING (profile_id IN (SELECT id FROM profiles WHERE user_id = auth.uid())) WITH CHECK (profile_id IN (SELECT id FROM profiles WHERE user_id = auth.uid()));",
]

POST_SEED_VERIFICATION = [
    "SELECT COUNT(*) AS roles_count FROM roles;",
    "SELECT COUNT(*) AS competencies_count FROM competencies;",
    "SELECT COUNT(*) AS role_competencies_count FROM role_competencies;",
    "SELECT COUNT(*) AS role_adjacency_count FROM role_adjacency;",
    "SELECT COUNT(*) AS role_cards_count FROM role_cards;",
]

LEVEL_ALIASES = {
    "F": "F",
    "P": "P",
    "A": "A",
    "Au": "Au",
    "Foundational": "F",
    "Proficient": "P",
    "Advanced": "A",
    "Authority": "Au",
    "Master": "Au",
}

ROLE_CARD_PATTERN = re.compile(r"Role_Card_(.+?)_v?\d*\(docx\)\.txt", re.IGNORECASE)
SLUG_RE = re.compile(r"[^a-z0-9]+")


# ---------- Helpers ----------
def _normalize_level(val: str) -> str:
    if not isinstance(val, str):
        return ""
    v = val.strip()
    return LEVEL_ALIASES.get(v, v[:2] if len(v) >= 1 else "")


def slugify(value: str) -> str:
    """Create a lowercase slug from a name."""
    s = value.strip().lower()
    s = s.replace("&", "and").replace("+", "plus")
    s = SLUG_RE.sub("-", s)
    s = s.strip("-")
    return s


def normalize_role_slug(name: str) -> str:
    """Normalize well-known role codes and general names to slugs."""
    name = str(name).strip()
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
    Determine database connection string.

    Precedence:
      1) db_connection.txt at repository root (first non-empty, non-comment line)
      2) DATABASE_URL environment variable
      3) db_connection.txt at workspace root (fallback)
      4) db_connection.txt inside backend folder (fallback)
    """
    # Prefer repo-root db_connection.txt
    primary_file = REPO_ROOT / "db_connection.txt"
    dsn = read_first_nonempty_line(primary_file)
    if dsn:
        return dsn

    # Then environment variable
    env = os.getenv("DATABASE_URL")
    if env:
        return env

    # Additional fallbacks
    for c in [
        WORKSPACE_ROOT / "db_connection.txt",
        BACKEND_ROOT / "db_connection.txt",
    ]:
        dsn = read_first_nonempty_line(c)
        if dsn:
            return dsn
    return None


def connect() -> "psycopg2.extensions.connection":
    dsn = get_database_url()
    if not dsn:
        raise RuntimeError(
            "Missing database connection. Set DATABASE_URL or provide db_connection.txt at repo root."
        )
    return psycopg2.connect(dsn)


def exec_ddl(conn, statements: List[str]) -> List[Tuple[str, Optional[str]]]:
    """
    Execute each statement individually and return [(sql, error_message_or_None), ...]
    """
    results = []
    with conn.cursor() as cur:
        for stmt in statements:
            s = stmt.strip().rstrip(";")
            if not s:
                continue
            try:
                cur.execute(stmt)
                results.append((stmt, None))
            except Exception as e:
                results.append((stmt, str(e)))
    conn.commit()
    return results


# ---------- Parsing attachments ----------
def read_competency_mapping(xlsx_path: Path):
    df = pd.read_excel(xlsx_path)
    df.columns = [str(c).strip() for c in df.columns]
    if not len(df.columns):
        return {}, {}, []

    first_col = df.columns[0]
    role_cols = df.columns[1:]

    competencies: Dict[str, Dict[str, Optional[str]]] = {}
    mappings: List[Dict] = []
    roles_seen: Dict[str, Dict[str, Optional[str]]] = {}

    for _, row in df.iterrows():
        comp_name = str(row[first_col]).strip()
        if not comp_name or comp_name.lower() == "nan":
            continue
        comp_slug = slugify(comp_name)
        competencies[comp_slug] = {"name": comp_name, "description": None, "category": None}

        for role_col in role_cols:
            level_raw = row.get(role_col, "")
            level = _normalize_level(str(level_raw)) if str(level_raw).strip() else ""
            if not level:
                continue
            role_name = str(role_col).strip()
            role_slug = normalize_role_slug(role_name)
            roles_seen[role_slug] = {
                "name": role_name,
                "description": None,
                "family": None,
                "seniority": None,
                "mission": None,
                "scope": None,
                "key_decisions": None,
                "conversations": None,
                "readiness_signals": None,
            }

            mappings.append(
                {
                    "role_id": role_slug,
                    "competency_id": comp_slug,
                    "required_level": level,
                    "weight": 1.0,
                }
            )

    return roles_seen, competencies, mappings


def parse_gap_cell(cell: str) -> List[Dict]:
    """
    Parse text like "DX: P->A; Risk: F->P" into a list of gap dicts.
    """
    gap_list = []
    parts = str(cell).split(";")
    for p in parts:
        p = p.strip()
        m = re.match(r"(.+?):\s*([A-Za-z]+)\s*->\s*([A-Za-z]+)", p)
        if not m:
            m = re.match(r"(.+?)\s*\(\s*([A-Za-z]+)\s*->\s*([A-Za-z]+)\s*\)", p)
        if m:
            comp_name = m.group(1).strip()
            src_level = _normalize_level(m.group(2).strip())
            tgt_level = _normalize_level(m.group(3).strip())
            gap_list.append(
                {
                    "competency_id": slugify(comp_name),
                    "source_level": src_level,
                    "target_level": tgt_level,
                    "delta": f"{src_level}->{tgt_level}",
                }
            )
    return gap_list


def parse_adjacency_file(xlsx_path: Path) -> List[Dict]:
    """
    Expect a matrix-like sheet:
      - First column = source role
      - Subsequent columns = target roles
      - Cells may contain gap text, from which we derive adjacency_score.
    """
    df = pd.read_excel(xlsx_path)
    df.columns = [str(c).strip() for c in df.columns]
    cols = df.columns.tolist()
    if len(cols) < 2:
        return []

    source_col = cols[0]
    targets = cols[1:]
    records: List[Dict] = []

    for _, row in df.iterrows():
        src_name = str(row[source_col]).strip()
        if not src_name or src_name.lower() == "nan":
            continue
        src_slug = normalize_role_slug(src_name)

        for tgt in targets:
            tgt_name = tgt
            tgt_slug = normalize_role_slug(tgt_name)
            cell = row.get(tgt, "")
            if pd.isna(cell) or str(cell).strip() == "":
                # Even if empty, we may capture neutral adjacency
                gap_list: List[Dict] = []
            else:
                gap_list = parse_gap_cell(str(cell))

            # Heuristic adjacency score: 1 - (#gaps * 0.05), bounded [0.1, 1.0]
            # Ensures a required numeric score even if gaps missing.
            score = max(0.1, min(1.0, 1.0 - (len(gap_list) * 0.05)))
            records.append(
                {
                    "source_role_id": src_slug,
                    "target_role_id": tgt_slug,
                    "adjacency_score": float(score),
                    "overlap_percent": None,
                    "gaps": gap_list if gap_list else None,
                }
            )
    return records


@dataclass
class RoleCardAggregate:
    role_id: str
    source_docs: List[str]
    combined_content: str


def read_role_cards(attach_dir: Path, known_roles: Dict[str, Dict[str, Optional[str]]]) -> Tuple[List[RoleCardAggregate], List[Dict]]:
    """Aggregate multiple card files per role into a single record due to unique(role_id)."""
    by_role: Dict[str, RoleCardAggregate] = {}
    unmapped: List[Dict] = []

    # Role_Card_* files
    for f in attach_dir.glob("2025*_Role_Card_*txt"):
        m = ROLE_CARD_PATTERN.search(f.name)
        if not m:
            basename = f.stem.replace("Role_Card_", "").split("(docx)")[0]
            role_code = basename.split("_v")[0]
        else:
            role_code = m.group(1)

        role_code = role_code.replace("_", "")
        code_map = {
            "AppDev": "appdev",
            "CAIO": "caio",
            "CCTO": "ccto",
            "CDAO": "cdao",
            "CDO": "cdo",
            "CInO": "cino",
            "CIO": "cio",
            "CPTO": "cpto",
            "CTrO": "ctro",
            "DigProd": "digprod",
            "FCTO": "fcto",
            "Infra": "infra",
            "Ops": "ops",
            "PMO": "pmo",
        }
        role_slug = code_map.get(role_code, normalize_role_slug(role_code))
        if role_slug not in known_roles:
            unmapped.append({"file": f.name, "derived_role": role_slug})

        content = f.read_text(encoding="utf-8", errors="ignore")
        if role_slug not in by_role:
            by_role[role_slug] = RoleCardAggregate(role_id=role_slug, source_docs=[f.name], combined_content=content)
        else:
            agg = by_role[role_slug]
            agg.source_docs.append(f.name)
            agg.combined_content += f"\n\n-----\nFILE: {f.name}\n\n{content}"

    # Additional narrative files that help seed CTO/CA roles
    for f in attach_dir.glob("2025*Chief*txt"):
        txt = f.read_text(encoding="utf-8", errors="ignore")
        inferred = None
        if "Chief Technology Officer" in txt or "CTO" in txt:
            inferred = "cto"
        elif "Chief Architect" in txt:
            inferred = "ca"
        if inferred:
            if inferred not in by_role:
                by_role[inferred] = RoleCardAggregate(role_id=inferred, source_docs=[f.name], combined_content=txt)
            else:
                agg = by_role[inferred]
                agg.source_docs.append(f.name)
                agg.combined_content += f"\n\n-----\nFILE: {f.name}\n\n{txt}"
            if inferred not in known_roles:
                known_roles[inferred] = {"name": inferred.upper(), "description": None, "family": None, "seniority": None,
                                         "mission": None, "scope": None, "key_decisions": None,
                                         "conversations": None, "readiness_signals": None}

    return list(by_role.values()), unmapped


# ---------- Upserts ----------
def upsert_roles(conn, roles: Dict[str, Dict[str, Optional[str]]]) -> int:
    rows = [
        (
            rid,
            data.get("name"),
            data.get("description"),
            data.get("family"),
            data.get("seniority"),
            data.get("mission"),
            json.dumps(data.get("scope")) if isinstance(data.get("scope"), (list, dict)) else None,
            json.dumps(data.get("key_decisions")) if isinstance(data.get("key_decisions"), (list, dict)) else None,
            json.dumps(data.get("conversations")) if isinstance(data.get("conversations"), (list, dict)) else None,
            json.dumps(data.get("readiness_signals")) if isinstance(data.get("readiness_signals"), (list, dict)) else None,
        )
        for rid, data in roles.items()
    ]
    sql = """
    INSERT INTO roles (id, name, description, family, seniority, mission, scope, key_decisions, conversations, readiness_signals)
    VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb)
    ON CONFLICT (id) DO UPDATE
    SET name = EXCLUDED.name,
        description = EXCLUDED.description,
        family = EXCLUDED.family,
        seniority = EXCLUDED.seniority,
        mission = EXCLUDED.mission,
        scope = EXCLUDED.scope,
        key_decisions = EXCLUDED.key_decisions,
        conversations = EXCLUDED.conversations,
        readiness_signals = EXCLUDED.readiness_signals;
    """
    with conn.cursor() as cur:
        execute_batch(cur, sql, rows, page_size=100)
    conn.commit()
    return len(rows)


def upsert_competencies(conn, competencies: Dict[str, Dict[str, Optional[str]]]) -> int:
    rows = [(cid, data.get("name"), data.get("description"), data.get("category")) for cid, data in competencies.items()]
    sql = """
    INSERT INTO competencies (id, name, description, category)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE
    SET name = EXCLUDED.name,
        description = EXCLUDED.description,
        category = EXCLUDED.category;
    """
    with conn.cursor() as cur:
        execute_batch(cur, sql, rows, page_size=100)
    conn.commit()
    return len(rows)


def upsert_role_competencies(conn, mappings: List[Dict]) -> int:
    rows = [(m["role_id"], m["competency_id"], m["required_level"], float(m.get("weight", 1.0))) for m in mappings]
    sql = """
    INSERT INTO role_competencies (role_id, competency_id, required_level, weight)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT ON CONSTRAINT uq_role_comp DO UPDATE
    SET required_level = EXCLUDED.required_level,
        weight = EXCLUDED.weight;
    """
    with conn.cursor() as cur:
        execute_batch(cur, sql, rows, page_size=200)
    conn.commit()
    return len(rows)


def upsert_role_adjacency(conn, records: List[Dict]) -> int:
    rows = [
        (
            r["source_role_id"],
            r["target_role_id"],
            float(r.get("adjacency_score") if r.get("adjacency_score") is not None else 0.7),
            r.get("overlap_percent"),
            json.dumps(r.get("gaps")) if r.get("gaps") is not None else None,
        )
        for r in records
    ]
    sql = """
    INSERT INTO role_adjacency (source_role_id, target_role_id, adjacency_score, overlap_percent, gaps)
    VALUES (%s, %s, %s, %s, %s::jsonb)
    ON CONFLICT ON CONSTRAINT uq_role_adj DO UPDATE
    SET adjacency_score = EXCLUDED.adjacency_score,
        overlap_percent = EXCLUDED.overlap_percent,
        gaps = EXCLUDED.gaps;
    """
    with conn.cursor() as cur:
        execute_batch(cur, sql, rows, page_size=200)
    conn.commit()
    return len(rows)


def upsert_role_cards(conn, card_aggs: List[RoleCardAggregate]) -> int:
    rows = [
        (
            agg.role_id,
            agg.combined_content,
            ", ".join(agg.source_docs) if agg.source_docs else None,
        )
        for agg in card_aggs
    ]
    # Table has UNIQUE(role_id), so we conflict on role_id only
    sql = """
    INSERT INTO role_cards (role_id, content, source_doc)
    VALUES (%s, %s, %s)
    ON CONFLICT (role_id) DO UPDATE
    SET content = EXCLUDED.content,
        source_doc = EXCLUDED.source_doc,
        updated_at = NOW();
    """
    with conn.cursor() as cur:
        execute_batch(cur, sql, rows, page_size=50)
    conn.commit()
    return len(rows)


# PUBLIC_INTERFACE
def main():
    """Run direct DDL and data seeding, then print/write verification counts."""
    report = {
        "ddl_results": [],
        "roles_upserted": 0,
        "competencies_upserted": 0,
        "role_competencies_upserted": 0,
        "role_adjacency_upserted": 0,
        "role_cards_upserted": 0,
        "verification": {},
        "warnings": [],
    }

    # Validate attachments existence
    comp_map_file = ATTACH_DIR / "20251127_044906_Competency_mapping.xlsx"
    adj_files = [
        ATTACH_DIR / "20251127_044904_CA_Role_Adjacency.xlsx",
        ATTACH_DIR / "20251127_044905_CA_Role_Adjacency29.xlsx",
    ]
    if not comp_map_file.exists():
        raise FileNotFoundError(f"Missing competency mapping file: {comp_map_file}")

    # Parse mapping
    roles_seen, competencies, mappings = read_competency_mapping(comp_map_file)

    # Ensure minimal roles
    minimal_roles = {
        "ca": {
            "name": "Chief Architect",
            "description": None,
            "family": None,
            "seniority": None,
            "mission": None,
            "scope": None,
            "key_decisions": None,
            "conversations": None,
            "readiness_signals": None,
        },
        "cto": {
            "name": "Chief Technology Officer (AI & Technology)",
            "description": None,
            "family": None,
            "seniority": None,
            "mission": None,
            "scope": None,
            "key_decisions": None,
            "conversations": None,
            "readiness_signals": None,
        },
        "cio": {
            "name": "Chief Information Officer",
            "description": None,
            "family": None,
            "seniority": None,
            "mission": None,
            "scope": None,
            "key_decisions": None,
            "conversations": None,
            "readiness_signals": None,
        },
    }
    for k, v in minimal_roles.items():
        roles_seen.setdefault(k, v)

    # Adjacency records
    adjacency_records: List[Dict] = []
    for f in adj_files:
        if f.exists():
            adjacency_records.extend(parse_adjacency_file(f))
        else:
            report["warnings"].append(f"Adjacency file missing: {f.name}")

    # Deduplicate adjacency by (src,tgt): prefer record with gaps info
    uniq = {}
    for r in adjacency_records:
        key = (r["source_role_id"], r["target_role_id"])
        if key not in uniq or (not uniq[key].get("gaps") and r.get("gaps")):
            uniq[key] = r
    adjacency_records = list(uniq.values())

    # Role cards
    card_aggs, unmapped_cards = read_role_cards(ATTACH_DIR, roles_seen)
    if unmapped_cards:
        report["warnings"].append({"unmapped_role_cards": unmapped_cards})

    # Connect and run
    conn = connect()

    # DDL
    ddl_results = exec_ddl(conn, DDL_STATEMENTS)
    rls_results = exec_ddl(conn, RLS_AND_POLICIES)
    combined = ddl_results + rls_results
    report["ddl_results"] = [
        {"statement": s, "error": err} for (s, err) in combined
    ]

    # Upserts in FK-safe order
    report["roles_upserted"] = upsert_roles(conn, roles_seen)
    report["competencies_upserted"] = upsert_competencies(conn, competencies)
    report["role_competencies_upserted"] = upsert_role_competencies(conn, mappings)
    # Make sure any roles introduced by cards are present
    new_roles_from_cards = {}
    for agg in card_aggs:
        if agg.role_id not in roles_seen:
            new_roles_from_cards[agg.role_id] = {"name": agg.role_id.upper(), "description": None,
                                                 "family": None, "seniority": None, "mission": None,
                                                 "scope": None, "key_decisions": None,
                                                 "conversations": None, "readiness_signals": None}
    if new_roles_from_cards:
        report["roles_upserted"] += upsert_roles(conn, new_roles_from_cards)

    report["role_adjacency_upserted"] = upsert_role_adjacency(conn, adjacency_records)
    report["role_cards_upserted"] = upsert_role_cards(conn, card_aggs)

    # Verification counts
    verification = {}
    with conn.cursor() as cur:
        for q in POST_SEED_VERIFICATION:
            cur.execute(q)
            res = cur.fetchone()
            # key name derived from alias in query
            colname = cur.description[0].name
            verification[colname] = int(res[0])
    conn.commit()
    conn.close()

    report["verification"] = verification

    # Also write counts-only report to top-level and backend logs
    COUNTS_REPORT_TOP.write_text(json.dumps(verification, indent=2))
    COUNTS_REPORT_BACKEND.write_text(json.dumps(verification, indent=2))

    # Save/print
    REPORT_PATH.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    # Provide a friendly error if DATABASE_URL not set and db_connection.txt missing
    try:
        main()
    except Exception as e:
        # Emit JSON so CI logs stay structured
        error_report = {"error": str(e)}
        print(json.dumps(error_report, indent=2), file=sys.stderr)
        # Also write a minimal report for traceability
        REPORT_PATH.write_text(json.dumps(error_report, indent=2))
        sys.exit(1)
