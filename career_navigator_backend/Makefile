.PHONY: db-migrate db-seed db-revise

# Requires DATABASE_URL in environment

db-migrate:
\talembic -c alembic.ini upgrade head

db-revise:
\talembic -c alembic.ini revision --autogenerate -m "$(m)"

db-seed:
\tpython -m scripts.seed_career_navigator
