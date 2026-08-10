/** @odoo-module **/
// SOM Restock — radar de recompra + asesor de compra.
//
//  · Radar: por material correlaciona stock libre, apartados, tránsito
//    (libre vs comprometido, con ETA), consumo mensual medido y lead time
//    medido del proveedor → cobertura en meses y alerta "ya toca pedir".
//  · Asesor: "necesito comprar N de X" → cuánto cubre el stock, cuánto el
//    tránsito, cuánto realmente comprar, y con qué materiales del mismo
//    proveedor conviene rellenar el pedido.
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

const STATUS_META = {
    urgent:  { label: "PEDIR YA",   hint: "Se acaba antes de que llegue un pedido nuevo" },
    soon:    { label: "PRÓXIMO",    hint: "Toca pedirlo en este ciclo" },
    ok:      { label: "CUBIERTO",   hint: "Cobertura suficiente" },
    no_data: { label: "SIN CONSUMO", hint: "Sin salidas en la ventana medida" },
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
            tab: "radar",              // radar | asesor
            data: { rows: [], suppliers: [], params: {} },
            search: "",
            statusFilter: "all",
            // Asesor
            advSupplierId: "",
            advProductId: "",
            advQty: "",
            advLoading: false,
            advice: null,
        });

        onWillStart(() => this.loadData());
    }

    async loadData() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("som.restock", "get_restock_dashboard", []);
        } catch (e) {
            console.error("[SomRestock] Error cargando radar:", e);
            this.notification.add("No se pudo cargar el radar de restock.", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    get filteredRows() {
        const q = this.state.search.trim().toUpperCase();
        return this.state.data.rows.filter((r) => {
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
        return this.state.data.rows.filter((r) => r.status === key).length;
    }

    setStatusFilter(key) {
        this.state.statusFilter = this.state.statusFilter === key ? "all" : key;
    }

    // ── Asesor ──────────────────────────────────────────────────────────
    get advisorProducts() {
        // Materiales del proveedor elegido (o todos si aún no hay proveedor).
        const sid = parseInt(this.state.advSupplierId, 10);
        return this.state.data.rows
            .filter((r) => !sid || r.supplier_id === sid)
            .slice()
            .sort((a, b) => a.name.localeCompare(b.name));
    }

    onAdvProductChange(ev) {
        this.state.advProductId = ev.target.value;
        const pid = parseInt(this.state.advProductId, 10);
        const row = this.state.data.rows.find((r) => r.product_id === pid);
        if (row && row.supplier_id && !this.state.advSupplierId) {
            this.state.advSupplierId = String(row.supplier_id);
        }
    }

    async runAdvice() {
        const sid = parseInt(this.state.advSupplierId, 10) || false;
        const pid = parseInt(this.state.advProductId, 10) || false;
        const qty = parseFloat(this.state.advQty) || 0;
        if (!sid && !pid) {
            this.notification.add("Elige un proveedor o un material para sugerir.", { type: "warning" });
            return;
        }
        this.state.advLoading = true;
        try {
            this.state.advice = await this.orm.call(
                "som.restock", "get_purchase_advice", [sid, pid, qty]);
        } catch (e) {
            console.error("[SomRestock] Error en asesor:", e);
            this.notification.add("No se pudo calcular la sugerencia.", { type: "danger" });
        } finally {
            this.state.advLoading = false;
        }
    }
}

registry.category("lazy_components").add("SomRestock", SomRestock);
