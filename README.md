# AutoRoster

AutoRoster helps a supervisor turn an employee Excel roster into bus routes that can be reviewed on a map and exported as a dispatch workbook.

## What happens in the app

1. A supervisor signs in through Clerk.
2. They search for and confirm the drop-off facility, then choose the shift and its start time.
3. They upload an Excel roster.
4. The backend matches pickup names against its stop master, then uses Geoapify and LocationIQ to find unresolved pickup coordinates. Unverified stops are kept out of route planning for review.
5. OSRM supplies road distances, travel times, and route geometry. OR-Tools searches for feasible bus assignments and stop orders.
6. The supervisor reviews the result and downloads the workbook. Saved roster history is private to the signed-in Clerk user and stored in Supabase.

The facility is chosen by the supervisor for each plan; the web app does not assume a default destination.

## Project layout

- backend/app — FastAPI API, authentication checks, geocoding, route planning, exports, and history endpoints
- run_route_planner.py — multi-shift planner using OR-Tools and OSRM
- frontend — React and Vite interface with Leaflet maps and Clerk sign-in
- data/reference and data/seed — optional pickup-name references and stop master seed data
- supabase/migrations — database and private storage setup for roster history
- docs — architecture and current route rules
- legacy and root-level CLI scripts — previous pipeline versions kept for reference

Runtime data, uploaded employee rosters, generated workbooks, local caches, and credentials are excluded from Git.

## Requirements

- Python 3.11, 3.12, or 3.13
- Node.js 20.19+ or 22.12+ for the Vite frontend
- API credentials for the services you enable
- A Supabase project for private roster history

## Run locally

### 1. Configure the backend

From the project root, create and activate a virtual environment, install dependencies, and copy the environment template:

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
~~~

Open the root .env and add the credentials described below. Start the API:

~~~powershell
python app.py
~~~

The backend listens on http://127.0.0.1:5000. Interactive API documentation is at http://127.0.0.1:5000/docs, and the health endpoint is http://127.0.0.1:5000/api/health.

### 2. Configure and run the frontend

In a second terminal:

~~~powershell
cd frontend
npm install
Copy-Item .env.example .env
~~~

Add the Clerk publishable key to frontend/.env, then start Vite:

~~~powershell
npm run dev
~~~

Open http://localhost:5173. Vite proxies /api requests to the backend on port 5000.

## Environment variables

Use the root .env for backend-only values. Use frontend/.env only for frontend values.

### Clerk

Create a Clerk application and use its Development keys locally:

- Put the Clerk Secret Key in the root .env as CLERK_SECRET_KEY.
- Put the Publishable Key in frontend/.env as VITE_CLERK_PUBLISHABLE_KEY.
- CLERK_JWT_KEY is optional; CLERK_AUTHORIZED_PARTIES should include the local frontend origin, as shown in .env.example.

Never put a Clerk Secret Key or any other server secret in a Vite variable or commit it.

### Location and routing services

- GOOGLE_MAPS_API_KEY — Google Places search for drop-off facilities. Enable Places API (New) for the key.
- GEOAPIFY_API_KEY and LOCATIONIQ_API_KEY — pickup geocoding and candidate lookup.
- GEMINI_API_KEY — optional text cleanup for pickup searches.
- OSRM_TABLE_URL and OSRM_ROUTE_URL — road matrix and route geometry endpoints; public OSRM URLs are configured by default and may have usage limits or downtime.

Keep provider keys in the root .env. The app does not retrieve Google Maps results by scraping the Google Maps website.

### Supabase roster history

Set SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, and SUPABASE_ROSTER_BUCKET in the root .env. The service-role key is backend-only and must never be exposed to the browser.

Apply supabase/migrations/20261027_roster_history.sql once in the Supabase SQL Editor. It creates the roster_history table with row-level security enabled and a private roster-history storage bucket. The API validates the Clerk session and scopes history operations to that Clerk user.

## Route rules and practical limits

Bus capacity, target ride time, hard ride-time limit, shift arrival deadlines, stop dwell time, merge radius, and solver search time are configurable in the root .env.example and backend/app/core/config.py. The example settings use 32 seats, a 60-minute target, and a 70-minute hard maximum.

The planner uses OSRM road estimates and OR-Tools local search, then validates route geometry and timing against OSRM. It returns the best feasible plan found within its configured search budget; it is a heuristic and does not prove a globally optimal fleet or route plan. Travel estimates do not include live traffic. If coordinates cannot be verified or no feasible road route is available, the planner reports those employees for review instead of inventing coordinates or travel times.

## Data and privacy

Do not commit employee rosters, exports, local geocoding caches, login data, supervisor records, drop-off selections, or .env files. Root .gitignore excludes these local files. Supabase roster history is private and scoped per Clerk user.

No license file is currently included.
