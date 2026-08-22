/** @odoo-module **/
/**
 * Progreso visible para botones LENTOS de formulario (publicar embarque,
 * preparar recepción): el usuario veía la pantalla congelada sin saber si
 * algo avanzaba. Se engancha al bus de RPC (mismo mecanismo del indicador
 * de carga nativo): cuando arranca la llamada de uno de estos botones se
 * muestra el overlay SOM; al responder (bien o mal), se cierra.
 *
 * Todo es cosmético y ultra defensivo: si el bus o el payload cambian de
 * forma entre builds, simplemente no se muestra nada.
 */
import { rpcBus } from "@web/core/network/rpc";
import { somProgress } from "./som_progress";

const SLOW_BUTTONS = {
    action_publish_transit_inventory: {
        title: "Publicando inventario en tránsito",
        subtitle: "Asignado → Committed · libre → Disponible",
    },
    action_unpublish_transit_inventory: {
        title: "Ocultando inventario en tránsito",
        subtitle: "Retirando el material del Inventario Visual",
    },
    action_generate_reception: {
        title: "Preparando la recepción física",
        subtitle: "Resolviendo lotes y demanda desde el viaje",
    },
    action_sync_reception_from_voyage: {
        title: "Sincronizando la recepción",
        subtitle: "Revisando llegadas nuevas del embarque",
    },
};

const active = new Map(); // data.id del RPC -> handle del overlay

rpcBus.addEventListener("RPC:REQUEST", (ev) => {
    try {
        const detail = ev.detail || {};
        const url = detail.url
            || (detail.settings && detail.settings.url) || "";
        if (url.indexOf("/web/dataset/call_") === -1) {
            return;
        }
        const params = (detail.data && detail.data.params) || {};
        if (params.model !== "stock.transit.voyage") {
            return;
        }
        const cfg = SLOW_BUTTONS[params.method];
        if (!cfg || !detail.data || detail.data.id === undefined) {
            return;
        }
        active.set(detail.data.id, somProgress({
            title: cfg.title,
            subtitle: cfg.subtitle,
            steps: ["Procesando en el servidor"],
        }));
    } catch (e) {
        /* cosmético: jamás interfiere con el RPC */
    }
});

rpcBus.addEventListener("RPC:RESPONSE", (ev) => {
    try {
        const detail = ev.detail || {};
        const rpcId = detail.data && detail.data.id;
        if (rpcId === undefined || !active.has(rpcId)) {
            return;
        }
        const prog = active.get(rpcId);
        active.delete(rpcId);
        if (detail.error) {
            prog.fail("El servidor devolvió un error");
        } else {
            prog.done("Listo");
        }
    } catch (e) {
        /* cosmético */
    }
});
