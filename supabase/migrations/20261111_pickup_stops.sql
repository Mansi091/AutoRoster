-- Pickup stop memory: one row per real place, every roster spelling mapped to it,
-- and a history of every change. The backend reads/writes these tables with the
-- service role only (same pattern as roster_history). Run once in the SQL editor.

create table if not exists public.pickup_stops (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    lat double precision not null,
    lon double precision not null,
    -- confirmed: a person checked the point on a map
    -- approximate: imported or found by search, not checked yet
    -- conflict: spellings of this stop were saved at different points; pick one
    quality text not null default 'approximate'
        check (quality in ('confirmed', 'approximate', 'conflict')),
    source text not null default '',
    -- other points seen for this stop (e.g. imported spellings that disagreed)
    candidates jsonb not null default '[]'::jsonb,
    confirmed_by text,
    confirmed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (lat between -90 and 90 and lon between -180 and 180)
);

create table if not exists public.pickup_stop_aliases (
    -- normalized spelling (lower case, letters and digits only) used for lookups
    alias_key text primary key,
    -- the spelling as it appeared in a roster
    alias_text text not null,
    stop_id uuid not null references public.pickup_stops (id) on delete cascade,
    created_at timestamptz not null default now()
);

create index if not exists pickup_stop_aliases_stop_idx
    on public.pickup_stop_aliases (stop_id);

create table if not exists public.pickup_stop_changes (
    id bigint generated always as identity primary key,
    stop_id uuid references public.pickup_stops (id) on delete set null,
    action text not null,           -- created | moved | confirmed | alias_added | imported
    old_value jsonb,
    new_value jsonb,
    changed_by text not null default '',
    changed_at timestamptz not null default now()
);

create index if not exists pickup_stop_changes_stop_idx
    on public.pickup_stop_changes (stop_id, changed_at desc);

alter table public.pickup_stops enable row level security;
alter table public.pickup_stop_aliases enable row level security;
alter table public.pickup_stop_changes enable row level security;

-- Backend only (service role). Do not add anon/authenticated public policies.
grant select, insert, update, delete on public.pickup_stops to service_role;
grant select, insert, update, delete on public.pickup_stop_aliases to service_role;
grant select, insert on public.pickup_stop_changes to service_role;
