# Route rules

Operational rules are configurable in the project root .env. Values in .env.example are starting defaults, not hard-coded guarantees.

- Bus capacity defaults to 32 seats.
- The target ride time defaults to 60 minutes.
- The hard maximum ride time defaults to 70 minutes.
- Shift arrival deadlines and the early-arrival buffer determine the schedule.
- Stop dwell, passenger boarding time, pickup merge radius, service radius, and solver search time can be adjusted.
- Road distances, drive times, and displayed route geometry are sourced from OSRM.
- The planner uses OR-Tools search and checks final plans against OSRM. It returns a best feasible plan found within the configured search budget; it does not prove a global optimum.
- Unverified pickup locations and unreachable road legs are not assigned guessed coordinates or times; affected employees/routes are returned for review.
- Live traffic is not included in the current ETA model.

Supervisors choose the facility and shift for a planning run. The web app does not assume a selected facility before the supervisor confirms one.
