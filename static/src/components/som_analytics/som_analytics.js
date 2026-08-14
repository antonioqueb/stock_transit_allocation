/** @odoo-module **/
import { somFormatDate } from "@stock_transit_allocation/utils/som_date";
// SOM Analytics v2 — BI por DOMINIOS con PROFUNDIZACIÓN real.
//
//  · Pestañas: Resumen · Comercial · Inventario · Compras · Tránsito ·
//    Entregas · Financiero. Cada una con su propio payload (SQL, rápido).
//  · Click en un elemento = DRILL: panel lateral con la radiografía completa
//    de ese elemento (tendencia, cortes por otras dimensiones, órdenes).
//    Desde el drill se puede, opcionalmente, fijar como filtro global.
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadBundle } from "@web/core/assets";
import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";

const BLUE = "#0b57d0";
const SKY = "#38bdf8";
const GREEN = "#10b981";
const AMBER = "#f59e0b";
const RED = "#ef4444";
const VIOLET = "#8b5cf6";
const PALETTE = [BLUE, SKY, GREEN, AMBER, VIOLET, RED, "#0e7490", "#ec4899"];
const FONT = "'Inter', -apple-system, 'Segoe UI', Roboto, sans-serif";
const MONTHS_ES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
    "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];

const TABS = [
    { key: "resumen", label: "Resumen" },
    { key: "comercial", label: "Comercial" },
    { key: "inventario", label: "Inventario" },
    { key: "compras", label: "Compras" },
    { key: "transito", label: "Tránsito" },
    { key: "recepciones", label: "Recepciones" },
    { key: "taller", label: "Taller" },
    { key: "entregas", label: "Entregas" },
    { key: "financiero", label: "Financiero" },
    { key: "control", label: "Control" },
];

// Números CRUDOS en formato anglosajón (1,234,567.89): sin abreviar
// miles con "k" ni millones con "M" — regla del negocio.
function fmtMoney(v) {
    return "$" + new Intl.NumberFormat("en-US", {
        minimumFractionDigits: 0, maximumFractionDigits: 0,
    }).format(v || 0);
}
function fmtCompact(v) {
    return new Intl.NumberFormat("en-US", {
        minimumFractionDigits: 0, maximumFractionDigits: 0,
    }).format(v || 0);
}
function fmtNum(v, dec = 1) {
    return new Intl.NumberFormat("en-US", {
        minimumFractionDigits: 0, maximumFractionDigits: dec,
    }).format(v || 0);
}
function monthLabel(m) {
    if (!m || m.length < 7) return m || "";
    return `${MONTHS_ES[parseInt(m.slice(5, 7), 10) - 1]} ${m.slice(2, 4)}`;
}

const somCenterText = {
    id: "somCenterText",
    afterDraw(chart) {
        const opts = chart.config.options.plugins?.somCenterText;
        if (!opts || !opts.text) return;
        const meta = chart.getDatasetMeta(0);
        if (!meta.data || !meta.data[0]) return;
        const { x, y } = meta.data[0];
        const ctx = chart.ctx;
        ctx.save();
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.font = `800 16px ${FONT}`;
        ctx.fillStyle = "#0f172a";
        ctx.fillText(opts.text, x, y - 7);
        ctx.font = `600 10px ${FONT}`;
        ctx.fillStyle = "#64748b";
        ctx.fillText(opts.sub || "", x, y + 10);
        ctx.restore();
    },
};

export class SomAnalytics extends Component {
    static template = "stock_transit_allocation.SomAnalytics";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.tabs = TABS;
        this.state = useState({
            tab: "resumen",
            loading: true,
            error: "",
            cache: {},          // {tabKey: payload}
            filters: this.defaultFilters(),
            chips: [],
            preset: "12m",
            drill: null,        // payload de get_drill
            drillLoading: false,
        });
        this.charts = {};
        onMounted(async () => {
            await loadBundle("web.chartjs_lib");
            if (window.Chart && !window.Chart.registry.plugins.get("somCenterText")) {
                window.Chart.register(somCenterText);
            }
            await this.loadTab(this.state.tab);
        });
        onWillUnmount(() => this.destroyCharts());
    }

    // Formato único del sistema: "13 ago 2026". Las fechas llegan en
    // ISO a propósito (el orden de las listas se calcula con ellas).
    fmtDate(value) {
        return somFormatDate(value, { empty: "" });
    }

    defaultFilters() {
        const today = new Date();
        const from = new Date(today);
        from.setFullYear(from.getFullYear() - 1);
        return {
            date_from: from.toISOString().slice(0, 10),
            date_to: today.toISOString().slice(0, 10),
        };
    }

    // ── Carga por pestaña (con caché por sesión de filtros) ─────────────
    get data() {
        return this.state.cache[this.state.tab];
    }

    async loadTab(tab, force = false) {
        this.state.tab = tab;
        if (this.state.cache[tab] && !force) {
            this._afterRender(() => this.renderTab(), ".som_bi__body canvas");
            return;
        }
        this.state.loading = true;
        this.state.error = "";
        try {
            const data = await this.orm.call(
                "som.analytics", "get_dashboard", [tab, this.state.filters]);
            if (data && data.error) {
                this.state.error = data.error;
            } else {
                this.state.cache = { ...this.state.cache, [tab]: data };
            }
        } catch (e) {
            this.state.error = (e.data && e.data.message) || String(e);
        }
        this.state.loading = false;
        this._afterRender(() => this.renderTab(), ".som_bi__body canvas");
    }

    reloadAll() {
        this.state.cache = {};
        this.loadTab(this.state.tab, true);
    }

    // OWL pinta el DOM en su propio animation frame: montar los gráficos
    // en un rAF directo corre ANTES de que existan los <canvas> y el guard
    // falla en silencio. Se reintenta hasta que el DOM esté listo.
    _afterRender(fn, probeSelector) {
        let tries = 0;
        const attempt = () => {
            if (probeSelector && !document.querySelector(probeSelector) && tries++ < 40) {
                requestAnimationFrame(attempt);
                return;
            }
            fn();
        };
        requestAnimationFrame(attempt);
    }

    destroyCharts() {
        for (const c of Object.values(this.charts)) {
            try { c.destroy(); } catch { /* noop */ }
        }
        this.charts = {};
    }

    // ── Filtros globales (secundarios al drill) ─────────────────────────
    addFilter(key, value, label) {
        this.state.filters = { ...this.state.filters, [key]: value };
        this.state.chips = [
            ...this.state.chips.filter((c) => c.key !== key),
            { key, label },
        ];
        this.closeDrill();
        this.reloadAll();
    }

    removeFilter(key) {
        const f = { ...this.state.filters };
        delete f[key];
        this.state.filters = f;
        this.state.chips = this.state.chips.filter((c) => c.key !== key);
        this.reloadAll();
    }

    clearFilters() {
        this.state.filters = this.defaultFilters();
        this.state.chips = [];
        this.state.preset = "12m";
        this.reloadAll();
    }

    setPreset(preset) {
        const today = new Date();
        const from = new Date(today);
        if (preset === "mes") from.setDate(1);
        else if (preset === "30d") from.setDate(from.getDate() - 30);
        else if (preset === "90d") from.setDate(from.getDate() - 90);
        else from.setFullYear(from.getFullYear() - 1);
        this.state.preset = preset;
        this.state.filters = {
            ...this.state.filters,
            date_from: from.toISOString().slice(0, 10),
            date_to: today.toISOString().slice(0, 10),
        };
        delete this.state.filters.month;
        this.state.chips = this.state.chips.filter((c) => c.key !== "month");
        this.reloadAll();
    }

    // ── DRILL: profundizar en un elemento ───────────────────────────────
    async drill(entity, value, label) {
        this.state.drillLoading = true;
        this.state.drill = { entity, value, label, kpis: {} };
        try {
            const data = await this.orm.call(
                "som.analytics", "get_drill",
                [entity, value, label, this.state.filters]);
            this.state.drill = data;
        } catch (e) {
            this.state.drill = null;
            this.state.error = (e.data && e.data.message) || String(e);
        }
        this.state.drillLoading = false;
        this._afterRender(() => this.renderDrill(), "#som_dr_month");
    }

    closeDrill() {
        for (const k of Object.keys(this.charts)) {
            if (k.startsWith("dr_")) {
                this.charts[k].destroy();
                delete this.charts[k];
            }
        }
        this.state.drill = null;
    }

    applyDrillAsFilter() {
        const d = this.state.drill;
        if (!d) return;
        const keyMap = {
            month: "month", product: "product_id",
            seller: "user_id", customer: "partner_id", level: "level",
        };
        this.addFilter(keyMap[d.entity], d.value, d.label);
    }

    // ── Fábrica de gráficos ─────────────────────────────────────────────
    baseOptions(onClickFn) {
        return {
            responsive: true,
            maintainAspectRatio: false,
            onClick: onClickFn || (() => {}),
            animation: { duration: 450, easing: "easeOutQuart" },
            onHover: (ev, els) => {
                ev.native.target.style.cursor =
                    els.length && onClickFn ? "pointer" : "default";
            },
            plugins: {
                legend: {
                    labels: {
                        boxWidth: 9, boxHeight: 9, usePointStyle: true,
                        pointStyle: "circle",
                        font: { size: 11, family: FONT, weight: "600" },
                        color: "#475569",
                    },
                },
                tooltip: {
                    backgroundColor: "rgba(15, 23, 42, .96)",
                    titleColor: "#f8fafc",
                    bodyColor: "#cbd5e1",
                    titleFont: { size: 12, weight: "700", family: FONT },
                    bodyFont: { size: 11.5, family: FONT },
                    padding: 12, cornerRadius: 10,
                    boxWidth: 8, boxHeight: 8, usePointStyle: true,
                    callbacks: {
                        label: (ctx) => {
                            const ds = ctx.dataset.label || "";
                            const v = ctx.parsed.y ?? ctx.parsed.x ?? ctx.parsed;
                            return ` ${ds}: ${ctx.dataset.somMoney ? fmtMoney(v) : fmtNum(v)}`;
                        },
                    },
                },
            },
        };
    }

    axMoney() {
        return {
            beginAtZero: true, border: { display: false },
            grid: { color: "rgba(100,116,139,.10)" },
            ticks: {
                font: { size: 10.5, family: FONT }, color: "#94a3b8",
                callback: (v) => fmtCompact(v),
            },
        };
    }

    axPlain(size = 10.5) {
        return {
            border: { display: false }, grid: { display: false },
            ticks: { font: { size, family: FONT, weight: "600" }, color: "#64748b" },
        };
    }

    mk(key, canvasId, config) {
        const el = document.getElementById(canvasId);
        if (!window.Chart) {
            console.warn("[SOM Analytics] Chart.js no disponible (web.chartjs_lib)");
            return;
        }
        if (!el) return;
        if (this.charts[key]) this.charts[key].destroy();
        this.charts[key] = new window.Chart(el.getContext("2d"), config);
    }

    barChart(key, id, labels, datasets, { horizontal = false, click = null, stacked = false } = {}) {
        this.mk(key, id, {
            type: "bar",
            data: { labels, datasets },
            options: {
                ...this.baseOptions(click),
                indexAxis: horizontal ? "y" : "x",
                interaction: { mode: "index", intersect: false },
                scales: horizontal
                    ? { x: { ...this.axMoney(), stacked }, y: { ...this.axPlain(10), stacked } }
                    : { y: { ...this.axMoney(), stacked }, x: { ...this.axPlain(), stacked } },
            },
        });
    }

    doughnut(key, id, labels, values, { click = null, center = "", sub = "", tooltips = null } = {}) {
        const base = this.baseOptions(click);
        this.mk(key, id, {
            type: "doughnut",
            data: {
                labels,
                datasets: [{
                    data: values, backgroundColor: PALETTE, hoverOffset: 10,
                    borderWidth: 3, borderColor: "#fff", borderRadius: 5,
                }],
            },
            options: {
                ...base,
                cutout: "66%",
                layout: { padding: 6 },
                plugins: {
                    ...base.plugins,
                    somCenterText: { text: center, sub },
                    legend: {
                        position: "bottom",
                        labels: {
                            boxWidth: 8, boxHeight: 8, usePointStyle: true,
                            pointStyle: "circle", padding: 9,
                            font: { size: 10.5, family: FONT, weight: "600" },
                            color: "#475569",
                        },
                    },
                    tooltip: {
                        ...base.plugins.tooltip,
                        callbacks: tooltips || base.plugins.tooltip.callbacks,
                    },
                },
            },
        });
    }

    ds(label, data, color, money = true) {
        return {
            label, data, somMoney: money,
            backgroundColor: color, hoverBackgroundColor: color,
            borderRadius: 6, borderSkipped: false, maxBarThickness: 34,
        };
    }

    // ── Render por pestaña ──────────────────────────────────────────────
    renderTab() {
        const d = this.data;
        if (!d) return;
        const t = this.state.tab;
        if (t === "resumen") this.renderResumen(d);
        else if (t === "comercial") this.renderComercial(d);
        else if (t === "inventario") this.renderInventario(d);
        else if (t === "compras") this.renderCompras(d);
        else if (t === "transito") this.renderTransito(d);
        else if (t === "recepciones") this.renderRecepciones(d);
        else if (t === "taller") this.renderTaller(d);
        else if (t === "entregas") this.renderEntregas(d);
        else if (t === "financiero") this.renderFinanciero(d);
        else if (t === "control") this.renderControl(d);
    }

    _monthlyCombo(key, id, rows, clickable = true) {
        const click = clickable ? (ev, els) => {
            if (!els.length) return;
            const r = rows[els[0].index];
            this.drill("month", r.key, `Mes ${monthLabel(r.key)}`);
        } : null;
        this.mk(key, id, {
            type: "bar",
            data: {
                labels: rows.map((r) => monthLabel(r.key)),
                datasets: [
                    this.ds("Venta MXN", rows.map((r) => r.venta), "rgba(11,87,208,.82)"),
                    this.ds("Utilidad all-in", rows.map((r) => r.utilidad), "rgba(16,185,129,.8)"),
                    {
                        type: "line", label: "m²", data: rows.map((r) => r.m2),
                        borderColor: SKY, borderWidth: 2.5,
                        pointBackgroundColor: "#fff", pointBorderColor: SKY,
                        pointBorderWidth: 2, pointRadius: 3, pointHoverRadius: 6,
                        tension: 0.4, yAxisID: "y1",
                    },
                ],
            },
            options: {
                ...this.baseOptions(click),
                interaction: { mode: "index", intersect: false },
                scales: {
                    y: this.axMoney(),
                    y1: {
                        beginAtZero: true, position: "right",
                        border: { display: false }, grid: { display: false },
                        ticks: { font: { size: 10, family: FONT }, color: "#7dd3fc" },
                    },
                    x: this.axPlain(),
                },
            },
        });
    }

    renderResumen(d) {
        this._monthlyCombo("r_m", "som_r_monthly", d.by_month || []);
        const fin = d.finance || {};
        this.barChart("r_f", "som_r_finance",
            ["Me deben (por cobrar)", "Debo (por pagar)"],
            [{
                label: "MXN", data: [fin.por_cobrar || 0, fin.por_pagar || 0],
                somMoney: true, backgroundColor: [GREEN, RED],
                borderRadius: 8, borderSkipped: false, maxBarThickness: 70,
            }]);
        const ag = d.aging || [];
        this.barChart("r_a", "som_r_aging",
            ag.map((r) => r.bucket),
            [{
                label: "m²", data: ag.map((r) => r.m2), somMoney: false,
                backgroundColor: [GREEN, SKY, AMBER, RED, "#94a3b8"],
                borderRadius: 6, borderSkipped: false,
            }]);
        const tr = d.transit_status || [];
        this.barChart("r_t", "som_r_transit",
            tr.map((r) => r.label),
            [{
                label: "m²", data: tr.map((r) => r.m2), somMoney: false,
                backgroundColor: "rgba(56,189,248,.8)",
                borderRadius: 6, borderSkipped: false,
            }]);
    }

    renderComercial(d) {
        this._monthlyCombo("c_m", "som_c_monthly", d.by_month || []);
        const lv = d.levels || [];
        const totalLv = lv.reduce((s, r) => s + r.venta, 0);
        this.doughnut("c_l", "som_c_levels",
            lv.map((r) => r.name), lv.map((r) => r.venta), {
                center: fmtCompact(totalLv), sub: "venta MXN",
                click: (ev, els) => {
                    if (!els.length) return;
                    const r = lv[els[0].index];
                    this.drill("level", r.key, `Nivel ${r.name}`);
                },
                tooltips: {
                    label: (ctx) => {
                        const r = lv[ctx.dataIndex];
                        const pct = totalLv ? (r.venta / totalLv * 100).toFixed(1) : 0;
                        return ` ${r.name}: ${fmtMoney(r.venta)} (${pct}%) · margen ${fmtNum(r.margen)}%`;
                    },
                },
            });
        const se = d.by_seller || [];
        this.barChart("c_s", "som_c_sellers",
            se.map((r) => (r.name || "").split(" ")[0]),
            [
                this.ds("Venta", se.map((r) => r.venta), "rgba(11,87,208,.82)"),
                this.ds("Utilidad", se.map((r) => r.utilidad), "rgba(16,185,129,.8)"),
            ],
            {
                click: (ev, els) => {
                    if (!els.length) return;
                    const r = se[els[0].index];
                    this.drill("seller", r.key, r.name);
                },
            });
        const tp = d.top_products || [];
        this.barChart("c_p", "som_c_products",
            tp.map((r) => r.name.length > 34 ? r.name.slice(0, 33) + "…" : r.name),
            [{
                label: "Utilidad all-in", data: tp.map((r) => r.utilidad),
                somMoney: true,
                backgroundColor: tp.map((r) => r.utilidad >= 0
                    ? "rgba(16,185,129,.82)" : "rgba(239,68,68,.82)"),
                borderRadius: 6, borderSkipped: false, maxBarThickness: 18,
            }],
            {
                horizontal: true,
                click: (ev, els) => {
                    if (!els.length) return;
                    const r = tp[els[0].index];
                    this.drill("product", r.key, r.name);
                },
            });
    }

    renderInventario(d) {
        const k = d.kpis || {};
        this.doughnut("i_s", "som_i_states",
            ["Disponible", "En hold", "Vendido en bodega"],
            [k.disponible_m2 || 0, k.hold_m2 || 0, k.vendido_m2 || 0],
            { center: fmtNum((k.disponible_m2 || 0) + (k.hold_m2 || 0) + (k.vendido_m2 || 0)), sub: "m² totales" });
        const ag = d.aging || [];
        this.barChart("i_a", "som_i_aging",
            ag.map((r) => r.bucket),
            [{
                label: "m²", data: ag.map((r) => r.m2), somMoney: false,
                backgroundColor: [GREEN, SKY, AMBER, RED, "#94a3b8"],
                borderRadius: 6, borderSkipped: false,
            }]);
        const ts = d.top_stock || [];
        this.barChart("i_t", "som_i_top",
            ts.map((r) => r.name.length > 34 ? r.name.slice(0, 33) + "…" : r.name),
            [{
                label: "m² en stock", data: ts.map((r) => r.m2), somMoney: false,
                backgroundColor: "rgba(11,87,208,.8)",
                borderRadius: 6, borderSkipped: false, maxBarThickness: 18,
            }],
            {
                horizontal: true,
                click: (ev, els) => {
                    if (!els.length) return;
                    const r = ts[els[0].index];
                    this.drill("product", r.key, r.name);
                },
            });
    }

    renderCompras(d) {
        const rows = d.by_month || [];
        this.barChart("p_m", "som_p_monthly",
            rows.map((r) => monthLabel(r.key)),
            [
                { ...this.ds("USD (original)", rows.map((r) => r.usd), "rgba(16,185,129,.8)") },
                { ...this.ds("MXN (original)", rows.map((r) => r.mxn), "rgba(11,87,208,.82)") },
            ],
            { stacked: false });
        const sp = d.top_suppliers || [];
        this.barChart("p_s", "som_p_suppliers",
            sp.map((r) => r.name.length > 30 ? r.name.slice(0, 29) + "…" : r.name),
            [{
                label: "Compras (MXN norm.)", data: sp.map((r) => r.mxn),
                somMoney: true, backgroundColor: "rgba(139,92,246,.8)",
                borderRadius: 6, borderSkipped: false, maxBarThickness: 18,
            }],
            { horizontal: true });
        const al = d.allocations || [];
        this.doughnut("p_a", "som_p_alloc",
            al.map((r) => r.state), al.map((r) => r.qty),
            {
                center: fmtNum(al.reduce((s, r) => s + r.qty, 0)), sub: "m² asignados",
                tooltips: {
                    label: (ctx) => {
                        const r = al[ctx.dataIndex];
                        return ` ${r.state}: ${fmtNum(r.qty)} m² · ${r.count} allocations`;
                    },
                },
            });
    }

    renderTransito(d) {
        const tr = d.by_status || [];
        this.barChart("t_s", "som_t_status",
            tr.map((r) => r.label),
            [{
                label: "m²", data: tr.map((r) => r.m2), somMoney: false,
                backgroundColor: "rgba(56,189,248,.82)",
                borderRadius: 7, borderSkipped: false,
            }]);
    }

    renderRecepciones(d) {
        const rows = d.by_week || [];
        this.mk("rc_w", "som_rc_weekly", {
            type: "bar",
            data: {
                labels: rows.map((r) => r.week),
                datasets: [
                    {
                        label: "m² recibidos", data: rows.map((r) => r.m2),
                        somMoney: false,
                        backgroundColor: "rgba(11,87,208,.82)",
                        borderRadius: 6, borderSkipped: false, maxBarThickness: 34,
                        yAxisID: "y",
                    },
                    {
                        type: "line", label: "Recepciones",
                        data: rows.map((r) => r.count),
                        borderColor: SKY, borderWidth: 2.5,
                        pointBackgroundColor: "#fff", pointBorderColor: SKY,
                        pointBorderWidth: 2, pointRadius: 3.5,
                        tension: 0.4, yAxisID: "y1",
                    },
                ],
            },
            options: {
                ...this.baseOptions(null),
                interaction: { mode: "index", intersect: false },
                scales: {
                    y: this.axMoney(),
                    y1: {
                        beginAtZero: true, position: "right",
                        border: { display: false }, grid: { display: false },
                        ticks: { precision: 0, font: { size: 10, family: FONT }, color: "#7dd3fc" },
                    },
                    x: this.axPlain(9.5),
                },
            },
        });
    }

    renderTaller(d) {
        const st = d.by_state || [];
        this.doughnut("tl_s", "som_tl_states",
            st.map((r) => r.state), st.map((r) => r.count),
            { center: fmtNum(st.reduce((s2, r) => s2 + r.count, 0), 0), sub: "OTs activas" });
        const wk = d.weekly_done || [];
        this.barChart("tl_w", "som_tl_weekly",
            wk.map((r) => r.week),
            [{
                label: "OTs terminadas", data: wk.map((r) => r.count),
                somMoney: false,
                backgroundColor: "rgba(16,185,129,.8)",
                borderRadius: 6, borderSkipped: false,
            }]);
    }

    renderControl(d) {
        const rows = d.bandeja || [];
        this.barChart("ct_b", "som_ct_bandeja",
            rows.map((r) => r.label),
            [{
                label: "Pendientes", data: rows.map((r) => r.count),
                somMoney: false,
                backgroundColor: rows.map((r) =>
                    r.count === 0 ? "rgba(16,185,129,.75)"
                    : r.age > 7 ? "rgba(239,68,68,.8)" : "rgba(245,158,11,.8)"),
                borderRadius: 6, borderSkipped: false, maxBarThickness: 20,
            }],
            { horizontal: true });
    }

    renderEntregas(d) {
        if (d.unavailable) return;
        const st = d.by_status || [];
        const map = { preparacion: "En preparación", en_ruta: "En ruta", entregada: "Entregada", cancelada: "Cancelada" };
        this.doughnut("e_s", "som_e_status",
            st.map((r) => map[r.status] || r.status), st.map((r) => r.count),
            { center: fmtNum(st.reduce((s, r) => s + r.count, 0), 0), sub: "remisiones" });
        const k = d.kpis || {};
        this.doughnut("e_f", "som_e_sign",
            ["Firmadas en app", "Manuales"], [k.firmadas_app || 0, k.manuales || 0],
            { center: fmtNum((k.firmadas_app || 0) + (k.manuales || 0), 0), sub: "cerradas" });
    }

    renderFinanciero(d) {
        const ab = d.ar_buckets || [];
        this.barChart("f_ar", "som_f_ar",
            ab.map((r) => r.bucket),
            [{
                label: "Por cobrar", data: ab.map((r) => r.monto), somMoney: true,
                backgroundColor: ["#10b981", "#a3e635", "#f59e0b", "#fb923c", "#ef4444"],
                borderRadius: 6, borderSkipped: false,
            }]);
        const pb = d.ap_buckets || [];
        this.barChart("f_ap", "som_f_ap",
            pb.map((r) => r.bucket),
            [{
                label: "Por pagar", data: pb.map((r) => r.monto), somMoney: true,
                backgroundColor: ["#38bdf8", "#818cf8", "#a78bfa", "#f472b6", "#ef4444"],
                borderRadius: 6, borderSkipped: false,
            }]);
        const cm = d.cash_month || [];
        this.barChart("f_c", "som_f_cash",
            cm.map((r) => monthLabel(r.key)),
            [
                this.ds("Entradas de caja", cm.map((r) => r.entradas), "rgba(16,185,129,.8)"),
                this.ds("Salidas de caja", cm.map((r) => r.salidas), "rgba(239,68,68,.75)"),
            ]);
        const bm = d.by_month || [];
        this.mk("f_m", "som_f_month", {
            type: "line",
            data: {
                labels: bm.map((r) => monthLabel(r.key)),
                datasets: [
                    {
                        label: "Facturado a clientes", data: bm.map((r) => r.facturado),
                        borderColor: GREEN, backgroundColor: "rgba(16,185,129,.09)",
                        borderWidth: 2.5, tension: 0.4, fill: true, somMoney: true,
                        pointBackgroundColor: "#fff", pointBorderColor: GREEN, pointBorderWidth: 2,
                    },
                    {
                        label: "Comprado a proveedores", data: bm.map((r) => r.comprado),
                        borderColor: RED, backgroundColor: "rgba(239,68,68,.07)",
                        borderWidth: 2.5, tension: 0.4, fill: true, somMoney: true,
                        pointBackgroundColor: "#fff", pointBorderColor: RED, pointBorderWidth: 2,
                    },
                ],
            },
            options: {
                ...this.baseOptions(null),
                interaction: { mode: "index", intersect: false },
                scales: { y: this.axMoney(), x: this.axPlain() },
            },
        });
    }

    // ── Render del panel de PROFUNDIZACIÓN ──────────────────────────────
    renderDrill() {
        const d = this.state.drill;
        if (!d || !d.by_month) return;
        const bm = d.by_month || [];
        this.mk("dr_m", "som_dr_month", {
            type: "line",
            data: {
                labels: bm.map((r) => monthLabel(r.key)),
                datasets: [
                    {
                        label: "Venta MXN", data: bm.map((r) => r.venta),
                        borderColor: BLUE, backgroundColor: "rgba(11,87,208,.10)",
                        borderWidth: 2.5, tension: 0.4, fill: true, somMoney: true,
                        pointBackgroundColor: "#fff", pointBorderColor: BLUE, pointBorderWidth: 2,
                    },
                    {
                        label: "Utilidad all-in", data: bm.map((r) => r.utilidad),
                        borderColor: GREEN, backgroundColor: "rgba(16,185,129,.09)",
                        borderWidth: 2.5, tension: 0.4, fill: true, somMoney: true,
                        pointBackgroundColor: "#fff", pointBorderColor: GREEN, pointBorderWidth: 2,
                    },
                ],
            },
            options: {
                ...this.baseOptions(null),
                interaction: { mode: "index", intersect: false },
                scales: { y: this.axMoney(), x: this.axPlain(10) },
            },
        });

        // Corte 1: la dimensión complementaria más útil según la entidad
        const dim1 = d.entity === "product" || d.entity === "level"
            ? { rows: d.by_seller, title: "seller" }
            : { rows: d.by_product, title: "product" };
        const r1 = dim1.rows || [];
        this.barChart("dr_1", "som_dr_dim1",
            r1.map((r) => r.name.length > 28 ? r.name.slice(0, 27) + "…" : r.name),
            [{
                label: "Venta MXN", data: r1.map((r) => r.venta), somMoney: true,
                backgroundColor: "rgba(11,87,208,.8)",
                borderRadius: 6, borderSkipped: false, maxBarThickness: 16,
            }],
            {
                horizontal: true,
                click: (ev, els) => {
                    if (!els.length) return;
                    const r = r1[els[0].index];
                    this.drill(dim1.title === "seller" ? "seller" : "product",
                        r.key, r.name);
                },
            });

        const lv = d.levels || [];
        this.doughnut("dr_l", "som_dr_levels",
            lv.map((r) => r.name), lv.map((r) => r.venta),
            { center: fmtCompact(lv.reduce((s, r) => s + r.venta, 0)), sub: "venta" });
    }

    drillDim1Title() {
        const d = this.state.drill;
        if (!d) return "";
        return (d.entity === "product" || d.entity === "level")
            ? "Quién lo vende" : "Qué materiales lleva";
    }

    // ── Navegación ──────────────────────────────────────────────────────
    openOrder(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "sale.order", res_id: id,
            views: [[false, "form"]], target: "current",
        });
    }

    openVoyage(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "stock.transit.voyage", res_id: id,
            views: [[false, "form"]], target: "current",
        });
    }

    // ── Formatters ──────────────────────────────────────────────────────
    money(v) { return fmtMoney(v); }
    compact(v) { return fmtCompact(v); }
    num(v, dec = 1) { return fmtNum(v, dec); }
    monthLbl(m) { return monthLabel(m); }
    marginClass(m) {
        if (m === undefined || m === null) return "";
        return m < 0 ? "bad" : m < 15 ? "mid" : "good";
    }
}

registry.category("lazy_components").add("SomAnalytics", SomAnalytics);
