/** @odoo-module **/
// SOM Analytics — BI interactivo con drill-down (estilo Power BI).
//
// Principios:
//  · Click en CUALQUIER segmento de un gráfico => se convierte en filtro
//    global (cross-filtering) y TODO el tablero se recalcula.
//  · Cada gráfico puede expandirse (modal grande con su tabla de datos y
//    versiones alternativas del mismo gráfico: barras/línea/dona).
//  · Utilidad SIEMPRE con costo all-in (backend som.analytics).
//  · Chart.js vendorizado de Odoo (web.chartjs_lib): cero dependencias
//    externas, funciona offline.
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadBundle } from "@web/core/assets";
import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";

const BLUE = "#0b57d0";
const SKY = "#38bdf8";
const GREEN = "#10b981";
const AMBER = "#f59e0b";
const RED = "#ef4444";
const PALETTE = ["#0b57d0", "#38bdf8", "#10b981", "#f59e0b", "#8b5cf6", "#ef4444", "#0e7490", "#ec4899"];
const FONT = "'Inter', -apple-system, 'Segoe UI', Roboto, sans-serif";

const MONTHS_ES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
    "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];

function fmtMoney(v) {
    return new Intl.NumberFormat("es-MX", {
        style: "currency", currency: "MXN", maximumFractionDigits: 0,
    }).format(v || 0);
}
function fmtCompact(v) {
    const n = Math.abs(v || 0);
    if (n >= 1e6) return (v / 1e6).toFixed(1) + " M";
    if (n >= 1e3) return (v / 1e3).toFixed(0) + " k";
    return String(Math.round(v || 0));
}
function fmtNum(v, dec = 1) {
    return new Intl.NumberFormat("es-MX", {
        minimumFractionDigits: 0, maximumFractionDigits: dec,
    }).format(v || 0);
}
function monthLabel(m) {
    if (!m || m.length < 7) return m || "";
    return `${MONTHS_ES[parseInt(m.slice(5, 7), 10) - 1]} ${m.slice(2, 4)}`;
}

// Plugin: texto al centro de las donas (total + subtítulo).
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
        ctx.font = `800 17px ${FONT}`;
        ctx.fillStyle = "#0f172a";
        ctx.fillText(opts.text, x, y - 7);
        ctx.font = `600 10.5px ${FONT}`;
        ctx.fillStyle = "#64748b";
        ctx.fillText(opts.sub || "", x, y + 11);
        ctx.restore();
    },
};

export class SomAnalytics extends Component {
    static template = "stock_transit_allocation.SomAnalytics";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            error: "",
            data: null,
            filters: this.defaultFilters(),
            chips: [],
            preset: "12m",
            expanded: null,
        });
        this.charts = {};
        onMounted(async () => {
            await loadBundle("web.chartjs_lib");
            if (window.Chart && !window.Chart.registry.plugins.get("somCenterText")) {
                window.Chart.register(somCenterText);
            }
            await this.load();
        });
        onWillUnmount(() => this.destroyCharts());
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

    // ── Data ────────────────────────────────────────────────────────────
    async load() {
        this.state.loading = true;
        this.state.error = "";
        try {
            const data = await this.orm.call(
                "som.analytics", "get_analytics_data", [this.state.filters]);
            this.state.data = data;
        } catch (e) {
            this.state.error = (e.data && e.data.message) || String(e);
            this.state.loading = false;
            return;
        }
        this.state.loading = false;
        await Promise.resolve();
        requestAnimationFrame(() => this.renderAllCharts());
    }

    destroyCharts() {
        for (const c of Object.values(this.charts)) {
            try { c.destroy(); } catch { /* noop */ }
        }
        this.charts = {};
    }

    // ── Filtros / drill ─────────────────────────────────────────────────
    addFilter(key, value, label) {
        this.state.filters = { ...this.state.filters, [key]: value };
        this.state.chips = [
            ...this.state.chips.filter((c) => c.key !== key),
            { key, label },
        ];
        this.load();
    }

    removeFilter(key) {
        const f = { ...this.state.filters };
        delete f[key];
        this.state.filters = f;
        this.state.chips = this.state.chips.filter((c) => c.key !== key);
        this.load();
    }

    clearFilters() {
        this.state.filters = this.defaultFilters();
        this.state.chips = [];
        this.state.preset = "12m";
        this.load();
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
        this.load();
    }

    // ── Sparklines SVG de los KPI cards ─────────────────────────────────
    sparkPoints(field) {
        const rows = this.state.data?.by_month || [];
        if (rows.length < 2) return "";
        const vals = rows.map((r) => r[field] || 0);
        const min = Math.min(...vals);
        const max = Math.max(...vals);
        const span = max - min || 1;
        return vals.map((v, i) => {
            const x = (i / (vals.length - 1)) * 100;
            const y = 26 - ((v - min) / span) * 22;
            return `${x.toFixed(1)},${y.toFixed(1)}`;
        }).join(" ");
    }

    sparkArea(field) {
        const pts = this.sparkPoints(field);
        return pts ? `0,30 ${pts} 100,30` : "";
    }

    // ── Charts: helpers de estilo ───────────────────────────────────────
    _grad(canvasId, from, to) {
        const el = document.getElementById(canvasId);
        if (!el) return from;
        const g = el.getContext("2d").createLinearGradient(0, 0, 0, el.clientHeight || 300);
        g.addColorStop(0, from);
        g.addColorStop(1, to);
        return g;
    }

    baseOptions(onClickFn) {
        return {
            responsive: true,
            maintainAspectRatio: false,
            onClick: onClickFn,
            animation: { duration: 550, easing: "easeOutQuart" },
            onHover: (ev, els) => {
                ev.native.target.style.cursor = els.length ? "pointer" : "default";
            },
            plugins: {
                legend: {
                    labels: {
                        boxWidth: 9, boxHeight: 9, usePointStyle: true,
                        pointStyle: "circle", font: { size: 11, family: FONT, weight: "600" },
                        color: "#475569",
                    },
                },
                tooltip: {
                    backgroundColor: "rgba(15, 23, 42, .96)",
                    titleColor: "#f8fafc",
                    bodyColor: "#cbd5e1",
                    titleFont: { size: 12, weight: "700", family: FONT },
                    bodyFont: { size: 11.5, family: FONT },
                    padding: 12,
                    cornerRadius: 10,
                    boxWidth: 8,
                    boxHeight: 8,
                    usePointStyle: true,
                    callbacks: {
                        label: (ctx) => {
                            const ds = ctx.dataset.label || "";
                            const v = ctx.parsed.y ?? ctx.parsed;
                            const money = ctx.dataset.somMoney;
                            return ` ${ds}: ${money ? fmtMoney(v) : fmtNum(v)}`;
                        },
                    },
                },
            },
        };
    }

    _axisMoney() {
        return {
            beginAtZero: true,
            border: { display: false },
            grid: { color: "rgba(100,116,139,.10)" },
            ticks: {
                font: { size: 10.5, family: FONT }, color: "#94a3b8",
                callback: (v) => fmtCompact(v),
            },
        };
    }

    _axisPlain(size = 10.5) {
        return {
            border: { display: false },
            grid: { display: false },
            ticks: { font: { size, family: FONT, weight: "600" }, color: "#64748b" },
        };
    }

    makeChart(key, canvasId, config) {
        const el = document.getElementById(canvasId);
        if (!el || !window.Chart) return;
        if (this.charts[key]) {
            this.charts[key].destroy();
        }
        this.charts[key] = new window.Chart(el.getContext("2d"), config);
    }

    renderAllCharts() {
        const d = this.state.data;
        if (!d) return;
        this.renderMonthly(d);
        this.renderLevels(d);
        this.renderProducts(d);
        this.renderSellers(d);
        this.renderAging(d);
        this.renderTransit(d);
        if (this.state.expanded) this.renderExpanded();
    }

    renderMonthly(d) {
        const rows = d.by_month || [];
        const clickFn = (ev, els) => {
            if (!els.length) return;
            const row = rows[els[0].index];
            this.addFilter("month", row.key, `Mes: ${monthLabel(row.key)}`);
        };
        this.makeChart("monthly", "som_bi_monthly", {
            type: "bar",
            data: {
                labels: rows.map((r) => monthLabel(r.key)),
                datasets: [
                    {
                        label: "Venta MXN", data: rows.map((r) => r.venta),
                        backgroundColor: this._grad("som_bi_monthly", "rgba(11,87,208,.92)", "rgba(11,87,208,.55)"),
                        hoverBackgroundColor: BLUE,
                        borderRadius: 7, borderSkipped: false, maxBarThickness: 34,
                        somMoney: true, yAxisID: "y", order: 2,
                    },
                    {
                        label: "Utilidad all-in", data: rows.map((r) => r.utilidad),
                        backgroundColor: this._grad("som_bi_monthly", "rgba(16,185,129,.9)", "rgba(16,185,129,.5)"),
                        hoverBackgroundColor: GREEN,
                        borderRadius: 7, borderSkipped: false, maxBarThickness: 34,
                        somMoney: true, yAxisID: "y", order: 2,
                    },
                    {
                        type: "line", label: "m² vendidos",
                        data: rows.map((r) => r.m2),
                        borderColor: SKY, borderWidth: 2.5,
                        backgroundColor: "rgba(56,189,248,.12)",
                        pointBackgroundColor: "#fff", pointBorderColor: SKY,
                        pointBorderWidth: 2, pointRadius: 3.5, pointHoverRadius: 6,
                        tension: 0.4, fill: true, yAxisID: "y1", order: 1,
                    },
                ],
            },
            options: {
                ...this.baseOptions(clickFn),
                interaction: { mode: "index", intersect: false },
                scales: {
                    y: this._axisMoney(),
                    y1: {
                        beginAtZero: true, position: "right",
                        border: { display: false }, grid: { display: false },
                        ticks: { font: { size: 10, family: FONT }, color: "#7dd3fc" },
                    },
                    x: this._axisPlain(),
                },
            },
        });
    }

    renderLevels(d) {
        const rows = d.levels || [];
        const total = rows.reduce((s, r) => s + r.venta, 0);
        const clickFn = (ev, els) => {
            if (!els.length) return;
            const row = rows[els[0].index];
            this.addFilter("level", row.key, `Nivel: ${row.name}`);
        };
        this.makeChart("levels", "som_bi_levels", {
            type: "doughnut",
            data: {
                labels: rows.map((r) => r.name),
                datasets: [{
                    data: rows.map((r) => r.venta),
                    backgroundColor: PALETTE,
                    hoverOffset: 10,
                    borderWidth: 3, borderColor: "#fff", borderRadius: 5,
                }],
            },
            options: {
                ...this.baseOptions(clickFn),
                cutout: "68%",
                layout: { padding: 8 },
                plugins: {
                    ...this.baseOptions(clickFn).plugins,
                    somCenterText: { text: fmtCompact(total), sub: "venta MXN" },
                    legend: {
                        position: "bottom",
                        labels: {
                            boxWidth: 8, boxHeight: 8, usePointStyle: true,
                            pointStyle: "circle",
                            font: { size: 10.5, family: FONT, weight: "600" },
                            color: "#475569", padding: 10,
                        },
                    },
                    tooltip: {
                        ...this.baseOptions(clickFn).plugins.tooltip,
                        callbacks: {
                            label: (ctx) => {
                                const r = rows[ctx.dataIndex];
                                const pct = total ? (r.venta / total * 100).toFixed(1) : 0;
                                return ` ${r.name}: ${fmtMoney(r.venta)} (${pct}%) · ${fmtNum(r.m2)} m² · margen ${fmtNum(r.margen)}%`;
                            },
                        },
                    },
                },
            },
        });
    }

    renderProducts(d) {
        const rows = d.top_products || [];
        const clickFn = (ev, els) => {
            if (!els.length) return;
            const row = rows[els[0].index];
            this.addFilter("product_id", row.key, `Material: ${row.name}`);
        };
        this.makeChart("products", "som_bi_products", {
            type: "bar",
            data: {
                labels: rows.map((r) => r.name.length > 36 ? r.name.slice(0, 35) + "…" : r.name),
                datasets: [{
                    label: "Utilidad all-in", data: rows.map((r) => r.utilidad),
                    backgroundColor: rows.map((r) => r.utilidad >= 0
                        ? "rgba(16,185,129,.82)" : "rgba(239,68,68,.82)"),
                    hoverBackgroundColor: rows.map((r) => r.utilidad >= 0 ? GREEN : RED),
                    borderRadius: 6, borderSkipped: false, maxBarThickness: 20,
                    somMoney: true,
                }],
            },
            options: {
                ...this.baseOptions(clickFn),
                indexAxis: "y",
                plugins: {
                    ...this.baseOptions(clickFn).plugins,
                    legend: { display: false },
                    tooltip: {
                        ...this.baseOptions(clickFn).plugins.tooltip,
                        callbacks: {
                            title: (items) => rows[items[0].dataIndex]?.name || "",
                            label: (ctx) => {
                                const r = rows[ctx.dataIndex];
                                return [
                                    ` Utilidad: ${fmtMoney(r.utilidad)}  (margen ${fmtNum(r.margen)}%)`,
                                    ` Venta: ${fmtMoney(r.venta)} · ${fmtNum(r.m2)} m²`,
                                ];
                            },
                        },
                    },
                },
                scales: {
                    x: this._axisMoney(),
                    y: {
                        border: { display: false }, grid: { display: false },
                        ticks: { font: { size: 10, family: FONT, weight: "600" }, color: "#475569" },
                    },
                },
            },
        });
    }

    renderSellers(d) {
        const rows = d.by_salesperson || [];
        const clickFn = (ev, els) => {
            if (!els.length) return;
            const row = rows[els[0].index];
            this.addFilter("user_id", row.key, `Vendedor: ${row.name}`);
        };
        this.makeChart("sellers", "som_bi_sellers", {
            type: "bar",
            data: {
                labels: rows.map((r) => (r.name || "").split(" ")[0]),
                datasets: [
                    {
                        label: "Venta MXN", data: rows.map((r) => r.venta),
                        backgroundColor: this._grad("som_bi_sellers", "rgba(11,87,208,.9)", "rgba(11,87,208,.5)"),
                        borderRadius: 6, borderSkipped: false, maxBarThickness: 26,
                        somMoney: true,
                    },
                    {
                        label: "Utilidad all-in", data: rows.map((r) => r.utilidad),
                        backgroundColor: this._grad("som_bi_sellers", "rgba(16,185,129,.9)", "rgba(16,185,129,.5)"),
                        borderRadius: 6, borderSkipped: false, maxBarThickness: 26,
                        somMoney: true,
                    },
                ],
            },
            options: {
                ...this.baseOptions(clickFn),
                interaction: { mode: "index", intersect: false },
                scales: { y: this._axisMoney(), x: this._axisPlain(10) },
            },
        });
    }

    renderAging(d) {
        const rows = d.aging || [];
        this.makeChart("aging", "som_bi_aging", {
            type: "bar",
            data: {
                labels: rows.map((r) => r.bucket),
                datasets: [{
                    label: "m² en stock", data: rows.map((r) => r.m2),
                    backgroundColor: [GREEN, SKY, AMBER, RED, "#94a3b8"],
                    borderRadius: 7, borderSkipped: false, maxBarThickness: 46,
                }],
            },
            options: {
                ...this.baseOptions(() => {}),
                plugins: {
                    ...this.baseOptions(() => {}).plugins,
                    legend: { display: false },
                    tooltip: {
                        ...this.baseOptions(() => {}).plugins.tooltip,
                        callbacks: {
                            label: (ctx) => {
                                const r = rows[ctx.dataIndex];
                                return [
                                    ` ${fmtNum(r.m2)} m² en ${r.lots} lotes`,
                                    ` Valor all-in inmovilizado: ${fmtMoney(r.valor)}`,
                                ];
                            },
                        },
                    },
                },
                scales: { y: this._axisMoney(), x: this._axisPlain(9.5) },
            },
        });
    }

    renderTransit(d) {
        const rows = d.transit || [];
        this.makeChart("transit", "som_bi_transit", {
            type: "bar",
            data: {
                labels: rows.map((r) => r.label),
                datasets: [{
                    label: "m² en el agua", data: rows.map((r) => r.m2),
                    backgroundColor: this._grad("som_bi_transit", "rgba(56,189,248,.92)", "rgba(56,189,248,.45)"),
                    hoverBackgroundColor: SKY,
                    borderRadius: 7, borderSkipped: false, maxBarThickness: 46,
                }],
            },
            options: {
                ...this.baseOptions(() => {}),
                plugins: {
                    ...this.baseOptions(() => {}).plugins,
                    legend: { display: false },
                    tooltip: {
                        ...this.baseOptions(() => {}).plugins.tooltip,
                        callbacks: {
                            label: (ctx) => {
                                const r = rows[ctx.dataIndex];
                                return [
                                    ` ${fmtNum(r.m2)} m² en ${r.count} embarques`,
                                    ` Costo all-in flotante: ${fmtMoney(r.valor)}`,
                                ];
                            },
                        },
                    },
                },
                scales: { y: this._axisMoney(), x: this._axisPlain(9) },
            },
        });
    }

    // ── Expansión (modal con versiones del gráfico + tabla) ─────────────
    expandChart(chartKey) {
        this.state.expanded = { chartKey, variant: "bar" };
        requestAnimationFrame(() => this.renderExpanded());
    }

    closeExpanded() {
        if (this.charts.expanded) {
            this.charts.expanded.destroy();
            delete this.charts.expanded;
        }
        this.state.expanded = null;
    }

    setVariant(variant) {
        if (!this.state.expanded) return;
        this.state.expanded = { ...this.state.expanded, variant };
        requestAnimationFrame(() => this.renderExpanded());
    }

    expandedRows() {
        const d = this.state.data;
        if (!d || !this.state.expanded) return [];
        const key = this.state.expanded.chartKey;
        if (key === "monthly") {
            return (d.by_month || []).map((r) => ({
                name: monthLabel(r.key), venta: r.venta, utilidad: r.utilidad,
                m2: r.m2, margen: r.margen,
            }));
        }
        const src = {
            levels: d.levels, products: d.top_products,
            sellers: d.by_salesperson, customers: d.top_customers,
        }[key] || [];
        return src.map((r) => ({
            name: r.name, venta: r.venta, utilidad: r.utilidad,
            m2: r.m2, margen: r.margen,
        }));
    }

    expandedTitle() {
        return {
            monthly: "Venta y utilidad por mes",
            levels: "Mezcla de niveles de precio",
            products: "Top materiales por utilidad all-in",
            sellers: "Desempeño por vendedor",
            customers: "Top clientes",
        }[this.state.expanded?.chartKey] || "";
    }

    renderExpanded() {
        if (!this.state.expanded) return;
        const rows = this.expandedRows();
        const variant = this.state.expanded.variant;
        const type = variant === "dona" ? "doughnut" : variant === "linea" ? "line" : "bar";
        const total = rows.reduce((s, r) => s + (r.venta || 0), 0);
        const cfg = {
            type,
            data: {
                labels: rows.map((r) => r.name),
                datasets: type === "doughnut"
                    ? [{
                        data: rows.map((r) => r.venta),
                        backgroundColor: PALETTE, hoverOffset: 10,
                        borderWidth: 3, borderColor: "#fff", borderRadius: 5,
                    }]
                    : [
                        {
                            label: "Venta MXN", data: rows.map((r) => r.venta),
                            backgroundColor: type === "line" ? "rgba(11,87,208,.10)" : this._grad("som_bi_expanded", "rgba(11,87,208,.9)", "rgba(11,87,208,.5)"),
                            borderColor: BLUE, borderWidth: type === "line" ? 2.5 : 0,
                            pointBackgroundColor: "#fff", pointBorderColor: BLUE, pointBorderWidth: 2,
                            tension: 0.4, somMoney: true,
                            fill: type === "line", borderRadius: 6, borderSkipped: false,
                        },
                        {
                            label: "Utilidad all-in", data: rows.map((r) => r.utilidad),
                            backgroundColor: type === "line" ? "rgba(16,185,129,.10)" : this._grad("som_bi_expanded", "rgba(16,185,129,.9)", "rgba(16,185,129,.5)"),
                            borderColor: GREEN, borderWidth: type === "line" ? 2.5 : 0,
                            pointBackgroundColor: "#fff", pointBorderColor: GREEN, pointBorderWidth: 2,
                            tension: 0.4, somMoney: true,
                            fill: type === "line", borderRadius: 6, borderSkipped: false,
                        },
                    ],
            },
            options: {
                ...this.baseOptions(() => {}),
                cutout: type === "doughnut" ? "66%" : undefined,
                plugins: {
                    ...this.baseOptions(() => {}).plugins,
                    somCenterText: type === "doughnut"
                        ? { text: fmtCompact(total), sub: "venta MXN" } : undefined,
                },
                scales: type === "doughnut" ? undefined : {
                    y: this._axisMoney(),
                    x: this._axisPlain(),
                },
            },
        };
        this.makeChart("expanded", "som_bi_expanded", cfg);
    }

    // ── Navegación ──────────────────────────────────────────────────────
    openOrder(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "sale.order",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    // ── Formatters expuestos al template ────────────────────────────────
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
