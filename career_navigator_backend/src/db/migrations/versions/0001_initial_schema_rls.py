"""Initial schema with RLS for Career Navigator

Revision ID: 0001_initial
Revises:
Create Date: 2025-11-27

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create extensions needed
    op.execute("create extension if not exists pgcrypto;")
    op.execute("create extension if not exists uuid-ossp;")

    # Schema note: using public (Supabase default)

    # System tables (readable by authenticated; write admin only)
    op.execute(
        """
        create table if not exists roles (
            id text primary key,           -- slug (e.g., 'CTO','CIO','CA')
            name text not null,
            description text,
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now()
        );
        create unique index if not exists roles_name_ux on roles(lower(name));

        create table if not exists competencies (
            id text primary key,           -- slug
            name text not null,
            description text,
            category text,
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now()
        );
        create unique index if not exists competencies_name_ux on competencies(lower(name));

        create table if not exists role_competencies (
            role_id text not null references roles(id) on delete cascade,
            competency_id text not null references competencies(id) on delete cascade,
            required_level text not null,   -- 'F','P','A','Au' (Foundational, Proficient, Advanced, Authority)
            notes text,
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now(),
            primary key (role_id, competency_id)
        );

        create table if not exists role_adjacency (
            id uuid primary key default gen_random_uuid(),
            source_role_id text not null references roles(id) on delete cascade,
            target_role_id text not null references roles(id) on delete cascade,
            gap jsonb not null default '[]'::jsonb, -- list of {competency_id, source_level, target_level, delta}
            weight numeric,        -- optional computed adjacency weight
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now(),
            unique (source_role_id, target_role_id)
        );

        create table if not exists role_cards (
            id uuid primary key default gen_random_uuid(),
            role_id text not null references roles(id) on delete cascade,
            source_doc text,             -- filename/source
            content text not null,       -- full role card text
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now(),
            unique (role_id, source_doc)
        );

        create table if not exists learning_resources (
            id uuid primary key default gen_random_uuid(),
            title text not null,
            url text,
            type text,                   -- e.g., 'article','course','book'
            competency_id text references competencies(id) on delete set null,
            role_id text references roles(id) on delete set null,
            tags text[],
            metadata jsonb,
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now()
        );
        """
    )

    # User owned tables (restricted by auth.uid())
    op.execute(
        """
        create table if not exists profiles (
            id uuid primary key default gen_random_uuid(),
            user_id uuid not null,  -- auth.users.id
            display_name text,
            current_role_id text references roles(id) on delete set null,
            target_role_id text references roles(id) on delete set null,
            preferences jsonb,
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now(),
            unique (user_id)
        );
        create index if not exists profiles_user_idx on profiles(user_id);

        create table if not exists user_competencies (
            id uuid primary key default gen_random_uuid(),
            user_id uuid not null,
            competency_id text not null references competencies(id) on delete cascade,
            level text not null,  -- 'F','P','A','Au' or scalar mapping
            evidence jsonb,
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now(),
            unique (user_id, competency_id)
        );
        create index if not exists user_comp_user_idx on user_competencies(user_id);

        create table if not exists progress_events (
            id uuid primary key default gen_random_uuid(),
            user_id uuid not null,
            event_type text not null,
            details jsonb,
            created_at timestamptz not null default now()
        );
        create index if not exists progress_events_user_idx on progress_events(user_id);

        create table if not exists assessments (
            id uuid primary key default gen_random_uuid(),
            user_id uuid not null,
            role_id text references roles(id) on delete set null,
            responses jsonb not null,
            score jsonb,
            created_at timestamptz not null default now()
        );
        create index if not exists assessments_user_idx on assessments(user_id);

        create table if not exists self_competency_ratings (
            id uuid primary key default gen_random_uuid(),
            user_id uuid not null,
            competency_id text not null references competencies(id) on delete cascade,
            self_level text not null,
            notes text,
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now(),
            unique (user_id, competency_id)
        );

        create table if not exists development_plans (
            id uuid primary key default gen_random_uuid(),
            user_id uuid not null,
            role_id text references roles(id) on delete set null,
            plan jsonb not null,
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now()
        );
        create index if not exists development_plans_user_idx on development_plans(user_id);

        create table if not exists sponsors (
            id uuid primary key default gen_random_uuid(),
            user_id uuid not null,
            sponsor_name text not null,
            relationship text,
            created_at timestamptz not null default now()
        );
        create index if not exists sponsors_user_idx on sponsors(user_id);

        create table if not exists evidence (
            id uuid primary key default gen_random_uuid(),
            user_id uuid not null,
            title text not null,
            description text,
            links text[],
            artifacts jsonb,
            created_at timestamptz not null default now()
        );
        create index if not exists evidence_user_idx on evidence(user_id);
        """
    )

    # RLS enablement
    op.execute(
        """
        -- Enable RLS
        alter table roles enable row level security;
        alter table competencies enable row level security;
        alter table role_competencies enable row level security;
        alter table role_adjacency enable row level security;
        alter table role_cards enable row level security;
        alter table learning_resources enable row level security;

        alter table profiles enable row level security;
        alter table user_competencies enable row level security;
        alter table progress_events enable row level security;
        alter table assessments enable row level security;
        alter table self_competency_ratings enable row level security;
        alter table development_plans enable row level security;
        alter table sponsors enable row level security;
        alter table evidence enable row level security;

        -- Policies for system tables: read for authenticated, write only for admin
        create policy roles_read_auth on roles for select using (auth.role() in ('authenticated','service_role','admin'));
        create policy competencies_read_auth on competencies for select using (auth.role() in ('authenticated','service_role','admin'));
        create policy role_competencies_read_auth on role_competencies for select using (auth.role() in ('authenticated','service_role','admin'));
        create policy role_adjacency_read_auth on role_adjacency for select using (auth.role() in ('authenticated','service_role','admin'));
        create policy role_cards_read_auth on role_cards for select using (auth.role() in ('authenticated','service_role','admin'));
        create policy learning_resources_read_auth on learning_resources for select using (auth.role() in ('authenticated','service_role','admin'));

        -- Insert/Update/Delete only for admin or service_role
        create policy roles_admin_write on roles for all using (auth.role() in ('service_role','admin')) with check (auth.role() in ('service_role','admin'));
        create policy competencies_admin_write on competencies for all using (auth.role() in ('service_role','admin')) with check (auth.role() in ('service_role','admin'));
        create policy role_competencies_admin_write on role_competencies for all using (auth.role() in ('service_role','admin')) with check (auth.role() in ('service_role','admin'));
        create policy role_adjacency_admin_write on role_adjacency for all using (auth.role() in ('service_role','admin')) with check (auth.role() in ('service_role','admin'));
        create policy role_cards_admin_write on role_cards for all using (auth.role() in ('service_role','admin')) with check (auth.role() in ('service_role','admin'));
        create policy learning_resources_admin_write on learning_resources for all using (auth.role() in ('service_role','admin')) with check (auth.role() in ('service_role','admin'));

        -- Policies for user-owned tables (user_id equals auth.uid())
        create policy profiles_owner_rw on profiles
            using (user_id = auth.uid())
            with check (user_id = auth.uid());
        create policy user_competencies_owner_rw on user_competencies
            using (user_id = auth.uid())
            with check (user_id = auth.uid());
        create policy progress_events_owner_rw on progress_events
            using (user_id = auth.uid())
            with check (user_id = auth.uid());
        create policy assessments_owner_rw on assessments
            using (user_id = auth.uid())
            with check (user_id = auth.uid());
        create policy self_competency_ratings_owner_rw on self_competency_ratings
            using (user_id = auth.uid())
            with check (user_id = auth.uid());
        create policy development_plans_owner_rw on development_plans
            using (user_id = auth.uid())
            with check (user_id = auth.uid());
        create policy sponsors_owner_rw on sponsors
            using (user_id = auth.uid())
            with check (user_id = auth.uid());
        create policy evidence_owner_rw on evidence
            using (user_id = auth.uid())
            with check (user_id = auth.uid());
        """
    )


def downgrade():
    op.execute(
        """
        drop table if exists evidence;
        drop table if exists sponsors;
        drop table if exists development_plans;
        drop table if exists self_competency_ratings;
        drop table if exists assessments;
        drop table if exists progress_events;
        drop table if exists user_competencies;
        drop table if exists profiles;

        drop table if exists learning_resources;
        drop table if exists role_cards;
        drop table if exists role_adjacency;
        drop table if exists role_competencies;
        drop table if exists competencies;
        drop table if exists roles;
        """
    )
