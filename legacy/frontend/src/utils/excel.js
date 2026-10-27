import XLSX from "xlsx-js-style";

/**
 * Maps the roster columns used for routing and final workbook exports:
 * 1. Employee ID
 * 2. Employee Name
 * 3. Shift
 * 4. Pickup Point (Bus Stop)
 * 5. Phone / Mobile Number
 * 6. Employee Type / Grade
 * 7. Route Name / Location
 */
export function detectColumns(headers) {
  const mapping = {
    employee_id: null,
    employee_name: null,
    shift: null,
    pickup_point: null,
    mobile: null,
    employee_type: null,
  };

  const headerStrs = headers.map((h) => ({ raw: h, clean: String(h).toLowerCase().trim() }));

  // 1. Employee ID
  const idMatch = headerStrs.find(
    (h) => h.clean === "emp id" || h.clean === "empid" || h.clean === "employee id" || h.clean.includes("id") || h.clean === "sr. no."
  );
  if (idMatch) mapping.employee_id = idMatch.raw;

  // 2. Employee Name
  const nameMatch = headerStrs.find(
    (h) => h.clean === "name" || h.clean === "employee name" || h.clean.includes("name")
  );
  if (nameMatch) mapping.employee_name = nameMatch.raw;

  // 3. Shift
  const shiftMatch = headerStrs.find(
    (h) => h.clean === "shift" || h.clean.includes("shift")
  );
  if (shiftMatch) mapping.shift = shiftMatch.raw;

  // 4. Pickup Point (Strictly Bus Stop / Pickup location, ignoring general town "location")
  const stopMatch = headerStrs.find(
    (h) =>
      h.clean === "bus stop" ||
      h.clean === "pickup point" ||
      h.clean === "pickup" ||
      h.clean.includes("bus stop") ||
      h.clean.includes("pickup") ||
      h.clean.includes("stop")
  );
  if (stopMatch) {
    mapping.pickup_point = stopMatch.raw;
  } else {
    // If no explicit "bus stop" header exists, check for address/stand
    const altStop = headerStrs.find(
      (h) => h.clean.includes("stand") || h.clean.includes("chowk") || h.clean.includes("address") || h.clean.includes("location")
    );
    if (altStop) mapping.pickup_point = altStop.raw;
  }

  // 5. Phone Number
  const mobMatch = headerStrs.find(
    (h) =>
      h.clean.includes("mob") ||
      h.clean.includes("phone") ||
      h.clean.includes("contact") ||
      h.clean.includes("cell")
  );
  if (mobMatch) mapping.mobile = mobMatch.raw;

  const typeMatch = headerStrs.find(
    (h) => ["grade", "employee type", "employment type"].includes(h.clean)
  );
  if (typeMatch) mapping.employee_type = typeMatch.raw;

  // 6. Optional secondary location column to resolve local ambiguous stops (e.g. Bhosari vs Kamshet)
  const locMatch = headerStrs.find(
    (h) => (h.clean === "location" || h.clean.includes("area") || h.clean.includes("city")) && h.raw !== mapping.pickup_point
  );
  if (locMatch) mapping.location_context = locMatch.raw;

  return mapping;
}

/**
 * Parses raw Excel file focusing only on the 5 required columns
 */
export async function parseExcelFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const data = new Uint8Array(e.target.result);
        const workbook = XLSX.read(data, { type: "array" });
        let foundRosterHeaders = false;

        for (const sheetName of workbook.SheetNames) {
          const worksheet = workbook.Sheets[sheetName];
          const rawGrid = XLSX.utils.sheet_to_json(worksheet, { header: 1, defval: "" });
          if (!rawGrid.length) continue;

          // Find the first worksheet containing roster headers, skipping title rows.
          let headerRowIdx = -1;
          let mapping = null;
          for (let r = 0; r < Math.min(20, rawGrid.length); r++) {
            const headers = rawGrid[r].map((cell) => String(cell).trim());
            const detected = detectColumns(headers);
            if (detected.employee_name && detected.pickup_point) {
              headerRowIdx = r;
              mapping = detected;
              foundRosterHeaders = true;
              break;
            }
          }
          if (headerRowIdx === -1) continue;

          const rawHeaders = rawGrid[headerRowIdx].map((cell) => String(cell).trim());
          const colIndices = {
            id: rawHeaders.indexOf(mapping.employee_id),
            name: rawHeaders.indexOf(mapping.employee_name),
            shift: rawHeaders.indexOf(mapping.shift),
            stop: rawHeaders.indexOf(mapping.pickup_point),
            mobile: rawHeaders.indexOf(mapping.mobile),
            employee_type: rawHeaders.indexOf(mapping.employee_type),
            location_context: rawHeaders.indexOf(mapping.location_context),
          };

          const standardizedRows = [];
          for (let r = headerRowIdx + 1; r < rawGrid.length; r++) {
            const row = rawGrid[r];
            if (!row || !row.length) continue;

            const id = colIndices.id !== -1 ? String(row[colIndices.id] || "").trim() : "";
            const name = colIndices.name !== -1 ? String(row[colIndices.name] || "").trim() : "";
            const shift =
              colIndices.shift !== -1
                ? String(row[colIndices.shift] || "A").trim().toUpperCase()
                : "A";
            const stop = colIndices.stop !== -1 ? String(row[colIndices.stop] || "").trim() : "";
            const mobile =
              colIndices.mobile !== -1 ? String(row[colIndices.mobile] || "").trim() : "";
            const employeeType =
              colIndices.employee_type !== -1
                ? String(row[colIndices.employee_type] || "").trim()
                : "";
            const locCtx =
              colIndices.location_context !== -1
                ? String(row[colIndices.location_context] || "").trim()
                : "";

            if (name && stop && stop.toLowerCase() !== "bus stop" && name.toLowerCase() !== "name") {
              standardizedRows.push({
                employee_id: id,
                employee_name: name,
                shift: shift.replace(/\s+/g, ""),
                pickup_point: stop,
                location_context: locCtx,
                grade: employeeType,
                mobile,
              });
            }
          }

          if (!standardizedRows.length) continue;

          resolve({
            sheetName,
            headers: rawHeaders,
            detectedMapping: mapping,
            employees: standardizedRows,
            totalRowsRead: rawGrid.length - headerRowIdx - 1,
            validEmployees: standardizedRows.length,
          });
          return;
        }

        if (foundRosterHeaders) {
          throw new Error(
            "Roster headers were found, but no employee rows had both a name and pickup point."
          );
        }
        throw new Error(
          "Could not find a header row with employee name and pickup-point columns on any worksheet. " +
            "Check for headers such as Employee Name and Bus Stop."
        );
      } catch (err) {
        reject(err);
      }
    };
    reader.onerror = () => reject(new Error("The selected Excel file could not be read."));
    reader.readAsArrayBuffer(file);
  });
}

const ROSTER_HEADERS = [
  "Sr. No.",
  "Employee Type",
  "Employee Name",
  "Shift",
  "Route Name",
  "Pickup Point",
  "Est. Ride to Drop (min)",
  "Mobile No.",
];

const HEADER_STYLE = {
  fill: { fgColor: { rgb: "FFC000" } },
  font: { bold: true, color: { rgb: "000000" } },
  alignment: { horizontal: "center", vertical: "center", wrapText: true },
  border: {
    top: { style: "thin", color: { rgb: "000000" } },
    bottom: { style: "thin", color: { rgb: "000000" } },
    left: { style: "thin", color: { rgb: "000000" } },
    right: { style: "thin", color: { rgb: "000000" } },
  },
};

const TITLE_STYLE = {
  fill: { fgColor: { rgb: "FFC000" } },
  font: { bold: true, size: 14, color: { rgb: "000000" } },
  alignment: { horizontal: "center", vertical: "center" },
};

const SECTION_STYLE = {
  fill: { fgColor: { rgb: "FFE699" } },
  font: { bold: true, color: { rgb: "000000" } },
};

const ROSTER_COLUMNS = [
  { wch: 9 },
  { wch: 18 },
  { wch: 28 },
  { wch: 10 },
  { wch: 22 },
  { wch: 42 },
  { wch: 22 },
  { wch: 18 },
];

function styleSheet(sheet, titleRow, headerRows, sectionRows = [], filter = false) {
  const columnCount = XLSX.utils.decode_range(sheet["!ref"]).e.c + 1;
  const lastColumn = columnCount - 1;
  sheet["!merges"] = sheet["!merges"] || [];
  sheet["!rows"] = sheet["!rows"] || [];

  if (titleRow !== null) {
    sheet["!merges"].push({
      s: { r: titleRow, c: 0 },
      e: { r: titleRow, c: lastColumn },
    });
    const title = sheet[XLSX.utils.encode_cell({ r: titleRow, c: 0 })];
    if (title) title.s = TITLE_STYLE;
    sheet["!rows"][titleRow] = { hpt: 26 };
  }

  sectionRows.forEach((rowIndex) => {
    const cell = sheet[XLSX.utils.encode_cell({ r: rowIndex, c: 0 })];
    if (cell) cell.s = SECTION_STYLE;
    sheet["!merges"].push({
      s: { r: rowIndex, c: 0 },
      e: { r: rowIndex, c: lastColumn },
    });
  });

  headerRows.forEach((rowIndex) => {
    for (let column = 0; column < columnCount; column += 1) {
      const cell = sheet[XLSX.utils.encode_cell({ r: rowIndex, c: column })];
      if (cell) cell.s = HEADER_STYLE;
    }
    sheet["!rows"][rowIndex] = { hpt: 22 };
  });

  if (filter) {
    sheet["!autofilter"] = {
      ref: XLSX.utils.encode_range({
        s: { r: headerRows[0], c: 0 },
        e: { r: XLSX.utils.decode_range(sheet["!ref"]).e.r, c: lastColumn },
      }),
    };
  }
}

function getRouteName(employee, route) {
  return employee.location_context || employee.area || route.corridor || "";
}

function getRosterRow(employee, serial, routeName, pickupPoint) {
  return [
    serial,
    employee.grade || employee.employee_type || "",
    employee.employee_name || "",
    employee.shift || "",
    routeName || "",
    pickupPoint || "",
    Number.isFinite(employee.ride_minutes_to_destination)
      ? Math.round(employee.ride_minutes_to_destination)
      : "",
    employee.mobile && employee.mobile !== "-" ? employee.mobile : "",
  ];
}

/**
 * Exports shift-wise route manifests plus a master roster grouped by pickup point.
 */
export function exportToExcel(
  routes,
  employees,
  filename = "Grouped_Bus_Roster.xlsx",
  options = {}
) {
  const workbook = XLSX.utils.book_new();
  const shifts = ["A", "B", "C"];
  const title = options.title || "Transport Roster";

  shifts.forEach((shift) => {
    const shiftRoutes = routes.filter(
      (route) => route.shift && route.shift.toUpperCase().includes(shift)
    );
    if (!shiftRoutes.length) return;

    const rows = [[`${title} - ${shift} Shift`]];
    const sectionRows = [];
    const headerRows = [];
    let serial = 1;

    shiftRoutes.forEach((route) => {
      sectionRows.push(rows.length);
      rows.push([
        `${route.bus_id} | ${route.passengers_assigned} employees | ${route.total_stops} pickup points`,
      ]);
      headerRows.push(rows.length);
      rows.push(ROSTER_HEADERS);

      route.stops.forEach((stop) => {
        (stop.employees || []).forEach((employee) => {
          rows.push(
            getRosterRow(
              employee,
              serial++,
              getRouteName(employee, route),
              stop.stop_name
            )
          );
        });
      });
      rows.push([]);
    });

    const sheet = XLSX.utils.aoa_to_sheet(rows);
    sheet["!cols"] = ROSTER_COLUMNS;
    styleSheet(sheet, 0, headerRows, sectionRows);
    XLSX.utils.book_append_sheet(workbook, sheet, `${shift}-Shift`);
  });

  const sortedEmployees = [...employees].sort(
    (a, b) =>
      String(a.shift || "").localeCompare(String(b.shift || "")) ||
      String(a.assigned_bus || "").localeCompare(String(b.assigned_bus || "")) ||
      String(a.pickup_point || "").localeCompare(String(b.pickup_point || "")) ||
      String(a.employee_name || "").localeCompare(String(b.employee_name || ""))
  );
  const masterHeaders = [...ROSTER_HEADERS, "Assigned Bus", "Location Status"];
  const masterRows = [
    [`${title} - All Employees`],
    masterHeaders,
    ...sortedEmployees.map((employee, index) => [
      ...getRosterRow(
        employee,
        index + 1,
        employee.location_context || employee.corridor || "",
        employee.pickup_point
      ),
      employee.assigned_bus || "",
      employee.location_status || "VERIFIED",
    ]),
  ];
  const masterSheet = XLSX.utils.aoa_to_sheet(masterRows);
  masterSheet["!cols"] = [...ROSTER_COLUMNS, { wch: 16 }, { wch: 24 }];
  styleSheet(masterSheet, 0, [1], [], true);
  XLSX.utils.book_append_sheet(workbook, masterSheet, "All");

  const pickupGroups = new Map();
  sortedEmployees.forEach((employee) => {
    const key = JSON.stringify([
      employee.shift || "",
      employee.pickup_point || "",
    ]);
    if (!pickupGroups.has(key)) pickupGroups.set(key, []);
    pickupGroups.get(key).push(employee);
  });

  const pickupRows = [[`${title} - Grouped by Pickup Point`]];
  const pickupSectionRows = [];
  const pickupHeaderRows = [];
  let pickupSerial = 1;
  pickupGroups.forEach((group) => {
    const first = group[0];
    pickupSectionRows.push(pickupRows.length);
    pickupRows.push([
      `${first.shift || "Unassigned Shift"} | ${first.pickup_point || "Unknown Pickup Point"} | Bus(es): ${[...new Set(group.map((employee) => employee.assigned_bus).filter(Boolean))].join(", ") || "Unassigned"} | ${group.length} employees`,
    ]);
    pickupHeaderRows.push(pickupRows.length);
    pickupRows.push(masterHeaders);
    group.forEach((employee) => {
      pickupRows.push([
        ...getRosterRow(
          employee,
          pickupSerial++,
          employee.location_context || employee.corridor || "",
          employee.pickup_point
        ),
        employee.assigned_bus || "",
        employee.location_status || "VERIFIED",
      ]);
    });
    pickupRows.push([]);
  });

  const pickupSheet = XLSX.utils.aoa_to_sheet(pickupRows);
  pickupSheet["!cols"] = [...ROSTER_COLUMNS, { wch: 16 }, { wch: 24 }];
  styleSheet(pickupSheet, 0, pickupHeaderRows, pickupSectionRows);
  XLSX.utils.book_append_sheet(workbook, pickupSheet, "Pickup Points");

  const unverifiedEmployees = sortedEmployees.filter(
    (employee) => employee.location_status === "LOCATION_UNVERIFIED"
  );
  if (unverifiedEmployees.length) {
    const reviewRows = [
      [`${title} - Unverified Pickup Locations`],
      [...masterHeaders, "Resolution Note"],
      ...unverifiedEmployees.map((employee, index) => [
        ...getRosterRow(
          employee,
          index + 1,
          employee.location_context || "",
          employee.pickup_point
        ),
        employee.assigned_bus || "Unassigned",
        employee.location_status,
        employee.resolution_reason || "",
      ]),
    ];
    const reviewSheet = XLSX.utils.aoa_to_sheet(reviewRows);
    reviewSheet["!cols"] = [
      ...ROSTER_COLUMNS,
      { wch: 16 },
      { wch: 24 },
      { wch: 60 },
    ];
    styleSheet(reviewSheet, 0, [1], [], true);
    XLSX.utils.book_append_sheet(workbook, reviewSheet, "Location Review");
  }

  XLSX.writeFile(workbook, filename);
}
