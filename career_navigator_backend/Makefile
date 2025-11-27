.PHONY: db-migrate db-seed db-revise db-direct-seed db-counts

# Requires DATABASE_URL in environment or db_connection.txt at repo root

db-migrate:
	alembic -c alembic.ini upgrade head

db-revise:
	alembic -c alembic.ini revision --autogenerate -m "$(m)"

db-seed:
	python -m scripts.seed_career_navigator

# Apply requested schema via direct SQL and seed from attachments (bypasses Alembic schema)
# Now runs a connection check first, then seeds, then verification counts.
db-direct-seed:
	python -m scripts.db_connect_check && python -m scripts.run_direct_sql_seed && python -m scripts.query_counts

# Run verification counts on key tables
db-counts:
	python -m scripts.query_counts
