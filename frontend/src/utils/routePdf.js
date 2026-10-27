/**
 * Route Sheet PDF Generator
 *
 * Produces a driver-ready "route sheet" for a single bus: the ordered list of
 * ordered pickup points, estimated time from each pickup to the drop, and the
 * employees waiting at each stop.
 */
import { jsPDF } from "jspdf";

const PAGE_W = 210; // A4 portrait width in mm
const PAGE_H = 297;
const MARGIN = 14;
const CONTENT_W = PAGE_W - MARGIN * 2;

/**
 * @param {object} route   - one entry from clustering.routes
 * @param {object} opts
 * @param {object} opts.destination
 * @param {object} opts.roadStats  - roadStatsMap[bus_id] from RouteMap (may be undefined)
 * @param {string} opts.generatedOn
 * @param {string} opts.companyName
 */
export function exportRoutePdf(route, opts = {}) {
  const { destination, roadStats = null, companyName = "AutoRoster" } = opts;
  const doc = new jsPDF({ unit: "mm", format: "a4" });

  const stat = roadStats;
  const totalMin = stat
    ? stat.durationMinutes
    : Math.round(route.total_minutes || (route.total_km / 30) * 60);

  const stops = route.stops || [];
  const totalPax = route.passengers_assigned ?? 0;
  const longestRide = Math.round(route.longest_ride_minutes || 0);

  // ---------------------------------------------------------------- header
  let y = MARGIN;

  doc.setFillColor(15, 23, 42);
  doc.rect(0, 0, PAGE_W, 26, "F");

  doc.setTextColor(255, 255, 255);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(16);
  doc.text(`${route.bus_id}`, MARGIN, 12);

  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  doc.setTextColor(203, 213, 225);
  doc.text(
    `${route.shift || ""}  •  ${route.corridor || "Central"} Corridor  •  ${companyName} Route Sheet`,
    MARGIN,
    18.5
  );

  // Right side of the header: date
  doc.setFontSize(8);
  doc.setTextColor(148, 163, 184);
  const dateStr =
    opts.generatedOn ||
    new Date().toLocaleDateString("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  doc.text(dateStr, PAGE_W - MARGIN, 12, { align: "right" });
  doc.text("Driver copy", PAGE_W - MARGIN, 18.5, { align: "right" });

  y = 34;

  // ------------------------------------------------------------ summary row
  const stats = [
    ["PASSENGERS", String(totalPax)],
    ["STOPS", String(stops.length)],
    ["EST. DRIVE", `~${totalMin} min`],
    ["LONGEST RIDE", `~${longestRide} min`],
  ];
  const boxW = (CONTENT_W - 6) / 4;
  stats.forEach((s, i) => {
    const x = MARGIN + i * (boxW + 2);
    doc.setFillColor(241, 245, 249);
    doc.roundedRect(x, y, boxW, 16, 1.5, 1.5, "F");
    doc.setFont("helvetica", "normal");
    doc.setFontSize(6.5);
    doc.setTextColor(100, 116, 139);
    doc.text(s[0], x + 3, y + 5.5);
    doc.setFont("helvetica", "bold");
    doc.setFontSize(11);
    doc.setTextColor(15, 23, 42);
    doc.text(s[1], x + 3, y + 12);
  });

  y += 22;

  if (route.ride_time_status === "EXCEEDS_LIMIT") {
    doc.setFillColor(255, 237, 213);
    doc.roundedRect(MARGIN, y, CONTENT_W, 10, 1.5, 1.5, "F");
    doc.setFont("helvetica", "bold");
    doc.setFontSize(7.5);
    doc.setTextColor(154, 52, 18);
    doc.text(
      "TIME LIMIT EXCEEDED: this route needs review; the longest pickup-to-drop ride is over 70 minutes.",
      MARGIN + 3,
      y + 6.5
    );
    y += 14;
  }

  // ------------------------------------------------- origin / destination
  doc.setFillColor(254, 242, 242);
  doc.roundedRect(MARGIN, y, CONTENT_W, 14, 2, 2, "F");
  doc.setFont("helvetica", "bold");
  doc.setFontSize(8);
  doc.setTextColor(185, 28, 28);
  doc.text("ROUTE", MARGIN + 3, y + 5.5);

  doc.setFont("helvetica", "normal");
  doc.setFontSize(8);
  doc.setTextColor(15, 23, 42);
  const from = stops.length ? stops[0].stop_name : "—";
  const to = destination?.name || route.end_stop || "Drop Location";
  doc.text(`Start: ${from}`, MARGIN + 3, y + 10.5);
  doc.text(
    `Drop: ${to}${destination ? `  (${destination.lat}, ${destination.lon})` : ""}`,
    MARGIN + 3 + CONTENT_W / 2,
    y + 10.5
  );

  y += 20;

  // ------------------------------------------------------------ table head
  const COLS = [
    { label: "#", w: 9, align: "center" },
    { label: "PICKUP POINT", w: 55, align: "left" },
    { label: "NEXT LEG", w: 18, align: "right" },
    { label: "RIDE TO DROP", w: 21, align: "right" },
    { label: "PAX", w: 10, align: "center" },
    { label: "BOARDING", w: 69, align: "left" },
  ];
  let colX = [];
  let acc = MARGIN;
  COLS.forEach((c) => {
    colX.push(acc);
    acc += c.w;
  });

  const drawHead = (yy) => {
    doc.setFillColor(241, 245, 249);
    doc.rect(MARGIN, yy, CONTENT_W, 8, "F");
    doc.setFont("helvetica", "bold");
    doc.setFontSize(6.5);
    doc.setTextColor(71, 85, 105);
    COLS.forEach((c, i) => {
      const tx = colX[i] + (c.align === "right" ? c.w - 2 : c.align === "center" ? c.w / 2 : 2);
      doc.text(c.label, tx, yy + 5.2, { align: c.align });
    });
    return yy + 8;
  };

  y = drawHead(y);

  // ------------------------------------------------------------ table rows
  doc.setFont("helvetica", "normal");

  stops.forEach((s, idx) => {
    const paxCount = (s.employees || []).length;
    const names = (s.employees || [])
      .map((e) => e.employee_name || "")
      .filter(Boolean)
      .join(", ");

    const rowH = 11;
    // keep a row together across page breaks
    if (y + rowH > PAGE_H - 22) {
      doc.addPage();
      y = MARGIN;
      y = drawHead(y);
    }

    // zebra stripe
    if (idx % 2 === 1) {
      doc.setFillColor(249, 250, 251);
      doc.rect(MARGIN, y, CONTENT_W, rowH, "F");
    }

    const textY = y + 4.4;

    // seq badge
    doc.setFillColor(226, 232, 240);
    doc.circle(colX[0] + 4.5, y + 5.2, 2.6, "F");
    doc.setFont("helvetica", "bold");
    doc.setFontSize(7);
    doc.setTextColor(71, 85, 105);
    doc.text(String(s.seq), colX[0] + 4.5, y + 7.2, { align: "center" });

    // pickup point (wrapped)
    doc.setFont("helvetica", "bold");
    doc.setFontSize(7.5);
    doc.setTextColor(15, 23, 42);
    const stopLines = doc.splitTextToSize(s.stop_name || "", COLS[1].w - 4);
    doc.text(stopLines.slice(0, 2), colX[1] + 2, textY);

    // next leg
    doc.setFont("helvetica", "normal");
    doc.setFontSize(7.5);
    doc.setTextColor(15, 23, 42);
    const isLast = idx === stops.length - 1;
    // The last stop's next leg is the run back to the plant, so show the
    // real distance to the drop rather than a placeholder word.
    const nextLeg =
      isLast && s.distance_to_destination_km != null
        ? `${s.distance_to_destination_km} km`
        : `${s.distance_from_prev_km} km`;
    doc.text(nextLeg, colX[2] + COLS[2].w - 2, textY, { align: "right" });

    doc.setFont("helvetica", "bold");
    doc.setTextColor(
      s.ride_time_status === "EXCEEDS_LIMIT" ? 185 : 15,
      s.ride_time_status === "EXCEEDS_LIMIT" ? 28 : 23,
      s.ride_time_status === "EXCEEDS_LIMIT" ? 28 : 42
    );
    doc.text(
      `~${Math.round(s.ride_minutes_to_destination || 0)} min`,
      colX[3] + COLS[3].w - 2,
      textY,
      { align: "right" }
    );

    // pax count
    doc.setFont("helvetica", "bold");
    doc.setTextColor(15, 23, 42);
    doc.text(String(paxCount), colX[4] + COLS[4].w / 2, textY, { align: "center" });

    // boarding names (wrapped, muted)
    doc.setFont("helvetica", "normal");
    doc.setFontSize(6.5);
    doc.setTextColor(100, 116, 139);
    const nameLines = doc.splitTextToSize(names || "—", COLS[5].w - 4);
    doc.text(nameLines.slice(0, 2), colX[5] + 2, textY);

    // separator
    doc.setDrawColor(226, 232, 240);
    doc.setLineWidth(0.2);
    doc.line(MARGIN, y + rowH, MARGIN + CONTENT_W, y + rowH);

    y += rowH;
  });

  // ------------------------------------------------------------- totals row
  if (y + 14 > PAGE_H - 22) {
    doc.addPage();
    y = MARGIN;
  }
  y += 2;
  doc.setFillColor(15, 23, 42);
  doc.roundedRect(MARGIN, y, CONTENT_W, 13, 2, 2, "F");
  doc.setFont("helvetica", "bold");
  doc.setFontSize(8);
  doc.setTextColor(255, 255, 255);
  doc.text(
    `TOTAL: ${stops.length} pickup points  •  ${totalPax} passengers  •  Longest ride ~${longestRide} min  •  Estimated drive ~${totalMin} min`,
    PAGE_W / 2,
    y + 8,
    { align: "center" }
  );

  // --------------------------------------------------------------- footer
  const footY = PAGE_H - 12;
  doc.setDrawColor(226, 232, 240);
  doc.setLineWidth(0.3);
  doc.line(MARGIN, footY - 4, MARGIN + CONTENT_W, footY - 4);
  doc.setFont("helvetica", "normal");
  doc.setFontSize(6.5);
  doc.setTextColor(148, 163, 184);
  doc.text(
    `${companyName} • ${route.bus_id} • ${route.shift || ""} • Generated ${dateStr}`,
    MARGIN,
    footY
  );
  doc.text(
    "Static no-traffic road-time estimate; confirm the pickup points and live route in navigation.",
    PAGE_W - MARGIN,
    footY,
    { align: "right" }
  );

  const safeName = route.bus_id.replace(/[^a-z0-9]+/gi, "_");
  doc.save(`RouteSheet_${safeName}.pdf`);
}
