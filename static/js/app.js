let currentData = null;
let leafletMap = null;
let mapLayers = [];

const ROUTE_COLORS = [
    "#3B82F6", "#10B981", "#8B5CF6", "#F59E0B", "#EC4899",
    "#06B6D4", "#6366F1", "#14B8A6", "#F97316"
];

document.addEventListener("DOMContentLoaded", () => {
    setupEventListeners();
});

function setupEventListeners() {
    const fileInput = document.getElementById("fileInput");
    const dropzone = document.getElementById("dropzone");
    const btnLoadDefault = document.getElementById("btnLoadDefault");
    const btnExportExcel = document.getElementById("btnExportExcel");
    const employeeSearch = document.getElementById("employeeSearch");

    // Dropzone drag & drop
    dropzone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropzone.classList.add("dragover");
    });
    dropzone.addEventListener("dragleave", () => {
        dropzone.classList.remove("dragover");
    });
    dropzone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropzone.classList.remove("dragover");
        if (e.dataTransfer.files.length) {
            uploadFile(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener("change", () => {
        if (fileInput.files.length) {
            uploadFile(fileInput.files[0]);
        }
    });

    btnLoadDefault.addEventListener("click", () => {
        loadCurrentData();
    });

    btnExportExcel.addEventListener("click", () => {
        exportExcel();
    });

    employeeSearch.addEventListener("input", (e) => {
        filterEmployees(e.target.value);
    });

    // Tab switching
    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".tab-pane").forEach(p => p.classList.add("hidden"));
            btn.classList.add("active");
            const target = document.getElementById(btn.dataset.tab);
            target.classList.remove("hidden");

            if (btn.dataset.tab === "tab-map" && leafletMap) {
                setTimeout(() => { leafletMap.invalidateSize(); }, 200);
            }
        });
    });

    // Shift filter chips
    document.querySelectorAll(".chip-filter").forEach(chip => {
        chip.addEventListener("click", () => {
            document.querySelectorAll(".chip-filter").forEach(c => c.classList.remove("active"));
            chip.classList.add("active");
            renderRoutes(chip.dataset.shift);
        });
    });
}

function showLoading(show) {
    document.getElementById("loadingIndicator").classList.toggle("hidden", !show);
    document.getElementById("resultsDashboard").classList.toggle("hidden", show);
}

function loadCurrentData() {
    showLoading(true);
    fetch("/api/load_current")
        .then(res => res.json())
        .then(data => {
            currentData = data;
            showLoading(false);
            renderDashboard(data);
        })
        .catch(err => {
            showLoading(false);
            alert("Error loading data: " + err);
        });
}

function uploadFile(file) {
    const formData = new FormData();
    formData.append("file", file);
    showLoading(true);

    fetch("/api/upload", {
        method: "POST",
        body: formData
    })
    .then(res => res.json())
    .then(data => {
        if (data.error) {
            showLoading(false);
            alert("Error: " + data.error);
            return;
        }
        currentData = data;
        showLoading(false);
        renderDashboard(data);
    })
    .catch(err => {
        showLoading(false);
        alert("Upload failed: " + err);
    });
}

function renderDashboard(data) {
    // KPIs
    document.getElementById("kpiEmployees").textContent = data.total_employees;
    document.getElementById("kpiBuses").textContent = data.total_buses;
    document.getElementById("kpiStops").textContent = data.total_stops;
    document.getElementById("btnExportExcel").disabled = false;

    renderRoutes("ALL");
    renderEmployees(data.employees);
    initMap(data.routes);
}

function renderRoutes(shiftFilter) {
    const grid = document.getElementById("routesGrid");
    grid.innerHTML = "";

    if (!currentData || !currentData.routes) return;

    const filtered = shiftFilter === "ALL"
        ? currentData.routes
        : currentData.routes.filter(r => r.shift === shiftFilter);

    filtered.forEach((r, idx) => {
        const card = document.createElement("div");
        card.className = "route-card";

        const stopsHtml = r.stops.map(s => `
            <div class="stop-step">
                <span class="stop-number">${s.seq}</span>
                <span class="stop-info">${s.stop_name}</span>
                <span class="stop-pax">+${s.passengers_boarding} pax</span>
                <span class="stop-dist">${s.distance_km > 0 ? '+' + s.distance_km + ' km' : 'Start'}</span>
            </div>
        `).join("");

        card.innerHTML = `
            <div class="route-header">
                <div>
                    <div class="route-title">${r.bus_id}</div>
                    <div style="font-size:0.75rem; color:var(--text-muted); margin-top:2px;">${r.corridor}</div>
                </div>
                <span class="route-shift-tag">${r.shift}</span>
            </div>
            <div class="route-body">
                <div class="route-meta">
                    <div class="meta-item">
                        <span class="meta-lbl">Assigned</span>
                        <span class="meta-val pax-badge">${r.total_passengers} / 15 seats</span>
                    </div>
                    <div class="meta-item">
                        <span class="meta-lbl">Stops</span>
                        <span class="meta-val">${r.total_stops} stops</span>
                    </div>
                    <div class="meta-item">
                        <span class="meta-lbl">Total Span</span>
                        <span class="meta-val">${r.total_km} km</span>
                    </div>
                </div>
                <div style="font-size: 0.78rem; font-weight:700; color:var(--text-dim); margin-bottom:8px; text-transform:uppercase;">
                    Driving Sequence:
                </div>
                <div class="stops-chain">
                    ${stopsHtml}
                </div>
            </div>
        `;
        grid.appendChild(card);
    });
}

function renderEmployees(employees) {
    const tbody = document.getElementById("employeeTableBody");
    tbody.innerHTML = "";
    document.getElementById("filteredEmpCount").textContent = employees.length;

    employees.forEach(e => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td style="color:var(--text-dim); font-family:monospace;">${e.sr_no}</td>
            <td style="font-family:monospace;">${e.employee_id || '-'}</td>
            <td style="font-weight:600; color:#fff;">${e.employee_name}</td>
            <td><span class="route-shift-tag">${e.shift}</span></td>
            <td><span class="bus-pill">${e.assigned_bus}</span></td>
            <td>${e.pickup_point}</td>
            <td style="font-family:monospace; color:var(--text-muted);">${e.mobile || '-'}</td>
        `;
        tbody.appendChild(tr);
    });
}

function filterEmployees(query) {
    if (!currentData || !currentData.employees) return;
    const q = query.toLowerCase().trim();
    const filtered = currentData.employees.filter(e => {
        return (e.employee_name && e.employee_name.toLowerCase().includes(q)) ||
               (e.pickup_point && e.pickup_point.toLowerCase().includes(q)) ||
               (e.assigned_bus && e.assigned_bus.toLowerCase().includes(q)) ||
               (e.mobile && String(e.mobile).includes(q));
    });
    renderEmployees(filtered);
}

function initMap(routes) {
    if (!leafletMap) {
        leafletMap = L.map("routeMap").setView([18.7000, 73.7800], 11);
        L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
            attribution: "© OpenStreetMap contributors, © CARTO",
            maxZoom: 18
        }).addTo(leafletMap);
    }

    // Clear old layers
    mapLayers.forEach(l => leafletMap.removeLayer(l));
    mapLayers = [];

    const bounds = [];

    routes.forEach((r, idx) => {
        const color = ROUTE_COLORS[idx % ROUTE_COLORS.length];
        const latlngs = [];

        r.stops.forEach((s, sIdx) => {
            if (s.lat && s.lon) {
                const pt = [s.lat, s.lon];
                latlngs.push(pt);
                bounds.push(pt);

                const circle = L.circleMarker(pt, {
                    radius: 6,
                    fillColor: color,
                    color: "#fff",
                    weight: 2,
                    opacity: 1,
                    fillOpacity: 0.9
                }).bindPopup(`<b>${r.bus_id}</b><br>Stop ${s.seq}: ${s.stop_name}<br>+${s.passengers_boarding} boarding`);

                circle.addTo(leafletMap);
                mapLayers.push(circle);
            }
        });

        if (latlngs.length > 1) {
            const polyline = L.polyline(latlngs, {
                color: color,
                weight: 4,
                opacity: 0.8,
                dashArray: "6, 6"
            }).addTo(leafletMap);
            mapLayers.push(polyline);
        }
    });

    if (bounds.length) {
        leafletMap.fitBounds(bounds, { padding: [30, 30] });
    }
}

function exportExcel() {
    if (!currentData) return;
    fetch("/api/export_excel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentData)
    })
    .then(res => res.blob())
    .then(blob => {
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "Grouped_Bus_Roster.xlsx";
        document.body.appendChild(a);
        a.click();
        a.remove();
    })
    .catch(err => alert("Export failed: " + err));
}
