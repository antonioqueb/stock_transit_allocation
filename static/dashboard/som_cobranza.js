/* Cobranza · SOM Analytics — página autónoma (vanilla + Chart.js).
 * Datos: som.analytics.get_collections (boot JSON) y RPC 'cobranza' al cambiar filtros. */
(function () {
  const boot = JSON.parse(document.getElementById("som-boot").textContent);
  const root = document.getElementById("som-root");
  const theme = (() => { try { return localStorage.getItem("som_theme") || "light"; } catch (e) { return "light"; } })();
  document.documentElement.dataset.theme = theme;
  const state = { data: boot, mode: boot.mode, filters: boot.filters || {}, seller: "", q: "", sort: "saldo", dir: -1, busy: false };
  const money = (v) => new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN", maximumFractionDigits: 0 }).format(Number(v || 0));
  const num = (v, d = 0) => new Intl.NumberFormat("es-MX", { minimumFractionDigits: d, maximumFractionDigits: d }).format(Number(v || 0));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const css = (n, fb) => (getComputedStyle(document.body).getPropertyValue(n) || fb).trim();
  const charts = {};

  async function rpc(method, args) {
    const r = await fetch("/som/analytics/rpc", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", method: "call", id: Date.now(), params: { method, args } }) });
    const j = await r.json(); if (j.error) throw new Error(j.error.data ? j.error.data.message : j.error.message); return j.result;
  }
  function iso(d) { return d.toISOString().slice(0, 10); }
  function preset(p) {
    const t = new Date(); const f = state.filters;
    if (p === "month") { f.date_from = iso(new Date(t.getFullYear(), t.getMonth(), 1)); f.date_to = iso(t); }
    else if (p === "quarter") { const q = Math.floor(t.getMonth() / 3) * 3; f.date_from = iso(new Date(t.getFullYear(), q, 1)); f.date_to = iso(t); }
    else if (p === "year") { f.date_from = iso(new Date(t.getFullYear(), 0, 1)); f.date_to = iso(t); }
    else { delete f.date_from; delete f.date_to; }
    state.preset = p; reload();
  }
  async function reload() {
    state.busy = true; render();
    try { state.data = await rpc("cobranza", [state.filters, state.mode]); } catch (e) { alert(e.message); }
    state.busy = false; render();
  }
  function xlsxUrl() {
    const q = new URLSearchParams({ mode: state.mode, date_from: state.filters.date_from || "", date_to: state.filters.date_to || "", source: state.filters.source || "" });
    return "/som/analytics/cobranza.xlsx?" + q.toString();
  }
  function orderUrl(id) { return "/odoo/action-sale.action_orders/" + id; }
  function rows() {
    let r = state.data.rows || [];
    if (state.seller) r = r.filter(x => x.vendedor === state.seller);
    if (state.q) { const q = state.q.toLowerCase(); r = r.filter(x => (x.name + " " + x.cliente + " " + x.ref).toLowerCase().includes(q)); }
    const k = state.sort; return [...r].sort((a, b) => (a[k] > b[k] ? 1 : a[k] < b[k] ? -1 : 0) * state.dir);
  }
  function render() {
    const d = state.data, k = d.kpis || {}, f = state.filters;
    const sellers = (d.by_seller || []).map(s => s.name);
    const list = rows();
    const modeLabel = { todos: "Todos los pedidos con saldo", sin: "Sin un solo pago", con: "Con anticipo y saldo" }[state.mode];
    root.innerHTML = `
      <header class="cb-top">
        <div class="brand"><div class="logo">$</div><div><b>Cobranza · pedidos por cobrar</b><small>${esc(boot.company)} · montos sin IVA en MXN · ${esc(modeLabel)}</small></div></div>
        <div class="presets">${["todos:Todos", "sin:Sin anticipo", "con:Con anticipo"].map(x => { const [v, l] = x.split(":"); return `<button data-mode="${v}" class="${state.mode === v ? "on" : ""}">${l}</button>`; }).join("")}</div>
        <div class="presets">${["month:Mes", "quarter:Trimestre", "year:Año", "all:Foto viva"].map(x => { const [v, l] = x.split(":"); const on = v === "all" ? !f.date_from : state.preset === v; return `<button data-preset="${v}" class="${on ? "on" : ""}">${l}</button>`; }).join("")}</div>
        <div class="dates"><input type="date" id="df" value="${esc(f.date_from || "")}"/><span>a</span><input type="date" id="dt" value="${esc(f.date_to || "")}"/></div>
        <a class="btn primary" href="${xlsxUrl()}">⬇ Descargar XLSX</a>
        <a class="btn" href="/som/analytics">← Analytics</a>
        <button class="theme-btn" id="theme" title="Tema">${theme === "dark" ? "☀" : "☾"}</button>
      </header>
      <div class="cb-content">
        <div class="stats">
          <div class="stat bad"><div class="stat-l">Saldo por cobrar</div><div class="stat-v">${money(k.saldo_mxn)}</div><div class="stat-s">${num(k.pedidos)} pedidos confirmados con saldo</div></div>
          <div class="stat bad"><div class="stat-l">Sin un solo pago</div><div class="stat-v">${money(k.saldo_sin_anticipo)}</div><div class="stat-s">${num(k.sin_anticipo)} pedidos · prioridad de cobranza</div></div>
          <div class="stat mid"><div class="stat-l">Con anticipo · resto</div><div class="stat-v">${money(k.saldo_con_anticipo)}</div><div class="stat-s">saldo de pedidos que ya abonaron</div></div>
          <div class="stat ${k.pedidos_mas_90 ? "bad" : "good"}"><div class="stat-l">Más de 90 días</div><div class="stat-v">${money(k.mas_90)}</div><div class="stat-s">${num(k.pedidos_mas_90)} pedidos · antigüedad promedio ${num(k.dias_promedio)} días</div></div>
        </div>
        <div class="grid">
          <div class="panel"><div class="panel-h"><h3>Saldo por vendedor</h3><span class="hint">clic filtra la tabla</span></div><div class="chartbox" style="height:280px"><canvas id="c-seller"></canvas></div></div>
          <div class="panel"><div class="panel-h"><h3>Saldo por antigüedad del pedido</h3><span class="hint">días desde la fecha de orden</span></div><div class="chartbox" style="height:280px"><canvas id="c-bucket"></canvas></div></div>
          <div class="panel wide"><div class="panel-h"><h3>Clientes con más saldo</h3><span class="hint">top 15</span></div><div class="chartbox" style="height:300px"><canvas id="c-customer"></canvas></div></div>
        </div>
        <div class="cb-toolbar">
          <select id="seller"><option value="">Todos los vendedores</option>${sellers.map(s => `<option ${state.seller === s ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>
          <input id="q" placeholder="Buscar pedido, cliente o referencia" value="${esc(state.q)}"/>
          <span class="count">${state.busy ? "Consultando…" : num(list.length) + " pedidos · " + money(list.reduce((a, r) => a + r.saldo, 0))}</span>
        </div>
        <div class="tablewrap tall"><table class="cb-table"><thead><tr>
          ${[["name", "Pedido"], ["fecha", "Fecha"], ["dias", "Días"], ["cliente", "Cliente"], ["vendedor", "Vendedor"], ["total", "Total sin IVA"], ["pagado", "Pagado"], ["saldo", "Saldo"], ["pct_pagado", "% pagado"], ["invoice_status", "Facturación"]]
            .map(([k2, l]) => `<th class="sort ${["total", "pagado", "saldo", "pct_pagado", "dias"].includes(k2) ? "r" : ""}" data-sort="${k2}">${l}${state.sort === k2 ? (state.dir < 0 ? " ▾" : " ▴") : ""}</th>`).join("")}
        </tr></thead><tbody>
          ${list.map(r => `<tr>
            <td class="name"><a href="${orderUrl(r.id)}" target="_blank">${esc(r.name)}</a>${r.ref ? `<br><span class="mut">${esc(r.ref)}</span>` : ""}</td>
            <td>${esc(r.fecha)}</td><td class="r"><span class="pill ${r.dias > 90 ? "bad" : r.dias > 30 ? "mid" : "good"}">${r.dias}</span></td>
            <td class="ell"><span class="strong">${esc(r.cliente)}</span><br><span class="contact">${r.telefono ? `<a href="tel:${esc(r.telefono)}">${esc(r.telefono)}</a>` : ""}${r.telefono && r.email ? " · " : ""}${r.email ? `<a href="mailto:${esc(r.email)}">${esc(r.email)}</a>` : ""}</span></td>
            <td>${esc(r.vendedor)}</td><td class="r">${money(r.total)}</td><td class="r">${money(r.pagado)}</td><td class="r strong ${r.pagado <= 0.01 ? "neg" : ""}">${money(r.saldo)}</td>
            <td class="r"><div class="bar"><span style="width:${Math.min(100, r.pct_pagado)}%"></span></div><span class="mut">${num(r.pct_pagado, 1)}%</span></td>
            <td><span class="pill ${r.invoice_status === "invoiced" ? "good" : r.invoice_status === "to invoice" ? "mid" : ""}">${esc(r.invoice_status || "—")}</span></td>
          </tr>`).join("") || `<tr><td colspan="10" class="mut">Sin pedidos por cobrar en este alcance.</td></tr>`}
        </tbody></table></div>
        <p class="cb-note">Pedido confirmado con saldo = total sin IVA menos lo pagado en facturas publicadas (llevado a neto en proporción al IVA del pedido). USD al tipo de cambio congelado del pedido o al del día. La foto viva no tiene corte de fechas; con periodo, solo pedidos con fecha de orden dentro del rango.</p>
      </div>`;
    root.querySelectorAll("[data-mode]").forEach(b => b.onclick = () => { state.mode = b.dataset.mode; reload(); });
    root.querySelectorAll("[data-preset]").forEach(b => b.onclick = () => preset(b.dataset.preset));
    root.querySelector("#df").onchange = (e) => { state.filters.date_from = e.target.value; state.preset = ""; reload(); };
    root.querySelector("#dt").onchange = (e) => { state.filters.date_to = e.target.value; state.preset = ""; reload(); };
    root.querySelector("#seller").onchange = (e) => { state.seller = e.target.value; render(); };
    root.querySelector("#q").oninput = (e) => { state.q = e.target.value; const tb = root.querySelector("tbody"); render(); root.querySelector("#q").focus(); };
    root.querySelectorAll("th.sort").forEach(th => th.onclick = () => { const k2 = th.dataset.sort; state.dir = state.sort === k2 ? -state.dir : -1; state.sort = k2; render(); });
    root.querySelector("#theme").onclick = () => { const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = t; try { localStorage.setItem("som_theme", t); } catch (e) {} render(); };
    drawCharts();
  }
  function drawCharts() {
    Object.values(charts).forEach((c) => c.destroy());
    const d = state.data;
    const txt = css("--mut", "#334155");
    const line = css("--line", "rgba(15,23,42,.1)");
    const millions = (v) => num(v / 1e6, 1) + " M";
    const base = (extraTooltip) => ({
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 200 },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: extraTooltip } },
      },
    });

    const sellers = d.by_seller || [];
    charts.seller = new Chart(document.getElementById("c-seller"), {
      type: "bar",
      data: {
        labels: sellers.map((x) => x.name),
        datasets: [{ data: sellers.map((x) => x.saldo), borderRadius: 5,
          backgroundColor: sellers.map((x) => (x.name === state.seller ? "#0b57d0" : "rgba(11,87,208,.55)")) }],
      },
      options: {
        ...base((c) => " " + money(c.parsed.x) + " · " + sellers[c.dataIndex].pedidos + " pedidos · " + sellers[c.dataIndex].sin_anticipo + " sin anticipo"),
        indexAxis: "y",
        scales: {
          x: { ticks: { color: txt, callback: millions }, grid: { color: line } },
          y: { ticks: { color: txt, font: { size: 11 } }, grid: { display: false } },
        },
        onClick: (_e, els) => {
          if (!els.length) return;
          const n = sellers[els[0].index].name;
          state.seller = state.seller === n ? "" : n;
          render();
        },
      },
    });

    const buckets = d.by_bucket || [];
    charts.bucket = new Chart(document.getElementById("c-bucket"), {
      type: "bar",
      data: {
        labels: buckets.map((b) => b.bucket + " días"),
        datasets: [{ data: buckets.map((b) => b.saldo), borderRadius: 5,
          backgroundColor: ["#059669cc", "#0284c7cc", "#d97706cc", "#dc2626b3", "#dc2626"] }],
      },
      options: {
        ...base((c) => " " + money(c.parsed.y) + " · " + buckets[c.dataIndex].pedidos + " pedidos"),
        scales: {
          x: { ticks: { color: txt, font: { size: 11 } }, grid: { color: line } },
          y: { ticks: { color: txt, font: { size: 11 }, callback: millions }, grid: { color: line } },
        },
      },
    });

    const customers = d.by_customer || [];
    charts.customer = new Chart(document.getElementById("c-customer"), {
      type: "bar",
      data: {
        labels: customers.map((c) => c.name.slice(0, 30)),
        datasets: [{ data: customers.map((c) => c.saldo), borderRadius: 5,
          backgroundColor: customers.map((c) => (c.max_dias > 90 ? "rgba(220,38,38,.75)" : c.max_dias > 30 ? "rgba(217,119,6,.75)" : "rgba(5,150,105,.75)")) }],
      },
      options: {
        ...base((c) => " " + money(c.parsed.y) + " · " + customers[c.dataIndex].pedidos + " pedidos · más antiguo " + customers[c.dataIndex].max_dias + " d"),
        scales: {
          x: { ticks: { color: txt, font: { size: 11 } }, grid: { color: line } },
          y: { ticks: { color: txt, font: { size: 11 }, callback: millions }, grid: { color: line } },
        },
        onClick: (_e, els) => {
          if (!els.length) return;
          state.q = customers[els[0].index].name;
          render();
        },
      },
    });
  }
  render();
})();
