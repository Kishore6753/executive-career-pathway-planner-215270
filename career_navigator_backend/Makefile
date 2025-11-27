.PHONY: db-migrate db-seed db-revise db-direct-seed

# Requires DATABASE_URL in environment or db_connection.txt at repo root

db-migrate:
	alembic -c alembic.ini upgrade head

db-revise:
	alembic -c alembic.ini revision --autogenerate -m "$(m)"

db-seed:
	python -m scripts.seed_career_navigator

# Apply requested schema via direct SQL and seed from attachments (bypasses Alembic schema)
db-direct-seed:
	python -m scripts.run_direct_sql_seed
