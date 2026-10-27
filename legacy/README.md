# Dynamic Employee Bus Routing & Fleet Optimization System

An end-to-end intelligent transportation and bus routing platform designed for corporate plants and industrial facilities (e.g., Talegaon MIDC / JCB India Plant). The system transforms raw employee transportation rosters into optimized bus allocations, minimizes total diesel consumption and fleet requirements, ensures zero zig-zagging via 2-opt shortest-path routing, and renders exact turn-by-turn paved road network geometries via OSRM and Leaflet.

---

## 1. System Architecture

```mermaid
flowchart TD
    subgraph Ingestion ["1. Data Ingestion & Normalization"]
        A[Raw Transport Excel File] --> B[Auto-Header Detector]
        B --> C[Column Mapping: ID, Name, Shift, Stop, Mobile, Location]
        C --> D[Shift Normalizer: Merges A+B & A+B+C into Shift A]
    end

    subgraph Geocoding ["2. Geocoding & Coordinate Resolution Engine"]
        D --> E[Multi-Tier Coordinate Resolver]
        E --> F1[(Verified Coordinate Master)]
        E --> F2[(Geoapify Cache / Live Search)]
        F1 & F2 --> G[Verified Coordinates or LOCATION_UNVERIFIED]
    end

    subgraph Corridor ["3. Highway Corridor Bucketing"]
        G --> H{Geographic Highway Classification}
        H -->|West Highway NH48| I1[Kamshet -> Vadgaon -> Talegaon -> Ambi]
        H -->|East Highway NH60| I2[Khed -> Chakan -> Moshi -> Bhosari]
        H -->|South Highway PCMC| I3[Pimpri -> Chinchwad -> Nigdi -> Ravet]
    end

    subgraph Optimization ["4. 32-Seater Fleet Optimizer & TSP Sequencing"]
        I1 & I2 & I3 -->         J[Pickup-Point Proximity Grouping]
        J --> K[No Employee-Count Target | Hard Capacity: 32]
        K --> L[60-Minute Ride Target | 70-Minute Maximum]
        L --> M[Minimize Estimated Passenger Ride Time]
        M --> N[Ordered Pickup Points -> Drop Location]
    end

    subgraph RoadEngine ["5. Real Road Network Engine (OSRM)"]
        O --> P[OSRM Driving Graph Traversal Service]
        P --> Q[OpenStreetMap Highway & Street Graph Snapping]
        Q --> R[Exact Turn-by-Turn Road Polylines & True Driving KM / Time]
    end

    subgraph Presentation ["6. Presentation & Export Layer"]
        R --> S[Interactive React + Leaflet Map: Single Bus Focus]
        O --> T[Executive KPI Dashboard]
        O --> U[Shift-Wise & Location-Grouped Excel Exporter]
    end
```

---

## 2. Key Architectural Modules

### A. Intelligent Excel Parser (`excel.js`)
* **Flexible Column Detection:** Automatically identifies headers (`Name`, `Shift`, `Bus Stop`, `Mobile`, `Location`) across various sheet designs without requiring fixed templates.
* **Shift Normalization:** Consolidates multi-shift tags (`A+B`, `A+B+C`) into **Shift A** for consistent dispatching.
* **Area Disambiguation:** Extracts secondary location context (e.g., distinguishing Ambi Chowk in Talegaon from Ambi Gaon or Chakan).

### B. High-Precision Coordinate Resolution (`geo.js` & `master_coords.json`)
* **Verified Local Master:** 400+ hand-verified GPS coordinates for stops across Pune, PCMC, Talegaon, Chakan, Khed, Nigdi, and Ravet.
* **Verified Lookup Strategy:** Resolves coordinate-master matches and spelling/alias corrections, then checks the Geoapify cache/service for remaining pickup points. Geocoded candidates must be in the Pune service area, high-confidence, and textually relevant to the requested stop; ambiguous names are marked `LOCATION_UNVERIFIED` and excluded from routing rather than assigned guessed coordinates. The optional Python auto-resolver uses the same checks and does not approve approximate fallback coordinates.
* **Live Search Configuration:** Live lookups use `GEOAPIFY_API_KEY` from the project `.env`; without a key, cached/master matches still work and unresolved locations remain unverified.

### C. Highway Corridor Bucketing (`geo.js`)
Stops are clustered into non-intersecting arterial highways leading to JCB India Ltd (default Google Maps pin: `18.7765871, 73.6947478`):
* **West Corridor:** Lonavala &rarr; Kamshet &rarr; Vadgaon &rarr; Talegaon &rarr; Plant.
* **East Corridor:** Khed (Rajgurunagar) &rarr; Chakan &rarr; Moshi &rarr; Plant.
* **South Corridor:** New Sangvi &rarr; Pimpri &rarr; Chinchwad &rarr; Akurdi &rarr; Nigdi &rarr; Ravet &rarr; Plant.

### D. Pickup-Point Grouping & Ride-Time Optimization
* **No Employee-Count Target:** Pickup points are grouped by proximity and estimated drive time; even one employee at a distant verified stop can be assigned a dedicated bus.
* **32-Seat Hard Limit:** Passenger count only constrains physical bus capacity; it is not a route-size target.
* **Passenger Ride-Time Objective:** The optimizer minimizes the sum of estimated pickup-to-drop ride minutes. Candidate additions use a fast nearest-neighbour estimate; final routes with 14 or fewer stops use an exact Dijkstra search over pickup-order states, while larger routes use nearest-neighbour ordering with 2-opt refinement.
* **Ride-Time Rule:** Routes aim to keep each employee's estimated time from their pickup to JCB within 60 minutes. Grouping allows up to 70 minutes; a single distant stop that itself exceeds 70 minutes remains assigned but is flagged for review.
* **No-Traffic Estimate:** OSRM road-network durations are static estimates without live traffic. Before a road matrix is available, the UI uses a rough 30 km/h straight-line estimate. The driver sheet provides ordered pickup points and estimated ride times; live-traffic navigation is a later phase.

### E. OSRM Road-Network Routing (`routing.js`)
* **Real Street Network:** Uses Open Source Routing Machine (OSRM) Contraction Hierarchies to calculate routes along real paved roads, flyovers, and turns instead of straight-line aerial displacement.
* **Driving Metrics:** Delivers true odometer driving distance and realistic travel duration.
* **Client Caching:** Retains computed road geometries in memory for fast switching between buses.

### F. Clean Excel Roster Generator (`excel.js`)
* **Shift-Wise Tabs:** Generates separate sheets (`A-Shift`, `B-Shift`, `C-Shift`) in optimized bus-stop order.
* **Pickup-Point View:** Adds a separate `Pickup Points` sheet grouping employees by shift and pickup point, with assigned bus information.
* **Master Roster:** Adds an `All` sheet containing every assigned employee in one searchable list.
* **Location Review:** Preserves employees whose pickup locations could not be verified, with their `LOCATION_UNVERIFIED` status and reason, without including them in route optimization.
* **Roster Fields:** Includes `Sr. No.`, `Employee Type`, `Employee Name`, `Shift`, `Route Name`, `Pickup Point`, and `Mobile No.`; assigned bus is also included in the summary sheets.
* **Formatting:** Applies readable section banners, highlighted headers, column widths, and filters to the master sheet.

---

## 3. Project Structure

```
bus_routing_project/
├── frontend/                                # Interactive React + Vite Web Application
│   ├── src/
│   │   ├── components/
│   │   │   └── RouteMap.jsx                 # Leaflet map with OSRM real road polylines
│   │   ├── utils/
│   │   │   ├── excel.js                     # Smart parser & shift-wise Excel generator
│   │   │   ├── geo.js                       # Proximity grouping and passenger ride-time optimization
│   │   │   ├── routing.js                   # OSRM road network API service
│   │   │   ├── roadMatrix.js                # OSRM /table distance+duration matrix (chunked, 100-coord cap)
│   │   │   ├── fleetCost.js                 # Vehicle classes, diesel model & daily cost per bus
│   │   │   ├── routePdf.js                  # jsPDF driver route sheet (origin -> stops -> drop)
│   │   │   └── master_coords.json           # Verified GPS database (400+ stops)
│   │   ├── App.jsx                          # Main UI with KPI cards, file upload & tabs
│   │   └── index.css                        # Modern styling system
│   ├── package.json
│   └── vite.config.js
├── src/                                     # Python Data Pipeline & Supervisor Review Tools
│   ├── config.py                            # Distance limits, capacity & geocoding config
│   ├── master.py                            # Pickup Point Master loader
│   ├── step1_clean.py                       # Data sanitization & cleaning log
│   ├── step2_pickup_points.py               # Unique stop extraction & alias detection
│   ├── step3_geocode.py                     # Geocoding candidate search
│   ├── step4_master.py                      # Supervisor coordinate approvals
│   ├── step5_distances.py                   # Distance matrix generation
│   ├── step6_grouping.py                    # Legacy shift grouping
│   └── step7_final_workbook.py              # Supervisor review workbook
├── data/
│   ├── input/MAIN_SHEET_TWO_RAW_DATA.xlsx   # Source employee transport dataset
│   └── manual/pickup_point_master.xlsx      # Manual supervisor review sheet
├── app.py                                   # Python Flask API backend
├── run_pipeline.py                          # 7-step Python CLI runner
├── pyproject.toml                           # uv-managed dependencies (Python 3.13)
├── uv.lock                                  # Reproducible dependency lock
├── .python-version                          # Pins the interpreter to 3.13
├── requirements.txt                         # pip-compatible fallback
└── tests/test_pipeline.py                   # unittest suite (16 tests)
```

---

## 4. Quickstart & Installation

### Prerequisites
* **Node.js** (v18+)
* **Python** (3.11–3.13) — the project pins `<3.14`
* **[uv](https://docs.astral.sh/uv/)** (recommended) — or `venv` + `pip` if you prefer

### 0. Setting up the Python environment (recommended: `uv`)

The repo is pinned to **Python 3.13** (`.python-version`) because `pandas`/`numpy` wheels for 3.14 are still catching up, and the pipeline is tested against 3.13.

**With `uv` (fastest) — one command does everything:**
```bash
uv sync                    # creates .venv/ from pyproject.toml + uv.lock
uv run python app.py       # run anything inside the venv, no activation needed
uv run python -m unittest discover -s tests -v
```

`uv sync` reads `.python-version` and `uv.lock`, so every developer and every
CI run gets the exact same interpreter and package versions.

**With `uv` without the lock file:**
```bash
uv venv --python 3.13
uv pip install -r requirements.txt
```

**With standard `venv` + `pip`:**
```bash
python3.13 -m venv .venv
source .venv/Scripts/activate   # Windows (Git Bash)
.venv\Scripts\activate          # Windows (PowerShell)
pip install -r requirements.txt
```

> The default `python` on this machine is **3.14**, which is *not* supported — always use 3.13
> (`.venv\Scripts\python.exe` once synced, or `uv run`).

**Secrets:** the Geoapify key lives in `.env` at the project root (git-ignored).
`src/config.py` loads it automatically via `python-dotenv`, so `uv run python run_pipeline.py`
works with no extra setup. A real environment variable always overrides the `.env` value.

### 1. Launching the Interactive Web Application (Frontend)
```bash
cd frontend
npm install
npm run dev
```
Open **`http://localhost:5173/`** in your browser.
* Click **"Choose Excel File"** to upload your employee roster (`.xlsx`).
* Switch between buses using the dropdown to inspect real driving road routes.
* Click **"Download route sheet"** on any bus to export a printable driver PDF.
* Click **"Export Excel"** to download the clean, shift-wise roster.

### 2. Launching the Backend API (Optional)
```bash
uv run python app.py      # or: .venv\Scripts\python.exe app.py
```
Runs on `http://127.0.0.1:5000/`.

### 3. Running the Python Pipeline
```bash
uv run python run_pipeline.py --skip-lookup
```
Generates intermediate supervisor audit workbooks inside `output/`.

---

## 5. Technology Stack
* **Frontend:** React 19, Leaflet, Vite, SheetJS (`xlsx`), jsPDF, Lucide Icons
* **Routing Engine:** Open Source Routing Machine (OSRM), OpenStreetMap Tiles
* **Algorithms:** Proximity-based same-shift grouping, 32-seat hard cap, 60-minute ride target / 70-minute maximum, passenger ride-time weighted Dijkstra pickup ordering (up to 14 stops), 2-opt refinement for larger routes, OSRM no-traffic road-time matrix
* **Backend / Scripting:** Python 3.13, Flask, Pandas, OpenPyXL
* **Environment:** `uv` (with `pyproject.toml` + `uv.lock` for reproducible installs)
