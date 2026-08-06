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
const GREEN = "#159957";
const AMBER = "#d97706";
const RED = "#e5484d";
const INK = "#0f172a";
const PALETTE = [BLUE, SKY, GREEN, AMBER, "#7c3aed", RED, "#0e7490", "#be185d"];

const MONTHS_ES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
    "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];

function fmtMoney(v) {
    return new Intl.NumberFormat("es-MX", {
        style: "currency", currency: "MXN", maximumFractionDigits: 0,
    }).format(v || 0);
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
            chips: [],          // [{key, label}]
            preset: "12m",
            expanded: null,     // {chartKey, variant}
        });
        this.charts = {};
        onMounted(async () => {
            await loadBundle("web.chartjs_lib");
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

    // ── Charts ──────────────────────────────────────────────────────────
    baseOptions(onClickFn) {
        return {
            responsive: true,
            maintainAspectRatio: false,
            onClick: onClickFn,
            plugins: {
                legend: { labels: { boxWidth: 12, font: { size: 11 } } },
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            const ds = ctx.dataset.label || "";
                            const v = ctx.parsed.y ?? ctx.parsed;
                            const money = ctx.dataset.somMoney;
                            return `${ds}: ${money ? fmtMoney(v) : fmtNum(v)}`;
                        },
                    },
                },
            },
            scales: undefined,
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
                        backgroundColor: "rgba(11,87,208,.78)",
                        borderRadius: 5, somMoney: true, yAxisID: "y",
                    },
                    {
                        label: "Utilidad all-in", data: rows.map((r) => r.utilidad),
                        backgroundColor: "rgba(21,153,87,.75)",
                        borderRadius: 5, somMoney: true, yAxisID: "y",
                    },
                    {
                        type: "line", label: "m² vendidos",
                        data: rows.map((r) => r.m2),
                        borderColor: SKY, backgroundColor: "rgba(56,189,248,.15)",
                        tension: 0.35, pointRadius: 3, yAxisID: "y1",
                    },
                ],
            },
            options: {
                ...this.baseOptions(clickFn),
                interaction: { mode: "index", intersect: false },
                scales: {
                    y: { beginAtZero: true, ticks: { callback: (v) => fmtNum(v / 1000, 0) + "k" } },
                    y1: { beginAtZero: true, position: "right", grid: { display: false } },
                    x: { grid: { display: false } },
                },
            },
        });
    }

    renderLevels(d) {
        const rows = d.levels || [];
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
                    backgroundColor: PALETTE, somMoney: true,
                    borderWidth: 2, borderColor: "#fff",
                }],
            },
            options: {
                ...this.baseOptions(clickFn),
                cutout: "58%",
                plugins: {
                    legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 10.5 } } },
                    tooltip: {
                        callbacks: {
                            label: (ctx) => {
                                const r = rows[ctx.dataIndex];
                                return ` ${r.name}: ${fmtMoney(r.venta)} · ${fmtNum(r.m2)} m² · margen ${fmtNum(r.margen)}%`;
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
                labels: rows.map((r) => r.name.length > 34 ? r.name.slice(0, 33) + "…" : r.name),
                datasets: [
                    {
                        label: "Utilidad all-in", data: rows.map((r) => r.utilidad),
                        backgroundColor: rows.map((r) => r.utilidad >= 0
                            ? "rgba(21,153,87,.78)" : "rgba(229,72,77,.78)"),
                        borderRadius: 4, somMoney: true,
                    },
                ],
            },
            options: {
                ...this.baseOptions(clickFn),
                indexAxis: "y",
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: (ctx) => {
                                const r = rows[ctx.dataIndex];
                                return ` Utilidad ${fmtMoney(r.utilidad)} · Venta ${fmtMoney(r.venta)} · ${fmtNum(r.m2)} m² · ${fmtNum(r.margen)}%`;
                            },
                        },
                    },
                },
                scales: {
                    x: { ticks: { callback: (v) => fmtNum(v / 1000, 0) + "k" } },
                    y: { ticks: { font: { size: 10 } } },
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
                labels: rows.map((r) => r.name),
                datasets: [
                    {
                        label: "Venta MXN", data: rows.map((r) => r.venta),
                        backgroundColor: "rgba(11,87,208,.75)", borderRadius: 4,
                        somMoney: true,
                    },
                    {
                        label: "Utilidad all-in", data: rows.map((r) => r.utilidad),
                        backgroundColor: "rgba(21,153,87,.75)", borderRadius: 4,
                        somMoney: true,
                    },
                ],
            },
            options: {
                ...this.baseOptions(clickFn),
                scales: {
                    y: { beginAtZero: true, ticks: { callback: (v) => fmtNum(v / 1000, 0) + "k" } },
                    x: { grid: { display: false }, ticks: { font: { size: 10 } } },
                },
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
                    borderRadius: 5,
                }],
            },
            options: {
                ...this.baseOptions(() => {}),
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: (ctx) => {
                                const r = rows[ctx.dataIndex];
                                return ` ${fmtNum(r.m2)} m² · ${r.lots} lotes · valor all-in ${fmtMoney(r.valor)}`;
                            },
                        },
                    },
                },
                scales: {
                    y: { beginAtZero: true },
                    x: { grid: { display: false }, ticks: { font: { size: 10 } } },
                },
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
                    backgroundColor: "rgba(56,189,248,.75)", borderRadius: 5,
                }],
            },
            options: {
                ...this.baseOptions(() => {}),
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: (ctx) => {
                                const r = rows[ctx.dataIndex];
                                return ` ${fmtNum(r.m2)} m² · ${r.count} embarques · costo ${fmtMoney(r.valor)}`;
                            },
                        },
                    },
                },
                scales: {
                    y: { beginAtZero: true },
                    x: { grid: { display: false }, ticks: { font: { size: 9.5 } } },
                },
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
        const cfg = {
            type,
            data: {
                labels: rows.map((r) => r.name),
                datasets: type === "doughnut"
                    ? [{
                        data: rows.map((r) => r.venta),
                        backgroundColor: PALETTE, borderWidth: 2, borderColor: "#fff",
                    }]
                    : [
                        {
                            label: "Venta MXN", data: rows.map((r) => r.venta),
                            backgroundColor: "rgba(11,87,208,.75)",
                            borderColor: BLUE, tension: 0.3, somMoney: true,
                            fill: type === "line" ? false : undefined, borderRadius: 4,
                        },
                        {
                            label: "Utilidad all-in", data: rows.map((r) => r.utilidad),
                            backgroundColor: "rgba(21,153,87,.75)",
                            borderColor: GREEN, tension: 0.3, somMoney: true,
                            fill: type === "line" ? false : undefined, borderRadius: 4,
                        },
                    ],
            },
            options: {
                ...this.baseOptions(() => {}),
                scales: type === "doughnut" ? undefined : {
                    y: { beginAtZero: true, ticks: { callback: (v) => fmtNum(v / 1000, 0) + "k" } },
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
    num(v, dec = 1) { return fmtNum(v, dec); }
    monthLbl(m) { return monthLabel(m); }
}

registry.category("lazy_components").add("SomAnalytics", SomAnalytics);
