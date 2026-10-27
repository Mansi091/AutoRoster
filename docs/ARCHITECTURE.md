# Architecture

AutoRoster is split into a React client and a FastAPI service.

~~~text
Supervisor browser
  ├── React + Vite + Clerk + Leaflet
  └── /api requests
        └── FastAPI
              ├── Clerk session validation
              ├── roster ingestion and pickup resolution
              ├── Geoapify / LocationIQ geocoding
              ├── OSRM road matrix and route geometry
              ├── OR-Tools assignment and stop-order search
              ├── Excel export
              └── Supabase private roster history
~~~

## Request flow

1. The supervisor signs in with Clerk. The frontend sends the Clerk session token with protected API requests.
2. The supervisor selects and confirms a drop-off facility and shift timing.
3. FastAPI receives the workbook and the confirmed facility. Pickup coordinates are resolved from the stop master first and geocoding providers for unresolved names.
4. The planner creates OSRM travel-time and distance matrices, searches for route plans with OR-Tools, and validates final routes with OSRM.
5. Route summaries, geometry, employee assignments, and review reasons are returned to the frontend.
6. When roster history is saved, the backend stores workbook metadata in the Supabase table and the workbook in a private Storage bucket under the Clerk user ID.

## Main directories

- backend/app/api — HTTP endpoints
- backend/app/core — configuration, logging, and Clerk verification
- backend/app/etl — Excel ingestion, coordinate resolution, and road matrices
- backend/app/routing — route scheduling and validation
- backend/app/services — planning workflow
- backend/app/export — workbook generation
- frontend/src — supervisor interface and map
- supabase/migrations — database setup

Operational values are read from the root .env. The frontend only receives the Clerk publishable key through frontend/.env.
