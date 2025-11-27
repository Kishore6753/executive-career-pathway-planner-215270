Career Navigator Backend - Database Setup

Prerequisites:
- A Supabase Postgres instance or any Postgres database.
- Set DATABASE_URL in the environment (do not commit secrets).
- Alternatively, use Supabase service role to connect via DATABASE_URL from the project's connection string.

Steps:
1) Install dependencies:
   pip install -r career_navigator_backend/requirements.txt

2) Create and apply migrations:
   cd career_navigator_backend
   export DATABASE_URL="postgres://<user>:<pass>@<host>:<port>/<db>"
   make db-migrate

3) Seed data from attachments:
   make db-seed

Artifacts:
- A detailed report is written to career_navigator_backend/logs/seed_report.json

RLS:
- System tables (roles, competencies, role_competencies, role_adjacency, role_cards, learning_resources) allow read to authenticated users.
- Writes to those tables require admin/service_role (Supabase JWT claim role='admin' or 'service_role').
- User-owned tables are restricted by auth.uid() = user_id.
