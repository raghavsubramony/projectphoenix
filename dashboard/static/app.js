"use strict";

// ---------- helpers ----------
const $ = (id) => document.getElementById(id);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const fmt = (v, d = 1) => Number(v).toFixed(d);

async function getJSON(url) {
  const r = await fetch(url);
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

// ---------- tab switching ----------
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $("tab-" + btn.dataset.tab).classList.add("active");
  });
});

// ======================================================================
//  SIMULATOR
// ======================================================================
const state = {
  steps: [],
  summary: null,
  i: 0,
  playing: false,
  acc: 0,
  last: 0,
  dt: 1,
  maxSpeed: 200,
  cycles: {},
};

async function loadOptions() {
  const opts = await getJSON("/api/options");
  const bodySel = $("body");
  const cycleSel = $("cycle");
  opts.bodies.forEach((b) => bodySel.add(new Option(b, b)));
  opts.cycles.forEach((c) => {
    cycleSel.add(new Option(c.name, c.name));
    state.cycles[c.name] = c.description;
  });
  updateCycleDesc();
}

function updateCycleDesc() {
  $("cycleDesc").textContent = state.cycles[$("cycle").value] || "";
}
$("cycle").addEventListener("change", updateCycleDesc);

$("speed").addEventListener("input", () => {
  $("speedMul").textContent = $("speed").value;
});

async function runSimulation() {
  setRunningUI(true, "Loading...");
  try {
    const params = new URLSearchParams({
      cycle: $("cycle").value,
      body: $("body").value,
      coupled: $("coupled").checked ? "1" : "0",
    });
    const data = await getJSON("/api/simulate?" + params.toString());
    state.steps = data.steps;
    state.summary = data.summary;
    state.dt = data.summary.dt_s || 1;
    state.maxSpeed = Math.max(60, Math.ceil((data.summary.peak_speed_kmh + 20) / 20) * 20);
    state.i = 0;
    state.acc = 0;
    traceReset();
    renderSummary(data.summary);
    play();
  } catch (e) {
    setRunningUI(false);
    alert("Simulation failed: " + e.message);
  }
}

function setRunningUI(running, label) {
  $("play").disabled = running;
  $("play").textContent = label || "\u25B6 Run";
  $("pause").disabled = !running;
}

function play() {
  if (!state.steps.length) return;
  state.playing = true;
  state.last = performance.now();
  setRunningUI(true);
  requestAnimationFrame(tick);
}

function pause() {
  state.playing = false;
  setRunningUI(false, "\u25B6 Resume");
  $("pause").disabled = true;
}

function reset() {
  state.playing = false;
  state.i = 0;
  state.acc = 0;
  if (state.steps.length) drawStep(state.steps[0]);
  traceReset();
  setRunningUI(false);
}

function tick(now) {
  if (!state.playing) return;
  const mult = Number($("speed").value);
  const elapsed = (now - state.last) / 1000;
  state.last = now;
  state.acc += elapsed * mult;

  while (state.acc >= state.dt && state.i < state.steps.length) {
    drawStep(state.steps[state.i]);
    pushTrace(state.steps[state.i]);
    state.i++;
    state.acc -= state.dt;
  }

  if (state.i >= state.steps.length) {
    state.playing = false;
    setRunningUI(false, "\u25B6 Run");
    $("summaryCard").hidden = false;
    return;
  }
  requestAnimationFrame(tick);
}

// ---------- speedometer ----------
const speedo = $("speedo").getContext("2d");
function drawSpeedo(speed) {
  const c = speedo;
  const W = 320, H = 320, cx = W / 2, cy = H / 2, R = 130;
  c.clearRect(0, 0, W, H);
  const start = Math.PI * 0.75, end = Math.PI * 2.25;
  // track
  c.lineWidth = 16;
  c.strokeStyle = "#1b2434";
  c.beginPath();
  c.arc(cx, cy, R, start, end);
  c.stroke();
  // ticks
  const ticks = 8;
  c.fillStyle = "#8da2bd";
  c.font = "11px Segoe UI";
  c.textAlign = "center";
  c.textBaseline = "middle";
  for (let i = 0; i <= ticks; i++) {
    const a = start + (end - start) * (i / ticks);
    const lr = R - 26;
    c.fillText(Math.round((state.maxSpeed * i) / ticks),
      cx + Math.cos(a) * lr, cy + Math.sin(a) * lr);
  }
  // value arc
  const frac = clamp(speed / state.maxSpeed, 0, 1);
  const a2 = start + (end - start) * frac;
  const grad = c.createLinearGradient(0, 0, W, H);
  grad.addColorStop(0, "#4ea8de");
  grad.addColorStop(1, "#36d1a6");
  c.strokeStyle = grad;
  c.lineWidth = 16;
  c.lineCap = "round";
  c.beginPath();
  c.arc(cx, cy, R, start, a2);
  c.stroke();
  // needle
  c.strokeStyle = "#ffd166";
  c.lineWidth = 3;
  c.beginPath();
  c.moveTo(cx, cy);
  c.lineTo(cx + Math.cos(a2) * (R - 8), cy + Math.sin(a2) * (R - 8));
  c.stroke();
  c.fillStyle = "#ffd166";
  c.beginPath();
  c.arc(cx, cy, 6, 0, Math.PI * 2);
  c.fill();
  // readout
  c.fillStyle = "#e6edf6";
  c.font = "bold 46px Segoe UI";
  c.fillText(Math.round(speed), cx, cy + 56);
  c.fillStyle = "#8da2bd";
  c.font = "13px Segoe UI";
  c.fillText("km/h", cx, cy + 86);
}

// ---------- bars / soc ----------
function setSignedBar(el, valEl, kw, scale, signed) {
  const mag = clamp(Math.abs(kw) / scale, 0, 1) * 100;
  if (signed) {
    if (kw >= 0) {
      el.style.marginLeft = "50%";
      el.style.width = (mag / 2) + "%";
    } else {
      el.style.width = (mag / 2) + "%";
      el.style.marginLeft = (50 - mag / 2) + "%";
    }
  } else {
    el.style.marginLeft = "0";
    el.style.width = mag + "%";
  }
  valEl.textContent = fmt(kw, 1) + " kW";
}

function drawStep(s) {
  drawSpeedo(s.speed_kmh);
  $("tierBadge").textContent = s.tier_label;
  const scale = Math.max(50, state.maxSpeed); // kW scale heuristic
  setSignedBar($("barDemand"), $("valDemand"), s.demand_kw, 200, false);
  setSignedBar($("barGen"), $("valGen"), s.generation_kw, 200, false);
  setSignedBar($("barBuffer"), $("valBuffer"), s.buffer_kw, 160, true);
  setSignedBar($("barBattery"), $("valBattery"), s.battery_kw, 160, true);
  setSignedBar($("barShortfall"), $("valShortfall"), s.shortfall_kw, 100, false);
  $("socBattery").style.width = (s.battery_soc * 100) + "%";
  $("socBuffer").style.width = (s.buffer_soc * 100) + "%";
  $("valSocBattery").textContent = fmt(s.battery_soc * 100, 1) + "%";
  $("valSocBuffer").textContent = fmt(s.buffer_soc * 100, 1) + "%";
  $("clockT").textContent = fmt(s.t, 1);
}

// ---------- trace ----------
const trace = $("trace");
const tctx = trace.getContext("2d");
let traceData = [];
const TRACE_WINDOW = 240; // samples shown

function traceReset() {
  traceData = [];
  tctx.clearRect(0, 0, trace.width, trace.height);
}

function pushTrace(s) {
  traceData.push(s);
  if (traceData.length > TRACE_WINDOW) traceData.shift();
  drawTrace();
}

function drawTrace() {
  const W = trace.width, H = trace.height;
  tctx.clearRect(0, 0, W, H);
  // gridlines
  tctx.strokeStyle = "#1b2434";
  tctx.lineWidth = 1;
  for (let g = 1; g < 4; g++) {
    const y = (H * g) / 4;
    tctx.beginPath();
    tctx.moveTo(0, y);
    tctx.lineTo(W, y);
    tctx.stroke();
  }
  if (traceData.length < 2) return;
  const n = traceData.length;
  const maxKw = Math.max(50, ...traceData.map((d) =>
    Math.max(Math.abs(d.demand_kw), Math.abs(d.generation_kw), Math.abs(d.buffer_kw))));

  const line = (key, color, range, signed) => {
    tctx.strokeStyle = color;
    tctx.lineWidth = 2;
    tctx.beginPath();
    traceData.forEach((d, i) => {
      const x = (i / (TRACE_WINDOW - 1)) * W;
      let v = d[key];
      let y;
      if (signed) {
        y = H / 2 - (v / range) * (H / 2 - 6);
      } else {
        y = H - (clamp(v / range, 0, 1)) * (H - 6);
      }
      i === 0 ? tctx.moveTo(x, y) : tctx.lineTo(x, y);
    });
    tctx.stroke();
  };

  line("speed_kmh", "#ffd166", state.maxSpeed, false);
  line("demand_kw", "#f4a259", maxKw, false);
  line("generation_kw", "#4ea8de", maxKw, false);
  line("buffer_kw", "#c77dff", maxKw, true);
}

// ---------- summary ----------
function renderSummary(s) {
  const cells = [
    ["Distance", fmt(s.distance_km, 2) + " km"],
    ["Duration", fmt(s.duration_s / 60, 1) + " min"],
    ["Mean speed", fmt(s.mean_speed_kmh, 1) + " km/h"],
    ["Peak speed", fmt(s.peak_speed_kmh, 1) + " km/h"],
    ["Fuel", fmt(s.fuel_l_per_100km, 2) + " L/100km"],
    ["Fuel (SoC-corr.)", fmt(s.equiv_fuel_l_per_100km, 2) + " L/100km"],
    ["CO2", fmt(s.co2_g_per_km, 1) + " g/km"],
    ["ATPE efficiency", fmt(s.mean_efficiency * 100, 1) + " %"],
    ["Net battery", fmt(s.net_battery_kwh, 2) + " kWh"],
    ["Buffer peak", fmt(s.buffer_peak_kw, 1) + " kW"],
    ["Battery peak", fmt(s.battery_peak_kw, 1) + " kW"],
    ["Battery temp", fmt(s.battery_peak_temp_c, 1) + " C"],
    ["Shortfalls", s.shortfall_events + " steps"],
    ["Max shortfall", fmt(s.max_shortfall_kw, 1) + " kW"],
  ];
  $("summaryGrid").innerHTML = cells.map(([l, v]) =>
    `<div class="cell"><div class="label">${l}</div><div class="value">${v}</div></div>`).join("");
  // distance display ticks up live; show final here too
  $("clockDist").textContent = fmt(s.distance_km, 2);
}

$("play").addEventListener("click", () => {
  if (state.steps.length && state.i > 0 && state.i < state.steps.length) play();
  else runSimulation();
});
$("pause").addEventListener("click", pause);
$("reset").addEventListener("click", reset);

// ======================================================================
//  TEST BENCH
// ======================================================================
async function runTests() {
  const grid = $("testsGrid");
  const tally = $("testTally");
  $("runTests").disabled = true;
  $("runTests").textContent = "Running...";
  grid.innerHTML = `<p class="hint">Executing the unittest suite in the twin...</p>`;
  tally.innerHTML = "";
  try {
    const data = await getJSON("/api/tests");
    grid.innerHTML = "";
    const s = data.summary;
    tally.innerHTML =
      `<span class="pill pass">${s.passed} passed</span>` +
      `<span class="pill ${s.failed ? "fail" : ""}">${s.failed} failed</span>` +
      `<span class="pill">${fmt(s.duration_ms, 0)} ms</span>`;
    // reveal cards with a staggered animation for a "live bench" feel
    data.tests.forEach((t, idx) => {
      setTimeout(() => grid.appendChild(testCard(t)), idx * 45);
    });
  } catch (e) {
    grid.innerHTML = `<p class="hint">Test run failed: ${e.message}</p>`;
  } finally {
    $("runTests").disabled = false;
    $("runTests").textContent = "\u25B6 Run test suite";
  }
}

function testCard(t) {
  const div = document.createElement("div");
  div.className = "test-card " + t.status;
  const msg = t.message
    ? `<div class="msg">${escapeHtml(t.message)}</div>` : "";
  div.innerHTML = `
    <div class="row">
      <div><span class="status-dot"></span><span class="name">${t.name}</span></div>
      <span class="dur">${fmt(t.duration_ms, 1)} ms</span>
    </div>
    <div class="cls">${t.short_class}</div>
    ${t.doc ? `<p class="doc">${escapeHtml(t.doc)}</p>` : ""}
    ${msg}`;
  return div;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

$("runTests").addEventListener("click", runTests);

// ---------- boot ----------
loadOptions().catch((e) => alert("Failed to load options: " + e.message));
drawSpeedo(0);
