Career Navigator Backend - Database Setup and Direct SQL Seeding

Prerequisites:
- A Supabase Postgres instance or any Postgres database.
- Set DATABASE_URL in the environment (do not commit secrets).
- Alternatively, provide a db_connection.txt file at the repository root (the first non-empty, non-comment line is used as the DSN).
- Ensure Python dependencies are installed.

Steps:
1) Install dependencies:
   cd career_navigator_backend
   pip install -r requirements.txt

2) Choose your connection method:
   Option A (environment variable):
     export DATABASE_URL="postgresql://<user>:<pass>@<host>:<port>/<db>?sslmode=require"

   Option B (file-based):
     - Copy executive-career-pathway-planner-215270/db_connection.txt.example to db_connection.txt in the same folder:
       cp executive-career-pathway-planner-215270/db_connection.txt.example executive-career-pathway-planner-215270/db_connection.txt
     - Edit db_connection.txt and put your full Postgres connection string on the first non-empty line.
     - Note: Do NOT commit real secrets.

3) Create and apply Alembic migrations (optional if using direct seeding DDL):
   cd career_navigator_backend
   make db-migrate

4) Seed data (ORM/utility-based):
   make db-seed

5) Direct SQL schema + seed (bypasses Alembic; applies DDL and ingests attachments):
   cd executive-career-pathway-planner-215270/career_navigator_backend
   make db-direct-seed

   This runs:
     python -m scripts.run_direct_sql_seed

   What it does:
   - Connects to the database using:
       1) DATABASE_URL env var, or
       2) db_connection.txt at repo root (executive-career-pathway-planner-215270/db_connection.txt),
          or a fallback db_connection.txt placed inside career_navigator_backend/.
   - Applies required DDL one statement at a time (CREATE EXTENSION, roles, competencies, role_competencies, role_adjacency, role_cards, and related tables).
   - Parses the Excel/text attachments in /attachments and upserts into:
       - roles
       - competencies
       - role_competencies
       - role_adjacency
       - role_cards
   - If a table already exists, it continues; if columns differ, it logs the error and proceeds with compatible upserts where possible.
   - Writes a concise JSON report (including SELECT COUNT(*) verification) to:
       executive-career-pathway-planner-215270/career_navigator_backend/logs/direct_seed_report.json
   - Prints the same report to stdout.

Attachments expected:
- attachments/20251127_044906_Competency_mapping.xlsx
- attachments/20251127_044904_CA_Role_Adjacency.xlsx
- attachments/20251127_044905_CA_Role_Adjacency29.xlsx
- attachments/Role_Card_*.txt docs and other role-related .txt files

Verification:
- After a successful run, the report includes counts:
  - roles_count
  - competencies_count
  - role_competencies_count
  - role_adjacency_count
  - role_cards_count

Troubleshooting:
- Missing module (e.g., pandas/openpyxl):
  pip install -r career_navigator_backend/requirements.txt

- Missing database connection error:
  Ensure either DATABASE_URL is exported or db_connection.txt exists at:
    executive-career-pathway-planner-215270/db_connection.txt
  The first non-empty, non-comment line must be a valid Postgres DSN, e.g.:
    postgresql://user:pass@host:5432/dbname?sslmode=require

- Supabase:
  Use the service role or connection string from Supabase (service role recommended for seeding).
  Ensure sslmode=require if needed.

Artifacts:
- ORM-based seeding report: career_navigator_backend/logs/seed_report.json
- Direct seeding report: career_navigator_backend/logs/direct_seed_report.json

RLS:
- System tables (roles, competencies, role_competencies, role_adjacency, role_cards, learning_resources) allow read to authenticated users.
- Writes to those tables require admin/service_role (Supabase JWT claim role='admin' or 'service_role').
- User-owned tables are restricted by auth.uid() = user_id.
