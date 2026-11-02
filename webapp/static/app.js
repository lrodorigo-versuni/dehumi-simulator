/* Room humidity simulator UI: scenario form, live simulation, sweep. */
(function () {
  "use strict";
  const $ = (sel) => document.querySelector(sel);
  const C = { s1: "var(--series-1)", s2: "var(--series-2)", s3: "var(--series-3)" };
  const SEQ = ["var(--seq-1)", "var(--seq-2)", "var(--seq-3)", "var(--seq-4)", "var(--seq-5)"];
  const STORE_KEY = "humidity-sim-scenario-v2";
  const KPI_ROWS = [
    // key, label, unit, digits, better: +1 higher, -1 lower, 0 neutral
    ["time_in_band_pct", "Time in comfort band", "%", 1, 1],
    ["time_above_band_pct", "Time above band", "%", 1, -1],
    ["time_below_band_pct", "Time below band", "%", 1, 0],
    ["max_rh_pct", "Max RH", "%", 1, -1],
    ["rms_error_pct", "RMS error vs target", "%", 2, -1],
    ["rh_excess_pct_h", "RH excess above band", "%·h", 2, -1],
    ["energy_kwh", "Energy (compressor + fan)", "kWh", 3, -1],
    ["fan_energy_kwh", "of which fan", "kWh", 3, 0],
    ["water_removed_g", "Water removed", "g", 0, 0],
    ["grams_per_kwh", "Water per kWh", "g/kWh", 0, 1],
    ["compressor_starts", "Compressor starts", "", 0, -1],
    ["starts_per_hour", "Starts per hour", "/h", 2, -1],
    ["compressor_on_pct", "Compressor on", "%", 1, 0],
    ["fan_only_pct", "Fan only (compressor off)", "%", 1, 0],
    ["blocked_commands", "Commands blocked by protections", "", 0, -1],
    ["defrost_cycles", "Defrost cycles", "", 0, -1],
    ["defrost_time_pct", "Time defrosting", "%", 1, -1],
    ["max_frost_g", "Max frost on coil", "g", 0, -1],
  ];
  const SWEEP_DEFAULTS = {
    fan_flow_m3_per_h: [30, 1000],
    setpoint_c: [-10, 20],
    max_power_w: [100, 1500],
    effectiveness: [0.2, 1.0],
  };
  const METRICS = {
    final_relative_humidity_pct: ["Final relative humidity", "%", 1],
    water_removed_g: ["Water removed", "g", 0],
    energy_kwh: ["Energy used", "kWh", 3],
    grams_per_kwh: ["Water removed per kWh", "g/kWh", 0],
    average_cop: ["Average COP", "", 2],
    power_limited_min: ["Time at max power", "min", 0],
  };

  let defaults = null;
  let controllers = [];
  let scenario = null;
  let lastResult = null;
  let timer = null;
  let requestId = 0;

  // ---------- scenario path helpers ----------
  const getPath = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
  function setPath(obj, path, value) {
    const keys = path.split(".");
    let o = obj;
    for (const k of keys.slice(0, -1)) o = o[k] = o[k] || {};
    o[keys[keys.length - 1]] = value;
  }
  const num = (v) => (v === "" || v === null ? null : Number(v));

  // ---------- form binding ----------
  function fillForm() {
    for (const input of document.querySelectorAll("[data-path]")) {
      const v = getPath(scenario, input.dataset.path);
      if (input.type === "checkbox") input.checked = !!v;
      else if (input.type === "radio") input.checked = input.value === v;
      else input.value = v ?? "";
    }
    renderCopGrid();
    renderDisturbances();
    renderEvents();
    renderControl();
    syncCompressorVisibility();
  }

  function onFormInput(ev) {
    const input = ev.target;
    if (!input.dataset || !input.dataset.path) return;
    let v;
    if (input.type === "checkbox") v = input.checked;
    else if (input.type === "radio") v = input.value;
    else v = num(input.value);
    setPath(scenario, input.dataset.path, v);
    syncCompressorVisibility();
    schedule();
  }

  function syncCompressorVisibility() {
    const comp = scenario.compressor || {};
    $("#compressor-fields").hidden = !comp.enabled;
    $("#cop-fixed").hidden = comp.cop_mode === "table";
    $("#cop-table-editor").hidden = comp.cop_mode !== "table";
    $("#defrost-fields").hidden = !(comp.defrost && comp.defrost.enabled);
    const onOff = (comp.mode || "on_off") === "on_off";
    $("#setpoint-field").hidden = onOff;
    $("#coil-mass-field").hidden = !onOff;
    $("#comp-mode-hint").textContent = onOff
      ? "Fixed speed: full power whenever it runs. The coil temperature is not set: it follows from the compressor, the fan and the room (and can go below 0 °C)."
      : "Variable capacity: holds the coil at the setpoint when it can, at reduced power.";
    const algo = controllerMode();
    $("#control-fields").hidden = !algo;
    $("#schedule-block").hidden = algo;
    const fanInput = document.querySelector('[data-path="fan.flow_rate_m3_per_h"]');
    fanInput.disabled = algo;
    fanInput.title = algo ? "Set by the algorithm (fan speeds below)" : "";
    for (const el of document.querySelectorAll(".controller-only")) el.hidden = !algo;
    $("#control-mode-hint").textContent = algo
      ? "The algorithm drives the compressor and the fan speed every time step (the time step is the control period)."
      : "The compressor follows the on/off schedule; the fan runs at the fixed flow above.";
    const rh = humidityHint();
    $("#hint-rh").textContent = rh;
  }

  function humidityHint() {
    const r = scenario.room;
    if (!r || !isFinite(r.temperature_c) || !isFinite(r.initial_humidity_gm3)) return "";
    const t = r.temperature_c;
    const es = 6.112 * Math.exp((17.62 * t) / (243.12 + t));
    const sat = (216.7 * es) / (273.15 + t);
    const pct = Math.min(100, (100 * r.initial_humidity_gm3) / sat);
    return `Starts at ≈ ${pct.toFixed(0)}% RH (saturation ${sat.toFixed(1)} g/m³ at ${t} °C)`;
  }

  // ---------- COP table editor ----------
  function copTable() {
    const comp = scenario.compressor;
    comp.cop_table = comp.cop_table || { coil_temperatures_c: [0, 10, 20], room_temperatures_c: [], cops: [[2.5, 3.4, 4.7]] };
    const t = comp.cop_table;
    if (!t.room_temperatures_c) t.room_temperatures_c = [];
    if (t.cops.length && !Array.isArray(t.cops[0])) t.cops = [t.cops];
    return t;
  }

  function cell(value, onChange, cls) {
    const input = document.createElement("input");
    input.type = "number";
    input.step = "any";
    input.value = value ?? "";
    if (cls) input.className = cls;
    input.addEventListener("input", () => { onChange(num(input.value)); schedule(); renderCopChart(); });
    return input;
  }

  function renderCopGrid() {
    const grid = $("#cop-grid");
    grid.textContent = "";
    if (!scenario.compressor) return;
    const t = copTable();
    const twoD = t.room_temperatures_c.length > 0;
    // Axis captions: columns are coil temperatures, rows are room temperatures.
    const caption = grid.insertRow();
    caption.appendChild(document.createElement("th"));
    const colTitle = document.createElement("th");
    colTitle.colSpan = t.coil_temperatures_c.length;
    colTitle.className = "axis-caption";
    colTitle.textContent = "Coil temperature (°C) →";
    caption.appendChild(colTitle);
    const head = grid.insertRow();
    const corner = document.createElement("th");
    corner.className = "axis-caption";
    corner.textContent = twoD ? "Room temp. (°C) ↓" : "";
    head.appendChild(corner);
    t.coil_temperatures_c.forEach((v, j) => {
      const th = document.createElement("th");
      const input = cell(v, (x) => (t.coil_temperatures_c[j] = x), "axis");
      input.title = "Coil temperature (°C)";
      th.appendChild(input);
      head.appendChild(th);
    });
    t.cops.forEach((row, i) => {
      const tr = grid.insertRow();
      const th = document.createElement("th");
      if (twoD) {
        const input = cell(t.room_temperatures_c[i], (x) => (t.room_temperatures_c[i] = x), "axis");
        input.title = "Room temperature (°C)";
        th.appendChild(input);
      } else th.textContent = "COP";
      tr.appendChild(th);
      row.forEach((v, j) => {
        const td = tr.insertCell();
        td.appendChild(cell(v, (x) => (row[j] = x)));
      });
    });
    renderCopChart();
  }

  function copAction(action) {
    const t = copTable();
    const nCoil = t.coil_temperatures_c.length;
    if (action === "add-col") {
      const last = t.coil_temperatures_c[nCoil - 1] ?? 0;
      t.coil_temperatures_c.push(last + 5);
      t.cops.forEach((row) => row.push(row[row.length - 1] ?? 3));
    } else if (action === "del-col" && nCoil > 1) {
      t.coil_temperatures_c.pop();
      t.cops.forEach((row) => row.pop());
    } else if (action === "add-row") {
      if (!t.room_temperatures_c.length) t.room_temperatures_c.push(scenario.room.temperature_c ?? 25);
      const last = t.room_temperatures_c[t.room_temperatures_c.length - 1];
      t.room_temperatures_c.push(last + 10);
      t.cops.push(t.cops[t.cops.length - 1].slice());
    } else if (action === "del-row") {
      if (t.cops.length > 1) { t.cops.pop(); t.room_temperatures_c.pop(); }
      if (t.cops.length === 1) t.room_temperatures_c = [];
    }
    renderCopGrid();
    schedule();
  }

  function renderCopChart() {
    if (!scenario.compressor || scenario.compressor.cop_mode !== "table") return;
    const t = copTable();
    const x = t.coil_temperatures_c.slice();
    if (x.some((v) => v === null || !isFinite(v))) return;
    const rows = t.cops;
    const twoD = t.room_temperatures_c.length > 0;
    const series = rows.map((row, i) => ({
      name: twoD ? `room ${t.room_temperatures_c[i]} °C` : "COP",
      values: row,
      color: rows.length === 1 ? C.s1 : SEQ[Math.round((i * (SEQ.length - 1)) / Math.max(1, rows.length - 1))],
      digits: 2,
    }));
    Charts.line($("#cop-chart"), {
      label: "COP versus coil temperature",
      x, series, markers: true, endLabels: false, xTitle: "°C",
      tooltipTitle: (i) => `coil ${x[i]} °C`,
    });
  }

  // ---------- control algorithm ----------
  const controllerMode = () => !!scenario.control && scenario.control.mode === "controller";

  async function loadControllers() {
    try {
      controllers = (await (await fetch("/api/controllers")).json()).controllers;
    } catch (e) {
      controllers = [];
    }
  }

  function fillSelect(select, selected, allowNone) {
    select.textContent = "";
    if (allowNone) {
      const o = document.createElement("option");
      o.value = ""; o.textContent = "— none —";
      select.appendChild(o);
    }
    const groups = new Map();
    for (const c of controllers) {
      const label = c.source === "built-in baseline" ? "Built-in baselines" : c.source;
      if (!groups.has(label)) {
        const g = document.createElement("optgroup");
        g.label = label;
        groups.set(label, g);
        select.appendChild(g);
      }
      const o = document.createElement("option");
      o.value = c.id;
      o.textContent = c.error ? `${c.name} (failed to load)` : c.name;
      o.disabled = !!c.error;
      o.selected = c.id === selected;
      groups.get(label).appendChild(o);
    }
  }

  function renderParams(box, cid, values) {
    box.textContent = "";
    const info = controllers.find((c) => c.id === cid);
    if (!info) return;
    for (const [name, def] of Object.entries(info.parameters)) {
      const label = document.createElement("label");
      label.className = "field param-field";
      const span = document.createElement("span");
      span.textContent = name.replace(/_/g, " ");
      const input = document.createElement("input");
      input.type = "number"; input.step = "any";
      input.value = values[name] ?? def;
      input.addEventListener("input", () => { values[name] = num(input.value); schedule(); });
      label.append(span, input);
      box.appendChild(label);
    }
  }

  function renderControl() {
    const c = scenario.control;
    if (!c) return;
    c.parameters = c.parameters || {};
    c.compare_parameters = c.compare_parameters || {};
    fillSelect($("#ctrl-select"), c.controller, false);
    fillSelect($("#cmp-select"), c.compare || "", true);
    const info = controllers.find((x) => x.id === c.controller);
    const err = controllers.find((x) => x.error);
    const doc = info && info.doc ? info.doc.replace(/([^\n])\n(?!\n)/g, "$1 ") : "";
    $("#ctrl-doc").textContent = info ? `${info.source} — ${doc}` : "";
    if (err) $("#ctrl-doc").textContent += `\n⚠ ${err.source} failed to load: ${err.error}`;
    renderParams($("#ctrl-params"), c.controller, c.parameters);
    renderParams($("#cmp-params"), c.compare, c.compare_parameters);
    renderFanSpeeds();
  }

  function renderFanSpeeds() {
    const list = $("#fan-speeds");
    list.textContent = "";
    scenario.control.fan_speeds.forEach((f, i) => {
      const row = document.createElement("div");
      row.className = "list-row speed-row";
      const idx = document.createElement("span");
      idx.className = "tag";
      idx.textContent = String(i);
      const name = document.createElement("input");
      name.type = "text"; name.value = f.name; name.setAttribute("aria-label", "Speed name");
      name.addEventListener("input", () => { f.name = name.value; schedule(); });
      row.append(idx, name);
      row.appendChild(labeled("m³/h", cell(f.flow_m3_per_h, (x) => (f.flow_m3_per_h = x))));
      row.appendChild(labeled("W", cell(f.power_w, (x) => (f.power_w = x))));
      row.appendChild(removeButton(() => {
        if (scenario.control.fan_speeds.length > 1) { scenario.control.fan_speeds.splice(i, 1); renderFanSpeeds(); schedule(); }
      }));
      list.appendChild(row);
    });
  }

  // ---------- disturbances & events ----------
  const WINDOW_DEFAULTS = { airflow_m3_per_h: 250, gustiness_pct: 40, gust_time_s: 60, outdoor_temperature_c: 8, outdoor_rh_pct: 85 };

  function renderDisturbances() {
    const list = $("#disturbances");
    list.textContent = "";
    (scenario.disturbances || []).forEach((d, i) => {
      const row = document.createElement("div");
      row.className = "list-row";
      const kind = document.createElement("select");
      for (const [v, label] of [["person", "Person"], ["boiling_water", "Boiling water"], ["window", "Open window"]]) {
        const o = document.createElement("option");
        o.value = v; o.textContent = label; o.selected = d.kind === v;
        kind.appendChild(o);
      }
      kind.addEventListener("change", () => {
        d.kind = kind.value;
        if (d.kind === "window") Object.assign(d, { ...WINDOW_DEFAULTS, ...d, kind: "window" });
        else if (d.moisture_rate_g_per_s == null) d.moisture_rate_g_per_s = d.kind === "person" ? 0.015 : 0.3;
        renderDisturbances(); schedule();
      });
      row.appendChild(kind);
      row.appendChild(removeButton(() => { scenario.disturbances.splice(i, 1); renderDisturbances(); schedule(); }));
      if (d.kind === "window") {
        row.appendChild(labeled("m³/h", cell(d.airflow_m3_per_h, (x) => (d.airflow_m3_per_h = x))));
        row.appendChild(labeled("gusts %", cell(d.gustiness_pct, (x) => (d.gustiness_pct = x))));
        row.appendChild(labeled("outside °C", cell(d.outdoor_temperature_c, (x) => (d.outdoor_temperature_c = x))));
        row.appendChild(labeled("RH %", cell(d.outdoor_rh_pct, (x) => (d.outdoor_rh_pct = x))));
      } else {
        row.appendChild(labeled("g/s", cell(d.moisture_rate_g_per_s, (x) => (d.moisture_rate_g_per_s = x))));
      }
      row.appendChild(labeled("from min", cell(d.start_min, (x) => (d.start_min = x ?? 0))));
      row.appendChild(labeled("until", cell(d.end_min, (x) => (d.end_min = x))));
      list.appendChild(row);
    });
  }

  function renderEvents() {
    const list = $("#events");
    list.textContent = "";
    (scenario.compressor_events || []).forEach((e, i) => {
      const row = document.createElement("div");
      row.className = "list-row events-row";
      row.appendChild(labeled("at min", cell(e.at_min, (x) => (e.at_min = x))));
      const state = document.createElement("select");
      for (const [v, label] of [["running", "Switch on"], ["off", "Switch off"]]) {
        const o = document.createElement("option");
        o.value = v; o.textContent = label; o.selected = e.state === v;
        state.appendChild(o);
      }
      state.addEventListener("change", () => { e.state = state.value; schedule(); });
      row.appendChild(state);
      row.appendChild(removeButton(() => { scenario.compressor_events.splice(i, 1); renderEvents(); schedule(); }));
      list.appendChild(row);
    });
  }

  function labeled(text, input) {
    const wrap = document.createElement("label");
    wrap.className = "mini";
    wrap.append(text + " ", input);
    return wrap;
  }

  function removeButton(onClick) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = "✕";
    b.setAttribute("aria-label", "Remove");
    b.addEventListener("click", onClick);
    return b;
  }

  // ---------- running ----------
  function schedule() {
    localStorage.setItem(STORE_KEY, JSON.stringify(scenario));
    clearTimeout(timer);
    timer = setTimeout(simulate, 250);
  }

  async function post(url, body) {
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return res.json();
  }

  function showError(err) {
    for (const el of document.querySelectorAll("input.invalid")) el.classList.remove("invalid");
    const box = $("#error");
    if (!err) { box.hidden = true; return; }
    box.hidden = false;
    box.textContent = `${err.field}: ${err.message}`;
    const target = document.querySelector(`[data-path="${err.field}"]`);
    if (target) target.classList.add("invalid");
  }

  async function simulate() {
    const id = ++requestId;
    document.body.classList.add("loading");
    $("#status").textContent = "Simulating…";
    const t0 = performance.now();
    try {
      const out = await post("/api/simulate", scenario);
      if (id !== requestId) return;
      if (out.error) {
        showError(out.error);
        $("#status").textContent = "Fix the highlighted input";
        return;
      }
      showError(null);
      lastResult = out;
      renderResult(out);
      $("#status").textContent = `${out.summary.steps} steps · ${Math.round(performance.now() - t0)} ms`;
    } catch (e) {
      $("#status").textContent = "Server not reachable";
    } finally {
      if (id === requestId) document.body.classList.remove("loading");
    }
  }

  function runningBands(s) {
    const bands = [];
    let start = null;
    for (let i = 1; i < s.t_min.length; i++) {
      if (s.running[i] && start === null) start = s.t_min[i - 1];
      if (!s.running[i] && start !== null) { bands.push([start, s.t_min[i - 1]]); start = null; }
    }
    if (start !== null) bands.push([start, s.t_min[s.t_min.length - 1]]);
    return bands;
  }

  let currentMode = "on_off";
  const out_mode = () => currentMode;

  function tiles(sum) {
    const box = $("#tiles");
    box.textContent = "";
    const items = [
      ["Final relative humidity", Charts.fmt(sum.final_relative_humidity_pct, 1) + " %", `${Charts.fmt(sum.final_absolute_humidity_g, 0)} g of vapor`],
      ["Water removed", Charts.fmt(sum.water_removed_g, 0) + " g", `${Charts.fmt(sum.water_added_g, 0)} g added by sources`],
      ["Energy used", Charts.fmt(sum.energy_kwh, 3) + " kWh", `compressor on ${Charts.fmt(sum.running_min, 0)} min`],
      ["Water per kWh", sum.grams_per_kwh === null ? "–" : Charts.fmt(sum.grams_per_kwh, 0) + " g", "dehumidification efficiency"],
      ["Average COP", sum.average_cop === null ? "–" : Charts.fmt(sum.average_cop, 2), "heat removed ÷ electricity"],
      out_mode() === "modulating"
        ? ["Time at max power", Charts.fmt(sum.power_limited_min, 0) + " min", "coil held above setpoint"]
        : ["Compressor on", Charts.fmt(sum.running_min, 0) + " min", "always at full power"],
      ["Defrost", `${Charts.fmt(sum.defrost_cycles ?? 0, 0)} cycles`, `${Charts.fmt(sum.defrost_min ?? 0, 0)} min stopped · max ${Charts.fmt(sum.max_frost_g ?? 0, 0)} g ice`],
    ];
    for (const [label, value, sub] of items) {
      const tile = document.createElement("div");
      tile.className = "tile";
      for (const [cls, text] of [["label", label], ["value", value], ["sub", sub]]) {
        const d = document.createElement("div");
        d.className = cls;
        d.textContent = text;
        tile.appendChild(d);
      }
      box.appendChild(tile);
    }
  }

  function kpiTiles(k) {
    const box = $("#tiles");
    box.textContent = "";
    const items = [
      ["Time in comfort band", Charts.fmt(k.time_in_band_pct, 1) + " %", `${Charts.fmt(k.time_above_band_pct, 1)} % above · ${Charts.fmt(k.time_below_band_pct, 1)} % below`],
      ["Max RH", Charts.fmt(k.max_rh_pct, 1) + " %", `RMS error ${Charts.fmt(k.rms_error_pct, 2)} %`],
      ["Energy used", Charts.fmt(k.energy_kwh, 3) + " kWh", `fan ${Charts.fmt(k.fan_energy_kwh, 3)} kWh`],
      ["Water per kWh", k.grams_per_kwh === null ? "–" : Charts.fmt(k.grams_per_kwh, 0) + " g", `${Charts.fmt(k.water_removed_g, 0)} g removed`],
      ["Compressor starts", Charts.fmt(k.compressor_starts, 0), `${Charts.fmt(k.starts_per_hour, 2)} per hour`],
      ["Blocked commands", Charts.fmt(k.blocked_commands, 0), "refused by protections"],
      ["Defrost", `${Charts.fmt(k.defrost_cycles, 0)} cycles`, `${Charts.fmt(k.defrost_time_pct, 1)} % of the time · max ${Charts.fmt(k.max_frost_g, 0)} g ice`],
    ];
    for (const [label, value, sub] of items) {
      const tile = document.createElement("div");
      tile.className = "tile";
      for (const [cls, text] of [["label", label], ["value", value], ["sub", sub]]) {
        const d = document.createElement("div");
        d.className = cls;
        d.textContent = text;
        tile.appendChild(d);
      }
      box.appendChild(tile);
    }
  }

  function kpiTable(out) {
    const table = $("#kpi-table");
    table.textContent = "";
    const head = table.createTHead().insertRow();
    for (const t of ["KPI", out.controller_name + " (A)", out.compare.controller_name + " (B)", "B − A"]) {
      const th = document.createElement("th");
      th.textContent = t;
      head.appendChild(th);
    }
    const body = table.createTBody();
    const a = out.kpis, b = out.compare.kpis;
    for (const [key, label, unit, digits, better] of KPI_ROWS) {
      const tr = body.insertRow();
      tr.insertCell().textContent = unit ? `${label} (${unit})` : label;
      const ca = tr.insertCell(), cb = tr.insertCell();
      ca.textContent = Charts.fmt(a[key], digits);
      cb.textContent = Charts.fmt(b[key], digits);
      const delta = a[key] === null || b[key] === null ? null : b[key] - a[key];
      tr.insertCell().textContent = delta === null ? "–" : (delta > 0 ? "+" : "") + Charts.fmt(delta, digits);
      if (better && delta !== null && Math.abs(delta) > Math.pow(10, -digits) / 2) {
        ((delta * better > 0) ? cb : ca).classList.add("better");
      }
    }
  }

  function renderResult(out) {
    const s = out.series;
    const x = s.t_min;
    const bands = runningBands(s);
    const algo = !!out.kpis;
    currentMode = out.compressor_mode || "on_off";
    $("#kpi-compare").hidden = !(algo && out.compare);
    $("#tiles").hidden = !!(algo && out.compare);
    if (!algo) tiles(out.summary);
    else if (out.compare) kpiTable(out);
    else kpiTiles(out.kpis);
    const title = (i) => `${Charts.fmt(x[i], 1)} min`;
    const cmp = algo && out.compare ? out.compare : null;
    const stateText = (ser, i) => {
      const limitedText = out.compressor_mode === "modulating" ? "on, at max power" : "on";
      const parts = [ser.defrosting && ser.defrosting[i] ? "defrosting" : ser.running[i] ? (ser.power_limited[i] ? limitedText : "on") : "off"];
      if (ser.frost_g && ser.frost_g[i] > 0.05) parts.push(`${Charts.fmt(ser.frost_g[i], 0)} g frost`);
      return parts.join(" · ");
    };
    const notes = (i) => {
      if (cmp) return [`Compressor A: ${stateText(s, i)}`, `Compressor B: ${stateText(cmp.series, i)}`];
      if (!out.compressor_mode) return [];
      return [`Compressor: ${stateText(s, i)}`];
    };
    $("#band-note").textContent = cmp ? `Shaded: compressor of A (${out.controller_name}) running` : "Shaded: compressor running";
    for (const tag of document.querySelectorAll(".only-a")) tag.hidden = !cmp;
    const common = { x, group: "sim", bands, xTitle: "min", tooltipTitle: title, notes };
    const rhSeries = [{ name: algo ? `${out.controller_name} (A)` : "Relative humidity", values: s.relative_humidity_pct, color: C.s1, unit: "%", digits: 1 }];
    if (algo && out.compare) rhSeries.push({ name: `${out.compare.controller_name} (B)`, values: out.compare.series.relative_humidity_pct, color: C.s2, unit: "%", digits: 1 });
    else if (algo && s.rh_measured_pct.some((v, i) => Math.abs(v - s.relative_humidity_pct[i]) > 0.05)) rhSeries.push({ name: "Measured by sensor", values: s.rh_measured_pct, color: C.s2, unit: "%", digits: 1 });
    Charts.line($("#c-rh"), { ...common, label: "Relative humidity over time", y: { min: 0, max: 100, clampMax: 100 },
      refs: algo ? [{ y: out.band.low_pct, label: "band low" }, { y: out.band.high_pct, label: "band high" }] : [],
      series: rhSeries });
    if (algo) {
      const withB = (name, key, unit, digits) => {
        const list = [{ name: out.compare ? `${out.controller_name} (A)` : name, values: s[key], color: C.s1, unit, digits }];
        if (out.compare) list.push({ name: `${out.compare.controller_name} (B)`, values: out.compare.series[key], color: C.s2, unit, digits });
        return list;
      };
      Charts.line($("#c-fan"), { ...common, label: "Fan flow chosen by the algorithm", y: { zero: true }, series: withB("Fan flow", "fan_flow_m3_per_h", "m³/h", 0) });
      Charts.line($("#c-energy"), { ...common, label: "Cumulative energy", y: { zero: true }, series: withB("Energy", "energy_kwh", "kWh", 3) });
    }
    Charts.line($("#c-abs"), { ...common, label: "Water vapor in the room", y: { zero: true },
      refs: [{ y: out.summary.saturation_g, label: "saturation" }],
      series: [{ name: "Water vapor", values: s.absolute_humidity_g, color: C.s1, unit: "g", digits: 0 }] });
    Charts.line($("#c-flow"), { ...common, label: "Moisture added and removed", y: { zero: true },
      series: [
        { name: "Sources (net, window can be < 0)", values: s.added_g_per_min, color: C.s2, unit: "g/min", digits: 2 },
        { name: "Removed by coil", values: s.removed_g_per_min, color: C.s1, unit: "g/min", digits: 2 },
      ] });
    Charts.line($("#c-temp"), { ...common, label: "Coil, dew point and room temperature",
      refs: out.setpoint_c === null ? [] : [{ y: out.setpoint_c, label: "setpoint" }],
      series: [
        { name: "Coil", values: s.coil_temperature_c, color: C.s1, unit: "°C", digits: 1 },
        { name: "Dew point", values: s.dew_point_c, color: C.s2, unit: "°C", digits: 1 },
        { name: "Room", values: s.room_temperature_c, color: C.s3, unit: "°C", digits: 1 },
      ] });
    const powerSeries = [{ name: cmp ? `${out.controller_name} (A)` : "Power", values: s.power_w, color: C.s1, unit: "W", digits: 0 }];
    if (cmp) powerSeries.push({ name: `${cmp.controller_name} (B)`, values: cmp.series.power_w, color: C.s2, unit: "W", digits: 0 });
    Charts.line($("#c-power"), { ...common, label: "Compressor electrical power", y: { zero: true },
      refs: out.max_power_w ? [{ y: out.max_power_w, label: "max power" }] : [],
      series: powerSeries });
    const modulating = out.compressor_mode === "modulating";
    const stateLanes = [{ name: cmp ? "A" : "on", color: C.s1, on: s.running, strong: modulating ? s.power_limited : null, marks: s.defrosting }];
    if (cmp) stateLanes.push({ name: "B", color: C.s2, on: cmp.series.running, strong: modulating ? cmp.series.power_limited : null, marks: cmp.series.defrosting });
    Charts.lanes($("#c-state"), {
      x, group: "sim", xTitle: "min", tooltipTitle: title, notes,
      label: cmp ? "Compressor state of A and B" : "Compressor state",
      onLabel: modulating ? "running" : "running (full power)", strongLabel: modulating ? "at max power" : null, markLabel: "defrosting", lanes: stateLanes,
    });
    Charts.line($("#c-cop"), { ...common, label: "Compressor COP", emptyText: "Compressor never ran",
      series: [{ name: "COP", values: s.cop, color: C.s1, digits: 2 }] });
    renderTable(s);
  }

  const TABLE_COLUMNS = [
    ["t_min", "time (min)", 1], ["relative_humidity_pct", "RH (%)", 1], ["absolute_humidity_g", "vapor (g)", 1],
    ["added_g_per_min", "sources net (g/min)", 2], ["removed_g_per_min", "removed (g/min)", 2],
    ["coil_temperature_c", "coil (°C)", 1], ["dew_point_c", "dew point (°C)", 1], ["power_w", "power (W)", 0],
    ["cop", "COP", 2], ["energy_kwh", "energy (kWh)", 3], ["running", "compressor", null], ["power_limited", "max power", null],
    ["frost_g", "frost (g)", 1], ["defrosting", "defrost", null],
  ];

  function renderTable(s) {
    const table = $("#data-table");
    table.textContent = "";
    const head = table.createTHead().insertRow();
    for (const [, label] of TABLE_COLUMNS) {
      const th = document.createElement("th");
      th.textContent = label;
      head.appendChild(th);
    }
    const body = table.createTBody();
    for (let i = 0; i < s.t_min.length; i++) {
      const tr = body.insertRow();
      for (const [key, , digits] of TABLE_COLUMNS) {
        const v = s[key][i];
        tr.insertCell().textContent = digits === null ? (v ? "yes" : "–") : Charts.fmt(v, digits);
      }
    }
  }

  function downloadCsv(ev) {
    ev.preventDefault();
    if (!lastResult) return;
    const s = lastResult.series;
    const lines = [TABLE_COLUMNS.map((c) => c[0]).join(",")];
    for (let i = 0; i < s.t_min.length; i++) lines.push(TABLE_COLUMNS.map(([k]) => (s[k][i] ?? "")).join(","));
    const url = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url; a.download = "humidity-simulation.csv"; a.click();
    URL.revokeObjectURL(url);
  }

  // ---------- sweep ----------
  function initSweep() {
    const param = $("#sweep-param");
    for (const [k, v] of Object.entries(defaults.sweep_parameters)) {
      const o = document.createElement("option");
      o.value = k; o.textContent = v.label + (v.unit ? ` (${v.unit})` : "");
      param.appendChild(o);
    }
    const metric = $("#sweep-metric");
    for (const [k, [label]] of Object.entries(METRICS)) {
      const o = document.createElement("option");
      o.value = k; o.textContent = label;
      metric.appendChild(o);
    }
    const setRange = () => { const [a, b] = SWEEP_DEFAULTS[param.value]; $("#sweep-from").value = a; $("#sweep-to").value = b; };
    param.addEventListener("change", setRange);
    metric.addEventListener("change", () => sweepResult && renderSweep(sweepResult));
    setRange();
    $("#sweep-run").addEventListener("click", runSweep);
  }

  let sweepResult = null;
  async function runSweep() {
    $("#status").textContent = "Running sweep…";
    document.body.classList.add("loading");
    const t0 = performance.now();
    const out = await post("/api/sweep", {
      scenario, parameter: $("#sweep-param").value,
      from: num($("#sweep-from").value), to: num($("#sweep-to").value), points: num($("#sweep-points").value),
    });
    document.body.classList.remove("loading");
    if (out.error) { showError(out.error); $("#status").textContent = "Sweep failed"; return; }
    showError(null);
    sweepResult = out;
    renderSweep(out);
    $("#status").textContent = `${out.rows.length} runs · ${Math.round(performance.now() - t0)} ms`;
  }

  function renderSweep(out) {
    const key = $("#sweep-metric").value;
    const [label, unit, digits] = METRICS[key];
    const x = out.rows.map((r) => r.value);
    $("#sweep-title").textContent = `${label} by ${out.label.toLowerCase()}`;
    $("#sweep-sub").textContent = `${unit ? unit + " · " : ""}x: ${out.unit || out.label}`;
    Charts.line($("#c-sweep"), {
      label: `${label} versus ${out.label}`, x, markers: true, xTitle: out.unit,
      tooltipTitle: (i) => `${out.label} ${Charts.fmt(x[i], 2)} ${out.unit}`,
      notes: (i) => [`RH ${Charts.fmt(out.rows[i].final_relative_humidity_pct, 1)} % · ${Charts.fmt(out.rows[i].energy_kwh, 3)} kWh · ${Charts.fmt(out.rows[i].water_removed_g, 0)} g removed`],
      series: [{ name: label, values: out.rows.map((r) => r[key]), color: C.s1, unit, digits }],
    });
    const table = $("#sweep-table");
    table.textContent = "";
    const head = table.createTHead().insertRow();
    const cols = [["value", `${out.label} (${out.unit || "–"})`, 2], ...Object.entries(METRICS).map(([k, [l, u, d]]) => [k, u ? `${l} (${u})` : l, d])];
    for (const [, l] of cols) { const th = document.createElement("th"); th.textContent = l; head.appendChild(th); }
    const body = table.createTBody();
    for (const r of out.rows) {
      const tr = body.insertRow();
      for (const [k, , d] of cols) tr.insertCell().textContent = Charts.fmt(r[k], d);
    }
  }

  // ---------- chrome ----------
  function selectTab(which) {
    Charts.hideTooltip();
    for (const [tab, view] of [["#tab-sim", "#view-sim"], ["#tab-sweep", "#view-sweep"]]) {
      const on = tab === which;
      $(tab).setAttribute("aria-selected", String(on));
      $(view).hidden = !on;
    }
  }

  function toggleTheme() {
    const root = document.documentElement;
    const dark = root.dataset.theme
      ? root.dataset.theme === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    localStorage.setItem("humidity-sim-theme", root.dataset.theme);
    if (lastResult) renderResult(lastResult);
  }

  async function init() {
    const saved = localStorage.getItem("humidity-sim-theme");
    if (saved) document.documentElement.dataset.theme = saved;
    defaults = await (await fetch("/api/defaults")).json();
    await loadControllers();
    try { scenario = JSON.parse(localStorage.getItem(STORE_KEY)) || null; } catch (e) { scenario = null; }
    scenario = scenario || structuredClone(defaults.scenario);
    if (!scenario.control) scenario.control = structuredClone(defaults.scenario.control);
    if (scenario.compressor && !scenario.compressor.defrost) scenario.compressor.defrost = structuredClone(defaults.scenario.compressor.defrost);
    if (scenario.compressor && !scenario.compressor.mode) {
      scenario.compressor.mode = defaults.scenario.compressor.mode;
      scenario.compressor.coil_heat_capacity_j_per_k = defaults.scenario.compressor.coil_heat_capacity_j_per_k;
    }
    // Saved scenarios from before the coil limit moved to L/day (1 g/s = 86.4 L/day).
    if (scenario.coil && scenario.coil.max_condensation_l_per_day === undefined) {
      const gps = scenario.coil.condensation_capacity_g_per_s;
      scenario.coil.max_condensation_l_per_day = gps == null ? defaults.scenario.coil.max_condensation_l_per_day : gps * 86.4;
      delete scenario.coil.condensation_capacity_g_per_s;
    }
    fillForm();
    initSweep();
    $("#form").addEventListener("input", onFormInput);
    $("#form").addEventListener("change", onFormInput);
    // Scrolling over a focused number field must not silently change it.
    document.addEventListener("wheel", (ev) => {
      if (ev.target instanceof HTMLInputElement && ev.target.type === "number" && document.activeElement === ev.target) ev.target.blur();
    }, { passive: true });
    for (const b of document.querySelectorAll("[data-cop]")) b.addEventListener("click", () => copAction(b.dataset.cop));
    $("#add-person").addEventListener("click", () => { scenario.disturbances.push({ kind: "person", moisture_rate_g_per_s: 0.015, start_min: 0, end_min: null }); renderDisturbances(); schedule(); });
    $("#add-window").addEventListener("click", () => { scenario.disturbances.push({ kind: "window", ...WINDOW_DEFAULTS, start_min: 30, end_min: 45 }); renderDisturbances(); schedule(); });
    $("#add-kettle").addEventListener("click", () => { scenario.disturbances.push({ kind: "boiling_water", moisture_rate_g_per_s: 0.3, start_min: 0, end_min: 15 }); renderDisturbances(); schedule(); });
    $("#ctrl-select").addEventListener("change", (ev) => { scenario.control.controller = ev.target.value; scenario.control.parameters = {}; renderControl(); schedule(); });
    $("#cmp-select").addEventListener("change", (ev) => { scenario.control.compare = ev.target.value || null; scenario.control.compare_parameters = {}; renderControl(); schedule(); });
    $("#reload-ctrl").addEventListener("click", async () => { await loadControllers(); renderControl(); schedule(); });
    $("#add-speed").addEventListener("click", () => { const l = scenario.control.fan_speeds; const last = l[l.length - 1]; l.push({ name: "speed " + l.length, flow_m3_per_h: (last ? last.flow_m3_per_h : 0) + 50, power_w: (last ? last.power_w : 0) + 10 }); renderFanSpeeds(); schedule(); });
    $("#add-event").addEventListener("click", () => { (scenario.compressor_events = scenario.compressor_events || []).push({ at_min: 0, state: "running" }); renderEvents(); schedule(); });
    $("#reset").addEventListener("click", () => { scenario = structuredClone(defaults.scenario); fillForm(); schedule(); });
    $("#tab-sim").addEventListener("click", () => selectTab("#tab-sim"));
    $("#tab-sweep").addEventListener("click", () => selectTab("#tab-sweep"));
    $("#theme-toggle").addEventListener("click", toggleTheme);
    $("#download-csv").addEventListener("click", downloadCsv);
    simulate();
  }

  init();
})();
