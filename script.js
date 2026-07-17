// ============================================================
// CONFIGURATION
// ============================================================
const CONFIG = {
  statusFile: "status.json",

  pollIntervalMs: 10000,               // fréquence de lecture du site (10s)
  offlineThresholdSec: 75,             // si aucune donnée depuis 75s -> "Machine éteinte"

  thresholds: {
    cpuTemp:  { warning: 75, critical: 88 },
    gpuTemp:  { warning: 75, critical: 88 },
    usage:    { warning: 85, critical: 96 },
    ramUsage: { warning: 85, critical: 96 },
    diskUsage:{ warning: 90, critical: 97 },
  }
};

// On lit status.json en same-origin (servi par GitHub Pages avec le reste
// du site), pas via raw.githubusercontent.com dont le cache CDN ignore
// souvent la query string de cache-busting et peut servir une version
// périmée pendant plusieurs minutes.
const dataUrl = () => `${CONFIG.statusFile}?t=${Date.now()}-${Math.random().toString(36).slice(2)}`;

// ============================================================
// Utilitaires
// ============================================================

function statusFor(value, { warning, critical }) {
  if (value === null || value === undefined || isNaN(value)) return "unknown";
  if (value >= critical) return "critical";
  if (value >= warning) return "warning";
  return "nominal";
}

function applyBezel(dotEl, status) {
  dotEl.classList.remove("warning", "critical");
  if (status === "warning") dotEl.classList.add("warning");
  if (status === "critical") dotEl.classList.add("critical");
}

function applyBarColor(barEl, status) {
  const colors = { nominal: "var(--nominal)", warning: "var(--warning)", critical: "var(--critical)" };
  barEl.style.background = colors[status] || colors.nominal;
}

function fmtTemp(v) { return (v === null || v === undefined) ? "—" : `${v.toFixed(0)}°C`; }
function fmtPct(v) { return (v === null || v === undefined) ? "—" : `${v.toFixed(0)}%`; }
function fmtKbps(v) {
  if (v === null || v === undefined) return "—";
  if (v > 1024) return `${(v / 1024).toFixed(1)} Mb/s`;
  return `${v.toFixed(0)} Kb/s`;
}
function fmtAgo(sec) {
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)} min`;
  return `${Math.floor(sec / 3600)} h`;
}

// ============================================================
// Rendu
// ============================================================

function renderOffline(secondsSince) {
  document.getElementById("offline-screen").hidden = false;
  document.getElementById("offline-since").textContent = fmtAgo(secondsSince);
}

function renderOnline() {
  document.getElementById("offline-screen").hidden = true;
}

function renderDashboard(data) {
  const t = CONFIG.thresholds;
  const alerts = [];

  document.getElementById("hostname").textContent = data.hostname || "—";
  document.getElementById("last-update").textContent = new Date(data.timestamp * 1000).toLocaleTimeString("fr-FR");

  // CPU
  if (data.cpu) {
    document.getElementById("name-cpu").textContent = data.cpu.name || "—";
    document.getElementById("cpu-temp").textContent = fmtTemp(data.cpu.temp);
    document.getElementById("cpu-usage").textContent = fmtPct(data.cpu.usage);
    const cpuStatus = statusFor(data.cpu.temp, t.cpuTemp);
    applyBezel(document.getElementById("dot-cpu"), cpuStatus);
    const bar = document.getElementById("bar-cpu");
    bar.style.width = `${Math.min(data.cpu.usage || 0, 100)}%`;
    applyBarColor(bar, statusFor(data.cpu.usage, t.usage));
    if (cpuStatus === "critical") alerts.push(`Température CPU critique (${fmtTemp(data.cpu.temp)})`);
    else if (cpuStatus === "warning") alerts.push(`Température CPU élevée (${fmtTemp(data.cpu.temp)})`);
  }

  // GPU
  if (data.gpu) {
    document.getElementById("name-gpu").textContent = data.gpu.name || "—";
    document.getElementById("gpu-temp").textContent = fmtTemp(data.gpu.temp);
    document.getElementById("gpu-usage").textContent = fmtPct(data.gpu.usage);
    const gpuStatus = statusFor(data.gpu.temp, t.gpuTemp);
    applyBezel(document.getElementById("dot-gpu"), gpuStatus);
    const bar = document.getElementById("bar-gpu");
    bar.style.width = `${Math.min(data.gpu.usage || 0, 100)}%`;
    applyBarColor(bar, statusFor(data.gpu.usage, t.usage));
    if (data.gpu.vram_used !== undefined) {
      document.getElementById("gpu-vram").textContent =
        `VRAM ${(data.gpu.vram_used / 1024).toFixed(1)} / ${(data.gpu.vram_total / 1024).toFixed(1)} Go`;
    }
    if (gpuStatus === "critical") alerts.push(`Température GPU critique (${fmtTemp(data.gpu.temp)})`);
    else if (gpuStatus === "warning") alerts.push(`Température GPU élevée (${fmtTemp(data.gpu.temp)})`);
  }

  // RAM
  if (data.ram) {
    const pct = data.ram.usage_pct;
    document.getElementById("ram-usage").textContent = fmtPct(pct);
    document.getElementById("ram-detail").textContent = `${data.ram.used.toFixed(1)} / ${data.ram.total.toFixed(1)} Go`;
    const ramStatus = statusFor(pct, t.ramUsage);
    applyBezel(document.getElementById("dot-ram"), ramStatus);
    const bar = document.getElementById("bar-ram");
    bar.style.width = `${Math.min(pct || 0, 100)}%`;
    applyBarColor(bar, ramStatus);
    if (ramStatus === "critical") alerts.push(`Mémoire saturée (${fmtPct(pct)})`);
  }

  // Disques
  if (Array.isArray(data.disks)) {
    const list = document.getElementById("disk-list");
    list.innerHTML = "";
    let worstDisk = "nominal";
    data.disks.forEach(d => {
      const status = statusFor(d.used_pct, t.diskUsage);
      if (status === "critical") worstDisk = "critical";
      else if (status === "warning" && worstDisk !== "critical") worstDisk = "warning";
      const row = document.createElement("div");
      row.className = "disk-row";
      row.innerHTML = `
        <span class="disk-name mono">${d.name}</span>
        <div class="bar"><div class="bar-fill" style="width:${Math.min(d.used_pct,100)}%"></div></div>
        <span class="disk-pct mono">${fmtPct(d.used_pct)}</span>
      `;
      applyBarColor(row.querySelector(".bar-fill"), status);
      list.appendChild(row);
    });
    applyBezel(document.getElementById("dot-disk"), worstDisk);
  }

  // Réseau
  if (data.network) {
    document.getElementById("net-up").textContent = fmtKbps(data.network.up_kbps);
    document.getElementById("net-down").textContent = fmtKbps(data.network.down_kbps);
  }

  // Bannière d'alerte globale
  const banner = document.getElementById("alert-banner");
  if (alerts.length > 0) {
    document.getElementById("alert-text").textContent = alerts.join(" · ");
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }
}

// ============================================================
// Boucle principale
// ============================================================

async function tick() {
  try {
    const res = await fetch(dataUrl(), {
      cache: "no-store",
      headers: { "Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache" }
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    const ageSec = Math.floor(Date.now() / 1000) - data.timestamp;

    if (ageSec > CONFIG.offlineThresholdSec) {
      renderOffline(ageSec);
    } else {
      renderOnline();
      renderDashboard(data);
    }
  } catch (err) {
    // Fichier introuvable ou réseau indisponible -> on considère la machine éteinte
    renderOffline(9999);
    console.warn("Impossible de récupérer la télémétrie :", err);
  }
}

tick();
setInterval(tick, CONFIG.pollIntervalMs);
