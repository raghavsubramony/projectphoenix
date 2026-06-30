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
  gates: null,
  gate1Tiers: [],
  selectedGateTier: 1,
  gate1Enabled: true,
  crankDeg: 0.0,
  scrubManual: false,
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
      gate1: $("gate1").checked ? "1" : "0",
    });
    state.gate1Enabled = $("gate1").checked;
    const data = await getJSON("/api/simulate?" + params.toString());
    state.steps = data.steps;
    state.summary = data.summary;
    state.gates = data.gates || null;
    state.gate1Tiers = (data.gates && data.gates.gate1 && data.gates.gate1.tiers) || [];
    state.selectedGateTier = (data.gates && data.gates.gate1 && data.gates.gate1.active_tier) || 1;
    state.crankDeg = 0.0;
    state.scrubManual = false;
    state.dt = data.summary.dt_s || 1;
    state.maxSpeed = Math.max(60, Math.ceil((data.summary.peak_speed_kmh + 20) / 20) * 20);
    state.i = 0;
    state.acc = 0;
    traceReset();
    renderSummary(data.summary);
    renderGateViews();
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
  if (state.steps.length) {
    drawStep(state.steps[0]);
  } else {
    updateLiveCombustion({ tier: 0, tier_label: "EV", generation_kw: 0,
      imep_bar: 0, knock_index: 0, peak_pressure_bar: 0, predicted_tdc_mm: 0,
      efficiency: 0 });
  }
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
  updateTierBadge(s);
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
  updateLiveCombustion(s);
}

function updateTierBadge(s) {
  const el = $("tierBadge");
  el.textContent = s.tier_label || "EV";
  el.className = "tier-badge";
  if (s.tier > 0) {
    el.classList.add("tier-on");
  } else if (s.mode === "CS") {
    el.classList.add("tier-cs");
  }
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

function activeTierForCad() {
  if (state.steps.length && state.i < state.steps.length) {
    const s = state.steps[state.i];
    const n = s.combustion_tier || (s.tier > 0 ? s.tier : 0);
    if (n > 0) return tierByNum(n);
  }
  return tierByNum(state.selectedGateTier);
}

function roundRect(ctx, x, y, w, h, r) {
  if (ctx.roundRect) {
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, r);
    return;
  }
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function wrapCrankDeg(deg) {
  while (deg > 180) deg -= 360;
  while (deg < -180) deg += 360;
  return deg;
}

function nearestCycleIndex(anim, crankDeg) {
  if (!anim || !anim.crank_deg || !anim.crank_deg.length) return 0;
  let best = 0;
  let bestD = 1e9;
  anim.crank_deg.forEach((d, i) => {
    const diff = Math.abs(d - crankDeg);
    if (diff < bestD) {
      bestD = diff;
      best = i;
    }
  });
  return best;
}

function advanceCrankForStep(tier, dt) {
  if (!tier || !tier.animation) return;
  const rpm = tier.animation.speed_rpm || 2600;
  state.crankDeg = wrapCrankDeg(state.crankDeg + (rpm / 60) * 360 * dt);
}

function cadReadoutHtml(anim, idx) {
  if (!anim) return "";
  const p = anim.pressure_bar[idx] || 0;
  const heat = anim.heat_norm[idx] || 0;
  const pos = anim.piston_mm[idx] || 0;
  return (
    `<div><strong>P</strong> ${fmt(p, 1)} bar</div>` +
    `<div><strong>Piston</strong> ${fmt(pos, 1)} / ${fmt(anim.stroke_mm, 1)} mm</div>` +
    `<div><strong>Combustion</strong> ${fmt(heat * 100, 0)} % intensity</div>` +
    `<div><strong>Bore</strong> ${fmt(anim.bore_mm, 1)} mm · stroke ${fmt(anim.stroke_mm, 1)} mm</div>`
  );
}

function drawCylinderCrossSection(canvasId, anim, crankDeg) {
  const canvas = $(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const W = canvas.width;
  const H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  if (!anim || !anim.crank_deg || !anim.crank_deg.length) {
    ctx.fillStyle = "#8da2bd";
    ctx.font = "12px Segoe UI";
    ctx.fillText("Run simulation with Gate 1 enabled", 24, H / 2);
    return;
  }

  const idx = nearestCycleIndex(anim, crankDeg);
  const pressure = anim.pressure_bar[idx] || 0;
  const heat = anim.heat_norm[idx] || 0;
  const pistonY = anim.piston_mm[idx] || 0;
  const stroke = anim.stroke_mm || 100;
  const bore = anim.bore_mm || 60;

  const pad = 28;
  const chamberTop = pad + 18;
  const chamberH = H - pad - 90;
  const scale = chamberH / Math.max(stroke, 1);
  const boreW = clamp(bore * 0.55, 48, W - pad * 2 - 40);
  const cx = W / 2;
  const left = cx - boreW / 2;

  // bounce chamber
  const bounceH = 36;
  ctx.fillStyle = "#1a2433";
  ctx.strokeStyle = "#3d5168";
  ctx.lineWidth = 2;
  ctx.beginPath();
  roundRect(ctx, left - 4, chamberTop + chamberH, boreW + 8, bounceH, 6);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#8da2bd";
  ctx.font = "10px Segoe UI";
  ctx.fillText("bounce chamber", left, chamberTop + chamberH + bounceH + 12);

  // cylinder liner
  ctx.fillStyle = "#243042";
  ctx.strokeStyle = "#4ea8de";
  ctx.lineWidth = 2;
  ctx.beginPath();
  roundRect(ctx, left, chamberTop, boreW, chamberH, 4);
  ctx.fill();
  ctx.stroke();

  // combustion glow
  const glow = clamp(heat * 0.85 + pressure / 80, 0, 1);
  if (glow > 0.05) {
    const grd = ctx.createLinearGradient(left, chamberTop, left + boreW, chamberTop + chamberH * 0.5);
    grd.addColorStop(0, `rgba(255, 120, 40, ${0.15 + glow * 0.45})`);
    grd.addColorStop(1, "rgba(255, 80, 20, 0)");
    ctx.fillStyle = grd;
    ctx.fillRect(left + 2, chamberTop + 2, boreW - 4, chamberH * 0.55);
  }

  // piston (TDC = top of chamber)
  const pistonH = 14;
  const py = chamberTop + pistonY * scale;
  ctx.fillStyle = "#ffd166";
  ctx.strokeStyle = "#c9a227";
  ctx.lineWidth = 2;
  ctx.beginPath();
  roundRect(ctx, left + 3, py, boreW - 6, pistonH, 3);
  ctx.fill();
  ctx.stroke();

  // linear alternator hint (magnet on piston rod)
  ctx.strokeStyle = "#36d1a6";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(cx, py + pistonH);
  ctx.lineTo(cx, chamberTop + chamberH - 8);
  ctx.stroke();
  ctx.fillStyle = "#36d1a6";
  ctx.font = "10px Segoe UI";
  ctx.fillText("linear generator", cx + 12, chamberTop + chamberH - 14);

  // coil windings
  for (let y = chamberTop + 30; y < chamberTop + chamberH - 20; y += 18) {
    ctx.strokeStyle = "#4ea8de";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(left + boreW + 6, y);
    ctx.lineTo(left + boreW + 22, y);
    ctx.stroke();
  }

  // TDC marker
  ctx.strokeStyle = "#ff5d73";
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(left - 8, chamberTop);
  ctx.lineTo(left + boreW + 30, chamberTop);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "#ff5d73";
  ctx.font="10px Segoe UI";
  ctx.fillText("TDC", left - 24, chamberTop + 4);

  // title
  ctx.fillStyle = "#e6edf6";
  ctx.font = "11px Segoe UI";
  ctx.fillText(`θ = ${fmt(crankDeg, 0)}°`, pad, 16);
  ctx.fillStyle = "#8da2bd";
  ctx.fillText("free-piston (no crank)", pad, 30);
}

function plotLineWithMarker(canvasId, xs, ys, color, xLabel, yLabel, markerIdx) {
  const canvas = $(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  drawAxes(ctx, w, h, xLabel, yLabel);
  if (!xs || xs.length < 2 || ys.length < 2) return;

  const xmin = Math.min(...xs);
  const xmax = Math.max(...xs);
  const ymin = Math.min(...ys);
  const ymax = Math.max(...ys);
  const xr = Math.max(1e-9, xmax - xmin);
  const yr = Math.max(1e-9, ymax - ymin);

  const toX = (v) => 36 + ((v - xmin) / xr) * (w - 46);
  const toY = (v) => (h - 24) - ((v - ymin) / yr) * (h - 34);

  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (let i = 0; i < xs.length; i++) {
    const x = toX(xs[i]);
    const y = toY(ys[i]);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();

  const mi = clamp(markerIdx, 0, xs.length - 1);
  const mx = toX(xs[mi]);
  const my = toY(ys[mi]);
  ctx.fillStyle = "#ff5d73";
  ctx.beginPath();
  ctx.arc(mx, my, 6, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 1.5;
  ctx.stroke();
}

function renderCycleAtCrank(tier, crankDeg, pvCanvas, presCanvas, cylCanvas, readoutId) {
  if (!tier || !tier.animation) return;
  const idx = nearestCycleIndex(tier.animation, crankDeg);
  plotLineWithMarker(pvCanvas, tier.trace.volume_cc, tier.trace.pressure_bar,
    "#ffd166", "volume cc", "bar", idx);
  plotLineWithMarker(presCanvas, tier.trace.crank_deg, tier.trace.pressure_bar,
    "#4ea8de", "crank deg", "bar", idx);
  drawCylinderCrossSection(cylCanvas, tier.animation, crankDeg);
  if (readoutId && $(readoutId)) {
    $(readoutId).innerHTML = cadReadoutHtml(tier.animation, idx);
  }
}

function syncCrankScrubbers(deg) {
  state.crankDeg = wrapCrankDeg(deg);
  if ($("crankScrub")) {
    $("crankScrub").value = String(Math.round(state.crankDeg));
    $("crankScrubVal").textContent = fmt(state.crankDeg, 0);
  }
  if ($("gateCrankScrub")) {
    $("gateCrankScrub").value = String(Math.round(state.crankDeg));
    $("gateCrankScrubVal").textContent = fmt(state.crankDeg, 0);
  }
}

function tierByNum(n) {
  return state.gate1Tiers.find((t) => t.tier === n) || null;
}

function updateLiveCombustion(s) {
  const card = $("combustionLiveCard");
  const hint = $("liveCombHint");
  if (!state.gate1Enabled) {
    $("liveCombTier").textContent = "Gate 1 off";
    $("liveCombMetrics").innerHTML = "";
    plotLineWithMarker("livePv", [], [], "#ffd166", "volume cc", "bar", 0);
    plotLineWithMarker("livePressure", [], [], "#4ea8de", "crank deg", "bar", 0);
    drawCylinderCrossSection("liveCylinder", null, 0);
    $("liveCadReadout").innerHTML = "";
    hint.textContent = "Check “Gate 1 combustion” and re-run to see per-tier physics.";
    return;
  }
  if (!state.gate1Tiers.length) {
    $("liveCombTier").textContent = "—";
    drawCylinderCrossSection("liveCylinder", null, 0);
    hint.textContent = state.gates && state.gates.gate1 && state.gates.gate1.message
      ? state.gates.gate1.message
      : "Run Highway or Mixed so the engine starts.";
    return;
  }
  const displayTier = s.combustion_tier || 0;
  const isLive = Boolean(s.combustion_live);
  if (displayTier <= 0) {
    $("liveCombTier").textContent = s.mode === "EV" ? "Engine off (EV)" : s.tier_label;
    $("liveCombMetrics").innerHTML =
      `<div class="metric-row"><span>Status</span><span>No combustion</span></div>` +
      `<div class="metric-row"><span>Demand</span><span>${fmt(s.demand_kw, 1)} kW</span></div>` +
      `<div class="metric-row"><span>Mode</span><span>${s.mode}</span></div>`;
    plotLineWithMarker("livePv", [], [], "#ffd166", "volume cc", "bar", 0);
    plotLineWithMarker("livePressure", [], [], "#4ea8de", "crank deg", "bar", 0);
    drawCylinderCrossSection("liveCylinder", null, 0);
    $("liveCadReadout").innerHTML = "";
    hint.textContent = s.demand_kw < 30
      ? "Low load — twin stays in EV until demand exceeds ~30 kW (accel, grade, or towing)."
      : "Combustion appears when a cylinder tier is generating.";
    return;
  }
  const tier = tierByNum(displayTier);
  if (!state.scrubManual && tier) {
    advanceCrankForStep(tier, state.dt);
  }
  syncCrankScrubbers(state.crankDeg);
  const tierLabel = isLive ? s.tier_label : `Tier ${displayTier} (load map)`;
  $("liveCombTier").textContent = tierLabel + (tier ? ` — ${tier.name}` : "");
  hint.textContent = isLive
    ? "Live combustion from Gate 1 physics; cutaway synced to cycle angle."
    : `${s.mode} on battery — design map for ${fmt(s.demand_kw, 0)} kW demand (engine not firing this step).`;
  const rows = [
    ["Source", isLive ? "Live step" : "Design map @ load"],
    ["IMEP", fmt(isLive ? s.imep_bar : (tier ? tier.metrics.imep_bar : 0), 2) + " bar"],
    ["Knock", fmt(isLive ? s.knock_index : (tier ? tier.metrics.knock_index : 0), 3)],
    ["Peak P", fmt(isLive ? s.peak_pressure_bar : (tier ? tier.metrics.peak_pressure_bar : 0), 1) + " bar"],
    ["Pred. TDC", fmt(isLive ? s.predicted_tdc_mm : (tier ? tier.metrics.predicted_tdc_mm : 0), 2) + " mm"],
    ["Gen power", fmt(s.generation_kw, 1) + " kW"],
    ["Demand", fmt(s.demand_kw, 1) + " kW"],
  ];
  if (isLive) {
    rows.push(["ATPE eff.", fmt(s.efficiency * 100, 1) + " %"]);
  }
  if (tier) {
    rows.push(["Displacement", tier.displacement_cc + " cc × " + tier.units]);
    rows.push(["Map load", fmt(tier.load_fraction * 100, 0) + " %"]);
  }
  $("liveCombMetrics").innerHTML = rows.map(([l, v]) =>
    `<div class="metric-row"><span>${l}</span><span>${v}</span></div>`
  ).join("");
  if (tier && tier.trace) {
    renderCycleAtCrank(tier, state.crankDeg,
      "livePv", "livePressure", "liveCylinder", "liveCadReadout");
  }
  state.selectedGateTier = displayTier;
  highlightGateTierTabs(displayTier);
}

function highlightGateTierTabs(activeTier) {
  document.querySelectorAll(".tier-tab").forEach((btn) => {
    const n = Number(btn.dataset.tier);
    btn.classList.toggle("active", n === activeTier);
  });
  document.querySelectorAll(".compare-item").forEach((el) => {
    el.classList.toggle("active-tier", Number(el.dataset.tier) === activeTier);
  });
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

// ======================================================================
//  GATES VIEW
// ======================================================================
function drawAxes(ctx, w, h, xLabel, yLabel) {
  ctx.strokeStyle = "#243042";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(36, 8);
  ctx.lineTo(36, h - 24);
  ctx.lineTo(w - 10, h - 24);
  ctx.stroke();
  ctx.fillStyle = "#8da2bd";
  ctx.font = "11px Segoe UI";
  ctx.fillText(yLabel, 6, 16);
  ctx.fillText(xLabel, w - 70, h - 8);
}

function plotLine(canvasId, xs, ys, color, xLabel, yLabel) {
  const canvas = $(canvasId);
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  drawAxes(ctx, w, h, xLabel, yLabel);
  if (!xs || xs.length < 2 || ys.length < 2) return;

  const xmin = Math.min(...xs);
  const xmax = Math.max(...xs);
  const ymin = Math.min(...ys);
  const ymax = Math.max(...ys);
  const xr = Math.max(1e-9, xmax - xmin);
  const yr = Math.max(1e-9, ymax - ymin);

  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (let i = 0; i < xs.length; i++) {
    const x = 36 + ((xs[i] - xmin) / xr) * (w - 46);
    const y = (h - 24) - ((ys[i] - ymin) / yr) * (h - 34);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();
}

function plotMultiTrend(canvasId, steps) {
  const canvas = $(canvasId);
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  drawAxes(ctx, w, h, "time", "value");
  if (!steps || steps.length < 2) return;

  const active = steps.filter((s) => s.tier > 0 && s.generation_kw > 0 && s.imep_bar > 0);
  if (active.length < 2) return;

  const xs = active.map((s) => s.t);
  const series = [
    { key: "imep_bar", color: "#4ea8de", label: "IMEP bar" },
    { key: "knock_index", color: "#ff5d73", label: "Knock" },
    { key: "predicted_tdc_mm", color: "#36d1a6", label: "TDC mm" },
  ];

  const xmin = Math.min(...xs);
  const xmax = Math.max(...xs);
  const xr = Math.max(1e-9, xmax - xmin);

  series.forEach((s) => {
    const vals = active.map((p) => p[s.key]);
    const vmin = Math.min(...vals);
    const vmax = Math.max(...vals);
    const vr = Math.max(1e-9, vmax - vmin);
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    active.forEach((p, i) => {
      const x = 36 + ((p.t - xmin) / xr) * (w - 46);
      const y = (h - 24) - ((p[s.key] - vmin) / vr) * (h - 34);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });

  series.forEach((s, i) => {
    ctx.fillStyle = s.color;
    ctx.fillRect(46 + i * 100, 10, 10, 10);
    ctx.fillStyle = "#8da2bd";
    ctx.font = "11px Segoe UI";
    ctx.fillText(s.label, 60 + i * 100, 19);
  });
}

function renderGateRoadmap(roadmap) {
  const host = $("gateRoadmap");
  if (!roadmap || !roadmap.length) {
    host.innerHTML = `<p class="hint">Roadmap not available.</p>`;
    return;
  }
  host.innerHTML = roadmap.map((g) => {
    const cls = g.status === "ready" ? "badge-ready" : "badge-planned";
    return `<article class="roadmap-item">
      <div class="name">${escapeHtml(g.name)}</div>
      <div class="meta">${escapeHtml(g.description)}</div>
      <span class="${cls}">${escapeHtml(g.status)}</span>
    </article>`;
  }).join("");
}

function renderGateTierTabs(g1) {
  const host = $("gateTierTabs");
  if (!g1 || !g1.tiers || !g1.tiers.length) {
    host.innerHTML = "";
    return;
  }
  host.innerHTML = g1.tiers.map((t) => {
    const unused = t.seen_in_run ? "" : " unused";
    const active = t.tier === state.selectedGateTier ? " active" : "";
    return `<button type="button" class="tier-tab${active}${unused}" data-tier="${t.tier}">
      ${escapeHtml(t.tier_label)}<span class="sub">${t.displacement_cc} cc · ${t.units} cyl</span>
    </button>`;
  }).join("");
  host.querySelectorAll(".tier-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.selectedGateTier = Number(btn.dataset.tier);
      renderGateViews();
    });
  });
}

function renderGateCompareGrid(g1) {
  const host = $("gateCompareGrid");
  if (!g1 || !g1.tiers || !g1.tiers.length) {
    host.innerHTML = "";
    return;
  }
  host.innerHTML = g1.tiers.map((t) => {
    const m = t.metrics;
    const seen = t.seen_in_run ? "Used in run" : "Design point only";
    const active = t.tier === g1.active_tier ? " active-tier" : "";
    return `<article class="compare-item${active}" data-tier="${t.tier}">
      <h4>${escapeHtml(t.tier_label)} — ${escapeHtml(t.name)}</h4>
      <div class="meta">${t.displacement_cc} cc · CR ${t.compression_ratio} · ${seen}</div>
      <canvas id="comparePv${t.tier}" width="280" height="160"></canvas>
      <div class="stats">
        <span>IMEP</span><span>${fmt(m.imep_bar, 2)} bar</span>
        <span>Peak P</span><span>${fmt(m.peak_pressure_bar, 1)} bar</span>
        <span>Knock</span><span>${fmt(m.knock_index, 3)}</span>
        <span>η elec</span><span>${fmt(m.electric_efficiency * 100, 1)}%</span>
      </div>
    </article>`;
  }).join("");
  g1.tiers.forEach((t) => {
    plotLine(
      "comparePv" + t.tier,
      t.trace.volume_cc,
      t.trace.pressure_bar,
      "#ffd166",
      "",
      ""
    );
  });
}

function renderGateMetricsForTier(t) {
  const host = $("gate1Metrics");
  if (!t) {
    host.innerHTML = "";
    return;
  }
  const m = t.metrics;
  const metrics = [
    ["Tier", t.tier_label + " — " + t.name],
    ["Displacement", t.displacement_cc + " cc × " + t.units + " cyl"],
    ["Compression ratio", String(t.compression_ratio)],
    ["Map load", fmt(t.load_fraction * 100, 1) + " %"],
    ["IMEP", fmt(m.imep_bar, 2) + " bar"],
    ["Knock", fmt(m.knock_index, 3)],
    ["Peak pressure", fmt(m.peak_pressure_bar, 1) + " bar"],
    ["CA50", fmt(m.ca50_deg, 2) + " deg"],
    ["Electric η", fmt(m.electric_efficiency * 100, 1) + " %"],
    ["Table η (tier)", fmt(t.table_efficiency * 100, 1) + " %"],
  ];
  host.innerHTML = metrics.map(([l, v]) =>
    `<div class="cell"><div class="label">${l}</div><div class="value">${v}</div></div>`
  ).join("");
}

function renderGateViews() {
  const gates = state.gates || {};
  const g1 = gates.gate1;
  const statusEl = $("gateStatusText");

  renderGateTierTabs(g1);

  if (!g1 || !g1.has_data || !g1.tiers || !g1.tiers.length) {
    statusEl.textContent = g1 && g1.message ? g1.message :
      "Run a simulation in the Simulator tab to populate Gate 1 visualizations.";
    renderGateMetricsForTier(null);
    plotLine("gate1Pressure", [], [], "#4ea8de", "crank deg", "bar");
    plotLine("gate1Pv", [], [], "#ffd166", "volume cc", "bar");
    plotMultiTrend("gate1Trend", []);
    renderGateCompareGrid(null);
    return;
  }

  const mode = g1.enabled
    ? "Gate 1 enabled — per-step IMEP/knock/TDC from simulation; maps below per tier."
    : "Gate 1 off in simulation — showing design-point traces only (enable checkbox on Simulator).";
  statusEl.textContent = mode;

  const sel = g1.tiers.find((t) => t.tier === state.selectedGateTier) || g1.tiers[0];
  state.selectedGateTier = sel.tier;
  highlightGateTierTabs(state.selectedGateTier);

  renderGateMetricsForTier(sel);
  renderCycleAtCrank(sel, state.crankDeg,
    "gate1Pv", "gate1Pressure", "gateCylinder", "gateCadReadout");
  plotMultiTrend("gate1Trend", state.steps);
  renderGateCompareGrid(g1);
  renderGateRoadmap(gates.roadmap || []);
}

function renderGateMetrics(g1) {
  // kept for compatibility; selection handled in renderGateMetricsForTier
  if (g1 && g1.tiers && g1.tiers.length) {
    const sel = g1.tiers.find((t) => t.tier === state.selectedGateTier) || g1.tiers[0];
    renderGateMetricsForTier(sel);
  }
}

function onLiveCrankScrub(deg) {
  state.scrubManual = true;
  syncCrankScrubbers(Number(deg));
  const tier = activeTierForCad();
  if (tier) {
    renderCycleAtCrank(tier, state.crankDeg,
      "livePv", "livePressure", "liveCylinder", "liveCadReadout");
  }
}

function onGateCrankScrub(deg) {
  syncCrankScrubbers(Number(deg));
  const tier = tierByNum(state.selectedGateTier);
  if (tier) {
    renderCycleAtCrank(tier, state.crankDeg,
      "gate1Pv", "gate1Pressure", "gateCylinder", "gateCadReadout");
  }
}

$("crankScrub").addEventListener("input", (e) => onLiveCrankScrub(e.target.value));
$("gateCrankScrub").addEventListener("input", (e) => onGateCrankScrub(e.target.value));

$("play").addEventListener("click", () => {
  if (state.steps.length && state.i > 0 && state.i < state.steps.length) {
    state.scrubManual = false;
    play();
  } else runSimulation();
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
renderGateViews();
