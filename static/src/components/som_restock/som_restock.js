/** @odoo-module **/
// SOM Restock — plan de compra automático, verificador y radar.
//
//  · PLAN DE COMPRA (default): CERO inputs. El sistema cruza consumo
//    medido, stock libre, tránsito libre y lead time medido por proveedor
//    y arma solo el pedido del día, agrupado por proveedor.
//  · VERIFICADOR: "necesito N de X" → busca el material, y responde cuánto
//    cubre el stock, cuánto el tránsito (con ETA) y cuánto comprar; el
//    proveedor lo detecta solo y sugiere el relleno del mismo proveedor.
//  · RADAR: tabla completa de materiales CON consumo (sin consumo no hay
//    ritmo que medir: esos materiales no aparecen, solo se pueden
//    verificar puntualmente).
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

const STATUS_META = {
    urgent:  { label: "PEDIR YA",   hint: "Se acaba antes de que llegue un pedido nuevo" },
    soon:    { label: "PRÓXIMO",    hint: "Toca pedirlo en este ciclo" },
    ok:      { label: "CUBIERTO",   hint: "Cobertura suficiente" },
    no_data: { label: "SIN CONSUMO", hint: "Sin salidas medidas: no hay ritmo que calcular" },
};

function fmtQty(v) {
    if (v === null || v === undefined || v === false) return "—";
    return Number(v).toLocaleString("en-US", {
        minimumFractionDigits: 0, maximumFractionDigits: 2,
    });
}

function fmtDate(iso) {
    if (!iso) return "—";
    const [y, m, d] = iso.split("-");
    return `${d}/${m}/${y}`;
}

export class SomRestock extends Component {
    static template = "stock_transit_allocation.SomRestock";
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.STATUS_META = STATUS_META;
        this.fmtQty = fmtQty;
        this.fmtDate = fmtDate;

        this.state = useState({
            loading: true,
            tab: "plan",               // plan | check | radar
            data: { rows: [], suppliers: [], params: {} },
            search: "",
            statusFilter: "all",
            // Verificador
            checkQuery: "",
            checkProduct: null,
            checkQty: "",
            checkLoading: false,
            advice: null,
        });

        onWillStart(() => this.loadData());
    }

    async loadData() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("som.restock", "get_restock_dashboard", []);
        } catch (e) {
            console.error("[SomRestock] Error cargando datos:", e);
            this.notification.add("No se pudo cargar Restock.", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    // Materiales con ritmo medido: única población del radar y del plan.
    get measuredRows() {
        return this.state.data.rows.filter((r) => r.status !== "no_data");
    }

    // ── PLAN DE COMPRA (se arma solo, agrupado por proveedor) ───────────
    get purchasePlan() {
        const urgencyOrder = { urgent: 0, soon: 1 };
        const rows = this.measuredRows.filter(
            (r) => r.suggested_qty > 0 && (r.status === "urgent" || r.status === "soon"));
        const groups = new Map();
        for (const r of rows) {
            const key = r.supplier_id || 0;
            if (!groups.has(key)) {
                groups.set(key, {
                    supplier_id: r.supplier_id,
                    supplier_name: r.supplier_name || "Proveedor por definir",
                    lead_days: r.lead_days,
                    lead_source: r.lead_source,
                    lines: [],
                    total: 0,
                    urgentCount: 0,
                });
            }
            const g = groups.get(key);
            g.lines.push(r);
            g.total += r.suggested_qty;
            if (r.status === "urgent") g.urgentCount++;
        }
        const plan = [...groups.values()];
        for (const g of plan) {
            g.lines.sort((a, b) =>
                (urgencyOrder[a.status] ?? 9) - (urgencyOrder[b.status] ?? 9) ||
                (a.cover_months ?? 999) - (b.cover_months ?? 999));
            g.total = Math.round(g.total * 100) / 100;
        }
        plan.sort((a, b) => b.urgentCount - a.urgentCount || b.total - a.total);
        return plan;
    }

    get planTotals() {
        const plan = this.purchasePlan;
        return {
            suppliers: plan.length,
            materials: plan.reduce((s, g) => s + g.lines.length, 0),
            urgent: plan.reduce((s, g) => s + g.urgentCount, 0),
        };
    }

    // ── RADAR ───────────────────────────────────────────────────────────
    get filteredRows() {
        const q = this.state.search.trim().toUpperCase();
        return this.measuredRows.filter((r) => {
            if (this.state.statusFilter !== "all" && r.status !== this.state.statusFilter) {
                return false;
            }
            if (!q) return true;
            return (
                r.name.toUpperCase().includes(q) ||
                (r.code || "").toUpperCase().includes(q) ||
                (r.supplier_name || "").toUpperCase().includes(q)
            );
        });
    }

    statusCount(key) {
        return this.measuredRows.filter((r) => r.status === key).length;
    }

    setStatusFilter(key) {
        this.state.statusFilter = this.state.statusFilter === key ? "all" : key;
    }

    // ── VERIFICADOR ("necesito N de X, ¿me alcanza?") ───────────────────
    // Busca sobre TODOS los materiales (incluidos sin consumo: aquí sí se
    // pueden analizar puntualmente contra stock y tránsito).
    get checkMatches() {
        const q = this.state.checkQuery.trim().toUpperCase();
        if (!q || this.state.checkProduct) return [];
        return this.state.data.rows
            .filter((r) =>
                r.name.toUpperCase().includes(q) ||
                (r.code || "").toUpperCase().includes(q))
            .slice(0, 8);
    }

    onCheckQueryInput(ev) {
        this.state.checkQuery = ev.target.value;
        this.state.checkProduct = null;
        this.state.advice = null;
    }

    selectCheckProduct(row) {
        this.state.checkProduct = row;
        this.state.checkQuery = row.name;
        this.state.advice = null;
    }

    clearCheck() {
        this.state.checkQuery = "";
        this.state.checkProduct = null;
        this.state.checkQty = "";
        this.state.advice = null;
    }

    async runCheck() {
        const row = this.state.checkProduct;
        if (!row) {
            this.notification.add("Escribe y elige el material que necesitas.", { type: "warning" });
            return;
        }
        const qty = parseFloat(this.state.checkQty) || 0;
        if (qty <= 0) {
            this.notification.add("Indica cuántos necesitas para verificar.", { type: "warning" });
            return;
        }
        this.state.checkLoading = true;
        try {
            // El proveedor lo resuelve el sistema (principal por volumen).
            this.state.advice = await this.orm.call(
                "som.restock", "get_purchase_advice",
                [row.supplier_id || false, row.product_id, qty]);
        } catch (e) {
            console.error("[SomRestock] Error en verificador:", e);
            this.notification.add("No se pudo calcular la verificación.", { type: "danger" });
        } finally {
            this.state.checkLoading = false;
        }
    }
}

registry.category("lazy_components").add("SomRestock", SomRestock);
