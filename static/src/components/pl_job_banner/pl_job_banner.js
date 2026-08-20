/** @odoo-module **/
/**
 * Banner de avance del job de PL físico en segundo plano.
 *
 * Field widget (many2one tc.physical.pl.job) para el form de la recepción:
 * hace polling del job cada 2.5 s mientras está en cola/procesando y pinta
 * la barra SOM con el avance REAL (filas conciliadas). Al terminar recarga
 * el formulario (los botones Procesar/Reprocesar dependen de
 * packing_list_imported); si truena, muestra el error completo en rojo.
 */
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, useState, onWillStart, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const POLL_MS = 2500;
const JOB_FIELDS = [
    "state", "progress_done", "progress_total", "progress_label",
    "error_message", "result_message", "write_date",
];

export class TcPlJobBanner extends Component {
    static template = "stock_transit_allocation.TcPlJobBanner";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ job: null });
        this._timer = null;
        this._sawLive = false;

        onWillStart(() => this._fetch());
        onWillUnmount(() => this._stop());
    }

    get jobId() {
        const val = this.props.record?.data?.[this.props.name];
        // m2o en OWL: {id, display_name} o [id, name] según la vía de carga.
        if (!val) return false;
        if (Array.isArray(val)) return val[0] || false;
        if (typeof val === "object") return val.id || val.resId || false;
        return val || false;
    }

    get pct() {
        const j = this.state.job;
        if (!j || !j.progress_total) return 0;
        return Math.max(0, Math.min(100, Math.round((j.progress_done / j.progress_total) * 100)));
    }

    async _fetch() {
        if (!this.jobId) return;
        try {
            const [job] = await this.orm.read("tc.physical.pl.job", [this.jobId], JOB_FIELDS);
            const prev = this.state.job;
            this.state.job = job || null;

            if (job && (job.state === "pending" || job.state === "running")) {
                this._sawLive = true;
                this._schedule();
            } else {
                this._stop();
                // Vio el job correr y terminó BIEN → recargar el form para
                // que aparezcan los botones de la siguiente etapa.
                if (job && job.state === "done" && this._sawLive && prev?.state !== "done") {
                    this.action.doAction({ type: "ir.actions.client", tag: "soft_reload" });
                }
            }
        } catch {
            // Registro borrado o sin acceso: dejar de pollear en silencio.
            this._stop();
        }
    }

    _schedule() {
        if (this._timer) return;
        this._timer = setInterval(() => this._fetch(), POLL_MS);
    }

    _stop() {
        if (this._timer) {
            clearInterval(this._timer);
            this._timer = null;
        }
    }
}

registry.category("fields").add("tc_pl_job_banner", {
    component: TcPlJobBanner,
    displayName: "Avance de PL en segundo plano",
    supportedTypes: ["many2one"],
});
