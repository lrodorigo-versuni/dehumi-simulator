/* Minimal SVG line charts: one y-axis per chart, synced crosshair, tooltips.
 * Text always uses text tokens; series colors are only on marks and keys.
 * Labels are inserted with textContent (never innerHTML).
 */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const groups = {};
  const tooltip = () => document.getElementById("tooltip");

  function svgEl(tag, attrs, parent) {
    const el = document.createElementNS(NS, tag);
    for (const k in attrs) el.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(el);
    return el;
  }

  function niceStep(span, count) {
    const raw = span / Math.max(count, 1);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const n = raw / mag;
    return (n >= 7.5 ? 10 : n >= 3.5 ? 5 : n >= 1.5 ? 2 : 1) * mag;
  }

  function niceDomain(lo, hi, count) {
    if (!isFinite(lo) || !isFinite(hi)) return { lo: 0, hi: 1, step: 0.2 };
    // A span that is only floating-point noise (e.g. 399.99999999999994 vs
    // 400) is a constant series: pad it instead of building a sub-ulp step.
    const scale = Math.max(Math.abs(lo), Math.abs(hi));
    if (hi - lo <= scale * 1e-9) { const pad = scale * 0.1 || 1; lo -= pad; hi += pad; }
    const step = niceStep(hi - lo, count);
    if (!(step > 0) || !isFinite(step)) return { lo, hi, step: hi - lo || 1 };
    return { lo: Math.floor(lo / step) * step, hi: Math.ceil(hi / step) * step, step };
  }

  // Tick values from..to every step, computed by index (no accumulated
  // error) and capped, so a degenerate domain can never loop forever.
  function ticks(from, to, step, max = 60) {
    const out = [];
    if (!(step > 0) || !isFinite(from) || !isFinite(to)) return out;
    const n = Math.floor((to - from) / step + 1e-6);
    for (let i = 0; i <= Math.min(n, max); i++) out.push(from + i * step);
    return out;
  }

  function fmt(v, digits) {
    if (v === null || v === undefined || !isFinite(v)) return "–";
    const a = Math.abs(v);
    if (a >= 10000) return (v / 1000).toFixed(a >= 100000 ? 0 : 1) + "K";
    return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  function tickFmt(v, step) {
    const a = Math.abs(v);
    if (a >= 1000) return fmt(v, 0);
    const d = step >= 1 ? 0 : Math.min(3, Math.ceil(-Math.log10(step)));
    return v.toFixed(d);
  }

  function line(el, spec) {
    const inst = { el, spec, index: null };
    if (el._chart && el._chart.observer) el._chart.observer.disconnect();
    el._chart = inst;
    const group = spec.group;
    if (group) {
      groups[group] = (groups[group] || []).filter((c) => c.el.isConnected && c.el._chart === c);
      groups[group].push(inst);
    }
    render(inst);
    inst.observer = new ResizeObserver(() => render(inst));
    inst.observer.observe(el);
    return inst;
  }

  function render(inst) {
    const { el, spec } = inst;
    const width = el.clientWidth;
    if (!width) return;
    if (spec.kind === "lanes") return renderLanes(inst);
    el.textContent = "";
    el.tabIndex = 0;
    el.setAttribute("role", "img");
    el.setAttribute("aria-label", spec.label || "chart");
    const series = spec.series.filter((s) => s.values && s.values.some((v) => v !== null && v !== undefined));
    if (series.length >= 2) {
      const legend = document.createElement("div");
      legend.className = "legend";
      for (const s of series) {
        const item = document.createElement("span");
        const key = document.createElement("i");
        key.style.background = s.color;
        item.appendChild(key);
        item.appendChild(document.createTextNode(s.name));
        legend.appendChild(item);
      }
      el.appendChild(legend);
    }
    const legendEl = el.querySelector(".legend");
    const height = el.clientHeight - (legendEl ? legendEl.offsetHeight + 2 : 0);
    const x = spec.x || [];
    if (!series.length || x.length < 2) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = spec.emptyText || "No data";
      el.appendChild(empty);
      return;
    }
    const m = { l: 46, r: spec.endLabels === false ? 14 : 58, t: 8, b: 26 };
    const W = width - m.l - m.r;
    const H = height - m.t - m.b;
    const svg = svgEl("svg", { width, height, viewBox: `0 0 ${width} ${height}` }, el);

    let ylo = Infinity, yhi = -Infinity;
    for (const s of series) for (const v of s.values) if (v !== null && v !== undefined) { ylo = Math.min(ylo, v); yhi = Math.max(yhi, v); }
    for (const r of spec.refs || []) if (r.y !== null && isFinite(r.y)) { ylo = Math.min(ylo, r.y); yhi = Math.max(yhi, r.y); }
    if (spec.y && spec.y.min !== undefined) ylo = Math.min(ylo, spec.y.min);
    if (spec.y && spec.y.max !== undefined) yhi = Math.max(yhi, spec.y.max);
    if (spec.y && spec.y.zero) ylo = Math.min(ylo, 0);
    const yd = niceDomain(ylo, yhi, Math.max(2, Math.floor(H / 40)));
    if (spec.y && spec.y.clampMax !== undefined) yd.hi = Math.min(yd.hi, spec.y.clampMax);
    const xlo = x[0], xhi = x[x.length - 1];
    const xs = (v) => m.l + ((v - xlo) / (xhi - xlo || 1)) * W;
    const ys = (v) => m.t + H - ((v - yd.lo) / (yd.hi - yd.lo || 1)) * H;
    inst.xs = xs; inst.ys = ys; inst.series = series; inst.m = m; inst.W = W; inst.H = H;

    for (const [a, b] of spec.bands || []) {
      svgEl("rect", { class: "band", x: xs(a), y: m.t, width: Math.max(0, xs(b) - xs(a)), height: H }, svg);
    }
    for (const v of ticks(yd.lo, yd.hi, yd.step)) {
      svgEl("line", { class: "grid-line", x1: m.l, x2: m.l + W, y1: ys(v), y2: ys(v) }, svg);
      const t = svgEl("text", { class: "tick", x: m.l - 6, y: ys(v) + 4, "text-anchor": "end" }, svg);
      t.textContent = tickFmt(v, yd.step);
    }
    const xd = niceDomain(xlo, xhi, Math.max(2, Math.floor(W / 80)));
    for (const v of ticks(Math.ceil(xlo / xd.step) * xd.step, xhi, xd.step)) {
      // Leave room for the axis title at the right end.
      if (spec.xTitle && xs(v) > m.l + W - 18) continue;
      const t = svgEl("text", { class: "tick", x: xs(v), y: m.t + H + 16, "text-anchor": "middle" }, svg);
      t.textContent = spec.xFormat ? spec.xFormat(v) : tickFmt(v, xd.step);
    }
    svgEl("line", { class: "axis-line", x1: m.l, x2: m.l + W, y1: m.t + H, y2: m.t + H }, svg);
    if (spec.xTitle) {
      const t = svgEl("text", { class: "axis-title", x: m.l + W, y: m.t + H + 16, "text-anchor": "end" }, svg);
      t.textContent = spec.xTitle;
    }

    for (const r of spec.refs || []) {
      if (r.y === null || !isFinite(r.y)) continue;
      svgEl("line", { class: "ref", x1: m.l, x2: m.l + W, y1: ys(r.y), y2: ys(r.y) }, svg);
      const t = svgEl("text", { class: "ref-label", x: m.l + 4, y: ys(r.y) - 4 }, svg);
      t.textContent = r.label;
    }

    for (const s of series) {
      let d = "";
      let pen = false;
      s.values.forEach((v, i) => {
        if (v === null || v === undefined) { pen = false; return; }
        d += (pen ? "L" : "M") + xs(x[i]).toFixed(1) + "," + ys(v).toFixed(1);
        pen = true;
      });
      svgEl("path", { class: "series", d, style: `stroke:${s.color}` }, svg);
      if (spec.markers) {
        s.values.forEach((v, i) => {
          if (v === null || v === undefined) return;
          svgEl("circle", { class: "dot", cx: xs(x[i]), cy: ys(v), r: 4, style: `fill:${s.color}` }, svg);
        });
      }
    }

    // Value at the end of each line, skipped where it would collide.
    if (spec.endLabels !== false && series.length <= 4) {
      const placed = [];
      for (const s of series) {
        let i = s.values.length - 1;
        while (i >= 0 && (s.values[i] === null || s.values[i] === undefined)) i--;
        if (i < 0) continue;
        const y = ys(s.values[i]);
        if (placed.some((p) => Math.abs(p - y) < 13)) continue;
        placed.push(y);
        const t = svgEl("text", { class: "end-label", x: m.l + W + 6, y: y + 4 }, svg);
        t.textContent = fmt(s.values[i], s.digits ?? 1) + (s.unit ? " " + s.unit : "");
      }
    }

    attachHover(inst, svg, x, xs, m, W, H);
  }

  // Crosshair + tooltip shared by every chart type (pointer and arrow keys).
  function attachHover(inst, svg, x, xs, m, W, H) {
    const el = inst.el;
    const xlo = x[0], xhi = x[x.length - 1];
    inst.hover = svgEl("g", {}, svg);
    const hit = svgEl("rect", { x: m.l, y: m.t, width: W, height: H, fill: "transparent" }, svg);
    const nearest = (px) => {
      const v = xlo + ((px - m.l) / W) * (xhi - xlo);
      let lo = 0, hi = x.length - 1;
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (x[mid] < v) lo = mid; else hi = mid; }
      return Math.abs(x[lo] - v) <= Math.abs(x[hi] - v) ? lo : hi;
    };
    hit.addEventListener("pointermove", (ev) => {
      const rect = svg.getBoundingClientRect();
      broadcast(inst, nearest(ev.clientX - rect.left), ev.clientX, ev.clientY);
    });
    hit.addEventListener("pointerleave", () => broadcast(inst, null));
    el.onkeydown = (ev) => {
      if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
      ev.preventDefault();
      const i = inst.index === null ? x.length - 1 : Math.max(0, Math.min(x.length - 1, inst.index + (ev.key === "ArrowRight" ? 1 : -1)));
      const r = svg.getBoundingClientRect();
      broadcast(inst, i, r.left + xs(x[i]), r.top + m.t + 20);
    };
    el.onblur = () => broadcast(inst, null);
    if (inst.index !== null) showIndex(inst, inst.index);
  }

  /* On/off lanes: one row per entity, a filled run while "on" and a solid
   * run while "strong" (e.g. at max power) and a muted run while "marks"
   * (e.g. defrosting). Point i covers the step that
   * ends at x[i]. Spec: {x, group, lanes: [{name, color, on, strong}],
   * onLabel, strongLabel, tooltipTitle, notes, xTitle}. */
  function lanes(el, spec) {
    return line(el, Object.assign({}, spec, { kind: "lanes", series: [] }));
  }

  function renderLanes(inst) {
    const { el, spec } = inst;
    const width = el.clientWidth;
    el.textContent = "";
    el.tabIndex = 0;
    el.setAttribute("role", "img");
    el.setAttribute("aria-label", spec.label || "state chart");
    const legend = document.createElement("div");
    legend.className = "legend";
    const keys = [["lane-key on", spec.onLabel || "on"]];
    if (spec.strongLabel) keys.push(["lane-key strong", spec.strongLabel]);
    if (spec.markLabel && spec.lanes.some((l) => l.marks && l.marks.some(Boolean))) keys.push(["lane-key mark", spec.markLabel]);
    for (const [cls, text] of keys) {
      const item = document.createElement("span");
      const key = document.createElement("i");
      key.className = cls;
      item.append(key, document.createTextNode(text));
      legend.appendChild(item);
    }
    el.appendChild(legend);
    const x = spec.x || [];
    if (x.length < 2) return;
    const height = el.clientHeight - legend.offsetHeight - 2;
    const m = { l: 46, r: 58, t: 6, b: 26 };
    const W = width - m.l - m.r;
    const H = height - m.t - m.b;
    const svg = svgEl("svg", { width, height, viewBox: `0 0 ${width} ${height}` }, el);
    const xlo = x[0], xhi = x[x.length - 1];
    const xs = (v) => m.l + ((v - xlo) / (xhi - xlo || 1)) * W;
    inst.xs = xs; inst.ys = () => 0; inst.series = []; inst.m = m; inst.W = W; inst.H = H;
    const n = spec.lanes.length;
    const gap = 6;
    const laneH = Math.min(26, (H - gap * (n - 1)) / n);
    const top0 = m.t + (H - (laneH * n + gap * (n - 1))) / 2;
    spec.lanes.forEach((lane, k) => {
      const y = top0 + k * (laneH + gap);
      svgEl("rect", { class: "lane-track", x: m.l, y, width: W, height: laneH, rx: 3 }, svg);
      const label = svgEl("text", { class: "tick", x: m.l - 6, y: y + laneH / 2 + 4, "text-anchor": "end" }, svg);
      label.textContent = lane.name;
      for (const [values, cls] of [[lane.on, "lane-on"], [lane.strong || [], "lane-strong"], [lane.marks || [], "lane-mark"]]) {
        let start = null;
        for (let i = 1; i <= x.length; i++) {
          const v = i < x.length && values[i];
          if (v && start === null) start = x[i - 1];
          if (!v && start !== null) {
            const x0 = xs(start), x1 = xs(x[i - 1]);
            const attrs = { class: cls, x: x0, y: y + 1, width: Math.max(1, x1 - x0), height: laneH - 2 };
            if (cls !== "lane-mark") attrs.style = `fill:${lane.color}`;
            svgEl("rect", attrs, svg);
            start = null;
          }
        }
      }
    });
    const xd = niceDomain(xlo, xhi, Math.max(2, Math.floor(W / 80)));
    for (const v of ticks(Math.ceil(xlo / xd.step) * xd.step, xhi, xd.step)) {
      if (spec.xTitle && xs(v) > m.l + W - 18) continue;
      const t = svgEl("text", { class: "tick", x: xs(v), y: m.t + H + 16, "text-anchor": "middle" }, svg);
      t.textContent = tickFmt(v, xd.step);
    }
    if (spec.xTitle) {
      const t = svgEl("text", { class: "axis-title", x: m.l + W, y: m.t + H + 16, "text-anchor": "end" }, svg);
      t.textContent = spec.xTitle;
    }
    attachHover(inst, svg, x, xs, m, W, H);
  }

  function broadcast(source, index, cx, cy) {
    const peers = source.spec.group ? groups[source.spec.group] : [source];
    for (const c of peers) {
      if (c.el.isConnected && c.spec.x === source.spec.x) showIndex(c, index);
    }
    const tip = tooltip();
    if (index === null) { tip.hidden = true; return; }
    const spec = source.spec;
    tip.textContent = "";
    const head = document.createElement("div");
    head.className = "t-head";
    head.textContent = spec.tooltipTitle ? spec.tooltipTitle(index) : spec.x[index];
    tip.appendChild(head);
    for (const s of source.series) {
      const row = document.createElement("div");
      row.className = "t-row";
      const key = document.createElement("i");
      key.style.background = s.color;
      const val = document.createElement("b");
      val.textContent = fmt(s.values[index], s.digits ?? 1) + (s.unit ? " " + s.unit : "");
      const name = document.createElement("span");
      name.textContent = s.name;
      row.append(key, val, name);
      tip.appendChild(row);
    }
    for (const note of spec.notes ? spec.notes(index) : []) {
      const n = document.createElement("div");
      n.className = "t-note";
      n.textContent = note;
      tip.appendChild(n);
    }
    tip.hidden = false;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    let left = cx + 14, top = cy - h - 10;
    if (left + w > window.innerWidth - 8) left = cx - w - 14;
    if (top < 8) top = cy + 14;
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }

  function showIndex(inst, index) {
    inst.index = index;
    if (!inst.hover) return;
    inst.hover.textContent = "";
    if (index === null) return;
    const { spec, xs, ys, m, H } = inst;
    const cx = xs(spec.x[index]);
    svgEl("line", { class: "crosshair", x1: cx, x2: cx, y1: m.t, y2: m.t + H }, inst.hover);
    for (const s of inst.series) {
      const v = s.values[index];
      if (v === null || v === undefined) continue;
      svgEl("circle", { class: "dot", cx, cy: ys(v), r: 4.5, style: `fill:${s.color}` }, inst.hover);
    }
  }

  function hideTooltip() {
    const tip = tooltip();
    if (tip) tip.hidden = true;
    for (const name in groups) for (const c of groups[name]) showIndex(c, null);
  }
  window.addEventListener("scroll", hideTooltip, { passive: true, capture: true });

  window.Charts = { line, lanes, fmt, hideTooltip };
})();
