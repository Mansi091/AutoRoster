-- Let the stop memory also keep roster stops that could not be placed on the map yet
-- (quality = 'needs_location', no coordinates), so they form one permanent to-do list.
-- Run once in the SQL editor after 20261111_pickup_stops.sql.

alter table public.pickup_stops alter column lat drop not null;
alter table public.pickup_stops alter column lon drop not null;

alter table public.pickup_stops drop constraint if exists pickup_stops_quality_check;
alter table public.pickup_stops add constraint pickup_stops_quality_check
    check (quality in ('confirmed', 'approximate', 'conflict', 'needs_location'));

-- Only a 'needs_location' stop may be without coordinates.
alter table public.pickup_stops drop constraint if exists pickup_stops_location_present;
alter table public.pickup_stops add constraint pickup_stops_location_present
    check (quality = 'needs_location' or (lat is not null and lon is not null));
