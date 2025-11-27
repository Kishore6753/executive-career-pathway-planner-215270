"""
Seed script for Career Navigator.

Usage:
  python -m scripts.seed_career_navigator

This script:
- Reads Excel/text files from the top-level 'attachments/' directory.
- Normalizes role and competency slugs.
- Upserts roles, competencies, role_competencies, role_adjacency, and role_cards.
- Emits logs/seed_report.json with counts and warnings.

Environment:
- DATABASE_URL preferred for direct Postgres.
- If missing, exits with error (Supabase client path can be added later if required).

Attachments used:
- 20251127_044906_Competency_mapping.xlsx
- 20251127_044904_CA_Role_Adjacency.xlsx
- 20251127_044905_CA_Role_Adjacency29.xlsx
- Role card text files named like 'Role_Card_*.txt' and other role docs.
"""
import json
import re
from pathlib import Path
from typing import Dict, List

import pandas as pd

from src.db.utils import (
    get_conn,
    slugify,
    normalize_role_slug,
    upsert_roles,
    upsert_competencies,
    upsert_role_competencies,
    upsert_role_adjacency,
    upsert_role_cards,
)

BASE_DIR = Path(__file__).resolve().parents[1].parents[1]  # go to workspace root
ATTACH_DIR = BASE_DIR / "attachments"
LOG_DIR = Path(__file__).resolve().parents[1].parents[0] / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
REPORT_PATH = LOG_DIR / "seed_report.json"

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


def _normalize_level(val: str) -> str:
    if not isinstance(val, str):
        return ""
    v = val.strip()
    return LEVEL_ALIASES.get(v, v[:2] if len(v) >= 1 else "")


def read_competency_mapping(xlsx_path: Path):
    df = pd.read_excel(xlsx_path)
    # Expect first column as "Competency" or similar; roles are columns thereafter
    df.columns = [str(c).strip() for c in df.columns]
    first_col = df.columns[0]
    role_cols = df.columns[1:]

    # Build competency catalog
    competencies: Dict[str, Dict[str, str]] = {}
    mappings: List[Dict] = []
    roles_seen: Dict[str, Dict[str, str]] = {}

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
            roles_seen[role_slug] = {"name": role_name, "description": None}

            mappings.append(
                {
                    "role_id": role_slug,
                    "competency_id": comp_slug,
                    "required_level": level,
                    "notes": None,
                }
            )

    return roles_seen, competencies, mappings


def parse_adjacency_file(xlsx_path: Path) -> List[Dict]:
    # Expect a matrix-like sheet: first column target roles or competency deltas per role mapping
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
        # For each target column, parse deltas if present
        for tgt in targets:
            tgt_name = tgt
            tgt_slug = normalize_role_slug(tgt_name)
            cell = row.get(tgt, "")
            if pd.isna(cell) or str(cell).strip() == "":
                continue
            # Attempt to parse a semi-structured text listing competency gaps, e.g. "DX: P->A; Risk: F->P"
            gap_list = []
            parts = str(cell).split(";")
            for p in parts:
                p = p.strip()
                # patterns like "Competency: X->Y" or "competency (X->Y)"
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
            records.append(
                {
                    "source_role_id": src_slug,
                    "target_role_id": tgt_slug,
                    "gap": gap_list,
                    "weight": None,
                }
            )
    return records


def read_role_cards(attach_dir: Path, known_roles: Dict[str, Dict[str, str]]):
    records = []
    unmapped = []

    for f in attach_dir.glob("2025*_Role_Card_*txt"):
        m = ROLE_CARD_PATTERN.search(f.name)
        if not m:
            # Try simpler derivation from filename
            basename = f.stem.replace("Role_Card_", "").split("(docx)")[0]
            role_code = basename.split("_v")[0]
        else:
            role_code = m.group(1)

        role_code = role_code.replace("_", "")
        # Common variants map
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
        records.append({"role_id": role_slug, "source_doc": f.name, "content": content})
    # Also ingest two textual role descriptions if present and map to likely roles
    for f in attach_dir.glob("2025*Chief*txt"):
        txt = f.read_text(encoding="utf-8", errors="ignore")
        inferred = None
        if "Chief Technology Officer" in txt or "CTO" in txt:
            inferred = "cto"
        elif "Chief Architect" in txt or "CA " in f.name:
            inferred = "ca"
        if inferred:
            records.append({"role_id": inferred, "source_doc": f.name, "content": txt})
            if inferred not in known_roles:
                # ensure will be created
                known_roles[inferred] = {"name": inferred.upper(), "description": None}
    return records, unmapped


def main():
    report = {
        "roles_upserted": 0,
        "competencies_upserted": 0,
        "role_competencies_upserted": 0,
        "role_adjacency_upserted": 0,
        "role_cards_upserted": 0,
        "warnings": [],
    }

    comp_map_file = ATTACH_DIR / "20251127_044906_Competency_mapping.xlsx"
    adj_files = [
        ATTACH_DIR / "20251127_044904_CA_Role_Adjacency.xlsx",
        ATTACH_DIR / "20251127_044905_CA_Role_Adjacency29.xlsx",
    ]

    if not comp_map_file.exists():
        raise FileNotFoundError(f"Missing competency mapping file: {comp_map_file}")

    roles_seen, competencies, mappings = read_competency_mapping(comp_map_file)

    # Connect DB
    conn = get_conn()

    # Ensure core roles for well-known codes even if not present
    minimal_roles = {
        "ca": {"name": "Chief Architect", "description": None},
        "cto": {"name": "Chief Technology Officer (AI & Technology)", "description": None},
        "cio": {"name": "Chief Information Officer", "description": None},
    }
    for k, v in minimal_roles.items():
        roles_seen.setdefault(k, v)

    report["roles_upserted"] = upsert_roles(conn, roles_seen)
    report["competencies_upserted"] = upsert_competencies(conn, competencies)
    report["role_competencies_upserted"] = upsert_role_competencies(conn, mappings)

    # Role adjacency
    adjacency_records: List[Dict] = []
    for f in adj_files:
        if f.exists():
            adjacency_records.extend(parse_adjacency_file(f))
        else:
            report["warnings"].append(f"Adjacency file missing: {f.name}")
    # Optionally deduplicate (unique by src,tgt)
    uniq = {}
    for r in adjacency_records:
        key = (r["source_role_id"], r["target_role_id"])
        if key not in uniq or (uniq[key].get("gap") == [] and r.get("gap")):
            uniq[key] = r
    adjacency_records = list(uniq.values())
    report["role_adjacency_upserted"] = upsert_role_adjacency(conn, adjacency_records)

    # Role cards
    role_card_records, unmapped = read_role_cards(ATTACH_DIR, roles_seen)
    if unmapped:
        report["warnings"].append({"unmapped_role_cards": unmapped})

    # Make sure any new roles from cards are inserted
    new_roles_from_cards = {}
    for rc in role_card_records:
        rid = rc["role_id"]
        if rid not in roles_seen:
            new_roles_from_cards[rid] = {"name": rid.upper(), "description": None}
    if new_roles_from_cards:
        report["roles_upserted"] += upsert_roles(conn, new_roles_from_cards)

    report["role_cards_upserted"] = upsert_role_cards(conn, role_card_records)

    REPORT_PATH.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
