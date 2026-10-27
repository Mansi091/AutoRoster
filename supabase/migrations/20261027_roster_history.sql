-- Clerk handles authentication. This table stores roster metadata keyed by the
-- verified Clerk user ID; workbook files live in a private Supabase Storage bucket.
create table if not exists public.roster_history (
    id text primary key,
    clerk_user_id text not null,
    uploaded_file_name text not null default '',
    total_employees integer not null default 0,
    routed_employees integer not null default 0,
    total_routes integer not null default 0,
    destination_name text not null default '',
    shifts_summary jsonb not null default '[]'::jsonb,
    workbook_filename text not null,
    created_at timestamptz not null default now(),
    unique (clerk_user_id, workbook_filename)
);

create index if not exists roster_history_owner_created_idx
    on public.roster_history (clerk_user_id, created_at desc);

alter table public.roster_history enable row level security;

-- The backend accesses this table only after validating a Clerk session and
-- always filters by clerk_user_id. Do not add anon/authenticated public policies.
grant select, insert, delete on public.roster_history to service_role;

insert into storage.buckets (id, name, public)
values ('roster-history', 'roster-history', false)
on conflict (id) do update set public = false;
