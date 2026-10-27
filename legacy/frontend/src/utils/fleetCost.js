/**
 * Fleet cost model: turns route distances into the numbers a transport
 * manager actually cares about - kilometres, litres of diesel, rupees.
 *
 * IMPORTANT: these are ESTIMATES. Fuel consumption in real service depends on
 * traffic, idling, gradient, AC load and driving style. Replace the constants
 * below with your own fleet's measured data before quoting figures to a client.
 */

// A 32-seater bus on a mixed highway + city shift.
export const FUEL = {
  // km/litre when running empty (pickup route + deadhead return)
  emptyKmPerLitre: 5.0,
  // additional consumption penalty at full load (32 pax)
  loadedPenaltyKmPerLitre: 1.2,
  pricePerLitre: 92, // ₹ per litre, diesel
  currency: "₹",
  // fixed daily cost per bus dispatched (depreciation, driver, insurance, permits)
  fixedCostPerBusPerDay: 1200,
};

/**
 * Company bus specification. Numbers are estimates; replace with measured
 * fleet rates before using the cost model operationally.
 */
export const VEHICLE_CLASSES = [
  {
    id: "bus-32",
    label: "32-Seater Bus",
    capacity: 32,
    fixedCostPerDay: 1200,
    emptyKmPerLitre: 5.0,
    loadedPenaltyKmPerLitre: 1.2,
  },
];

const DEFAULT_VEHICLE = VEHICLE_CLASSES[0];

/** Returns the company's 32-seat bus specification for route cost estimates. */
export function chooseVehicle(passengers, vehicleClasses = VEHICLE_CLASSES) {
  const pax = passengers ?? 0;
  const bus = vehicleClasses[0] ?? DEFAULT_VEHICLE;
  return { ...bus, overloaded: pax > bus.capacity };
}

/**
 * The vehicle specifications available to a fleet.
 *
 * @param {number} maxCapacity largest seat count the company actually owns
 */
export function availableVehicleClasses() {
  return VEHICLE_CLASSES;
}

/**
 * Fuel efficiency for a given passenger load, for a given vehicle class.
 */
export function kmPerLitre(passengers, capacity = 32, vehicle = DEFAULT_VEHICLE) {
  const loadRatio = capacity > 0 ? Math.min(1, passengers / capacity) : 0;
  return (
    (vehicle.emptyKmPerLitre ?? FUEL.emptyKmPerLitre) -
    (vehicle.loadedPenaltyKmPerLitre ?? FUEL.loadedPenaltyKmPerLitre) * loadRatio
  );
}

/**
 * Litres burnt by one bus on one leg.
 */
export function litresFor(km, passengers, capacity = 32, vehicle = DEFAULT_VEHICLE) {
  if (!km) return 0;
  return km / kmPerLitre(passengers, capacity, vehicle);
}

/**
 * Full fleet cost breakdown.
 *
 * @param {Array}  routes     clustered routes (each with passengers_assigned, and
 *                             optionally road_km when a road matrix was used)
 * @param {object} opts
 * @param {number} opts.totalEmployees
 * @param {boolean} opts.assignVehicles  pick the cheapest vehicle class per route
 * @returns {object} cost summary
 */
export function computeFleetCost(routes, opts = {}) {
  const totalEmployees = opts.totalEmployees ?? 0;
  const assignVehicles = opts.assignVehicles !== false;
  const vehicleClasses = availableVehicleClasses();

  const perBus = routes.map((r) => {
    const km = r.road_km ?? r.total_km;
    const pax = r.passengers_assigned ?? 0;
    const vehicle = assignVehicles
      ? chooseVehicle(pax, vehicleClasses)
      : { ...DEFAULT_VEHICLE };
    const loadEfficiency = kmPerLitre(pax, vehicle.capacity, vehicle);

    // One-way run (plant -> first pickup -> ... -> plant)
    const runLitres = litresFor(km, pax, vehicle.capacity, vehicle);
    // Deadhead return: bus runs empty back to its starting point
    const returnLitres = litresFor(km, 0, vehicle.capacity, vehicle);
    const dailyLitres = runLitres + returnLitres;

    return {
      bus_id: r.bus_id,
      shift: r.shift,
      corridor: r.corridor,
      vehicle: vehicle.label,
      vehicle_id: vehicle.id,
      vehicle_capacity: vehicle.capacity,
      passengers: pax,
      load_pct: Number(((pax / vehicle.capacity) * 100).toFixed(0)),
      one_way_km: km,
      daily_km: Number((km * 2).toFixed(1)),
      km_per_litre: Number(loadEfficiency.toFixed(2)),
      daily_litres: Number(dailyLitres.toFixed(1)),
      daily_fuel_cost: Number((dailyLitres * FUEL.pricePerLitre).toFixed(0)),
      daily_fixed_cost: vehicle.fixedCostPerDay,
      daily_total_cost: Number(
        (dailyLitres * FUEL.pricePerLitre + vehicle.fixedCostPerDay).toFixed(0)
      ),
      // How many passengers we move per kilometre driven. Low = wasteful.
      pax_per_km: Number((pax / (km * 2 || 1)).toFixed(4)),
    };
  });

  const totalOneWay = perBus.reduce((s, b) => s + b.one_way_km, 0);
  const totalDailyKm = perBus.reduce((s, b) => s + b.daily_km, 0);
  const totalLitres = perBus.reduce((s, b) => s + b.daily_litres, 0);
  const totalFuelCost = perBus.reduce((s, b) => s + b.daily_fuel_cost, 0);
  const fixedCost = perBus.reduce((s, b) => s + b.daily_fixed_cost, 0);
  const totalCost = totalFuelCost + fixedCost;

  return {
    buses: routes.length,
    passengers: totalEmployees,
    total_one_way_km: Number(totalOneWay.toFixed(1)),
    total_daily_km: Number(totalDailyKm.toFixed(1)),
    total_daily_litres: Number(totalLitres.toFixed(1)),
    total_daily_fuel_cost: Math.round(totalFuelCost),
    total_fixed_cost: Math.round(fixedCost),
    total_daily_cost: Math.round(totalCost),
    cost_per_employee: totalEmployees
      ? Number((totalCost / totalEmployees).toFixed(0))
      : 0,
    avg_pax_per_km: Number(
      (totalEmployees / (totalDailyKm || 1)).toFixed(4)
    ),
    vehicle_mix: perBus.reduce((acc, b) => {
      acc[b.vehicle] = (acc[b.vehicle] || 0) + 1;
      return acc;
    }, {}),
    per_bus: perBus,
  };
}
