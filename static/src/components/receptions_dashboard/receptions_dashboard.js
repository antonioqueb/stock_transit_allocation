/** @odoo-module **/
// Tablero de Recepciones — centraliza todo lo que viene de tránsito hacia
// existencias: qué está por llegar (ETA de la API), qué ya publicó compras
// (listo para trabajar), qué está atrasado y qué ya se recepcionó.
// Refresco automático cada 60 s con skip si el payload no cambió.
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadBundle } from "@web/core/assets";
import { Component, onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";

export class ReceptionsDashboard extends Component {
    static template = "stock_transit_allocation.ReceptionsDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ loading: true, data: null });
        this.chartRef = useRef("chartWeekly");
        this.chart = null;
        this.timer = null;
        this.lastPayload = null;
        onMounted(async () => {
            await loadBundle("web.chartjs_lib");
            await this.load();
            this.timer = setInterval(() => this.load(), 60000);
        });
        onWillUnmount(() => {
            if (this.timer) {
                clearInterval(this.timer);
            }
            if (this.chart) {
                this.chart.destroy();
            }
        });
    }

    async load() {
        let data;
        try {
            data = await this.orm.call(
                "stock.transit.voyage",
                "get_receptions_dashboard_data",
                []
            );
        } catch {
            this.state.loading = false;
            return;
        }
        const payload = JSON.stringify(data);
        if (payload === this.lastPayload) {
            return;
        }
        this.lastPayload = payload;
        this.state.data = data;
        this.state.loading = false;
        await Promise.resolve();
        requestAnimationFrame(() => this.renderChart());
    }

    renderChart() {
        const d = this.state.data;
        if (!d || !this.chartRef.el || !window.Chart) {
            return;
        }
        if (this.chart) {
            this.chart.destroy();
            this.chart = null;
        }
        this.chart = new window.Chart(this.chartRef.el.getContext("2d"), {
            type: "bar",
            data: {
                labels: d.weekly.map((w) => w.week),
                datasets: [
                    {
                        type: "line",
                        label: "Recepciones",
                        data: d.weekly.map((w) => w.count),
                        borderColor: "#38BDF8",
                        backgroundColor: "rgba(56,189,248,.18)",
                        tension: 0.35,
                        yAxisID: "y1",
                        pointRadius: 3,
                    },
                    {
                        label: "m² recibidos",
                        data: d.weekly.map((w) => w.m2),
                        backgroundColor: "rgba(11,87,208,.78)",
                        borderRadius: 5,
                        maxBarThickness: 30,
                        yAxisID: "y",
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: "index", intersect: false },
                scales: {
                    y: { beginAtZero: true, grid: { color: "rgba(100,116,139,.12)" } },
                    y1: { beginAtZero: true, position: "right", ticks: { precision: 0 }, grid: { display: false } },
                    x: { grid: { display: false } },
                },
            },
        });
    }

    // ── Navegación ──────────────────────────────────────────────
    openVoyage(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "stock.transit.voyage",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openReception(id) {
        if (!id) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "stock.picking",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openReceptionsList() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Recepciones físicas",
            res_model: "stock.picking",
            domain: [["origin", "=like", "%(Recepción Física)"]],
            views: [[false, "list"], [false, "form"]],
            target: "current",
        });
    }

    etaBadge(c) {
        if (c.days_to_eta === null || c.days_to_eta === undefined) {
            return { text: "Sin ETA", cls: "muted" };
        }
        if (c.days_to_eta > 1) {
            return { text: `en ${c.days_to_eta} días`, cls: "blue" };
        }
        if (c.days_to_eta >= 0) {
            return { text: c.days_to_eta === 0 ? "hoy" : "mañana", cls: "sky" };
        }
        return { text: `hace ${-c.days_to_eta} días`, cls: "red" };
    }
}

registry
    .category("actions")
    .add("stock_transit_allocation.receptions_dashboard", ReceptionsDashboard);
