/** @odoo-module **/
// Medición de actividad del usuario dentro del webclient.
//
// Qué mide y cómo, para que los números signifiquen algo:
//  · TIEMPO ACTIVO  — la pestaña está VISIBLE y hubo teclado/mouse/scroll en
//    los últimos 90 s. Es trabajo real.
//  · TIEMPO INACTIVO — la pestaña está visible pero nadie la toca: leyendo,
//    en el teléfono, o se paró. Se mide aparte, jamás se suma como trabajo.
//  · La pestaña en SEGUNDO PLANO no cuenta nada. Dejar Odoo abierto toda la
//    noche no puede parecer una jornada.
//  · TIEMPO DE ESPERA — cuánto tardan las llamadas al servidor, leído del
//    PerformanceObserver del navegador (red + servidor, que es lo que el
//    usuario de verdad espera).
//
// Se acumula por PANTALLA (acción/modelo). Se envía cada 60 s y al cerrar la
// pestaña con sendBeacon, que es el único envío que sobrevive al cierre.
import { registry } from "@web/core/registry";
import { browser } from "@web/core/browser/browser";

const TICK_MS = 15_000;        // resolución de la medición
const FLUSH_MS = 60_000;       // cada cuánto se manda el lote
const IDLE_AFTER_MS = 90_000;  // sin input = inactivo
const PING_URL = "/som/activity/ping";

// Pantallas con nombre propio. Lo que no cae aquí se clasifica por su
// modelo (form/list de Odoo) y, en último caso, como "otro".
const ACTION_SCREENS = {
    inventory_visual_enhanced: ["inventario_visual", "Inventario Visual"],
    inventory_walkthrough: ["walkthrough", "Walkthrough"],
    "stock_transit_allocation.som_analytics": ["analytics", "SOM Analytics"],
    "stock_transit_allocation.som_restock": ["restock", "Restock"],
    action_transit_allocation: ["transito", "Asignación de Tránsito"],
    action_to_be_purchased: ["por_comprar", "Por Comprar"],
    action_to_be_allocated: ["por_asignar", "Por Asignar"],
    action_transit_kanban_custom: ["transito", "Embarques"],
    action_transit_fleet_map: ["transito", "Mapa de Embarques"],
    som_receptions_dashboard: ["recepciones", "Tablero de Recepciones"],
    cash_dashboard: ["caja", "Control de Efectivo"],
    stone_workshop_dashboard: ["taller", "Taller"],
};

const MODEL_SCREENS = {
    "sale.order": ["cotizacion", "Cotización / Orden de venta"],
    "stock.lot.hold.order": ["apartado", "Apartado"],
    "stock.picking": ["almacen", "Recepciones y entregas"],
    "purchase.order": ["compras", "Compras"],
    "product.template": ["productos", "Productos"],
    "stock.lot": ["placas", "Placas"],
    "res.partner": ["clientes", "Clientes"],
    "account.move": ["facturacion", "Facturación"],
};

export class SomActivityTracker {
    constructor(env) {
        this.env = env;
        this.token = this._makeToken();
        this.device = /Mobi|Android|iPhone|iPad/i.test(
            browser.navigator?.userAgent || "") ? "mobile" : "desktop";

        this.buckets = new Map();   // clave de pantalla -> acumulado
        this.lastInputAt = Date.now();
        // Pantalla anunciada por un diálogo (carrito, wizard de OV…): manda
        // sobre la acción de fondo mientras esté abierto.
        this.overlay = null;
        this.intervalStart = new Date();
        this.rpc = { count: 0, total: 0, max: 0 };

        this._boot();
    }

    // ── Arranque ────────────────────────────────────────────────────────
    _boot() {
        const doc = document;
        const opts = { passive: true, capture: true };
        for (const evt of ["pointerdown", "keydown", "wheel", "touchstart"]) {
            doc.addEventListener(evt, () => { this.lastInputAt = Date.now(); }, opts);
        }
        // El scroll se dispara en ráfagas: se marca sin trabajo extra.
        doc.addEventListener("scroll", () => { this.lastInputAt = Date.now(); },
            { passive: true, capture: true });

        doc.addEventListener("visibilitychange", () => {
            // Al ocultarse se corta el intervalo para no arrastrar tiempo de
            // segundo plano al bucket de la pantalla.
            this._tick();
            if (doc.visibilityState === "hidden") {
                this._flush(true);
            }
        });
        browser.addEventListener("pagehide", () => this._flush(true));

        this._watchRpcLatency();

        // Un diálogo puede declarar la pantalla real (el carrito no es una
        // acción, vive encima del Inventario Visual).
        this.env.bus.addEventListener("SOM_ACTIVITY:SCREEN", (ev) => {
            this._tick();
            const detail = ev.detail || {};
            this.overlay = detail.key
                ? { key: detail.key, label: detail.label || detail.key,
                    model: detail.model || "", resId: detail.res_id || 0 }
                : null;
        });

        this.tickTimer = browser.setInterval(() => this._tick(), TICK_MS);
        this.flushTimer = browser.setInterval(() => this._flush(false), FLUSH_MS);
    }

    _makeToken() {
        const uuid = window.crypto?.randomUUID?.();
        if (uuid) {
            return uuid;
        }
        return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    }

    // ── Espera del sistema ──────────────────────────────────────────────
    _watchRpcLatency() {
        const PO = window.PerformanceObserver;
        if (!PO) {
            return;
        }
        try {
            this.observer = new PO((list) => {
                for (const entry of list.getEntries()) {
                    if (!entry.name || entry.name.indexOf("/web/dataset/") === -1) {
                        continue;
                    }
                    const ms = Math.round(entry.duration || 0);
                    if (ms <= 0 || ms > 120_000) {
                        continue;
                    }
                    this.rpc.count += 1;
                    this.rpc.total += ms;
                    this.rpc.max = Math.max(this.rpc.max, ms);
                }
            });
            this.observer.observe({ type: "resource", buffered: false });
        } catch {
            // Navegador sin PerformanceObserver de recursos: se mide todo lo
            // demás igual, solo se pierde el tiempo de espera.
        }
    }

    // ── Pantalla actual ─────────────────────────────────────────────────
    _resolveScreen() {
        if (this.overlay) {
            return this.overlay;
        }
        try {
            const controller = this.env.services.action?.currentController;
            const action = controller?.action || {};
            const tag = action.tag || action.xml_id || "";
            if (tag && ACTION_SCREENS[tag]) {
                const [key, label] = ACTION_SCREENS[tag];
                return { key, label, model: action.res_model || "", resId: 0 };
            }
            const model = action.res_model || "";
            const resId = Number(controller?.props?.resId || 0) || 0;
            if (model && MODEL_SCREENS[model]) {
                const [key, label] = MODEL_SCREENS[model];
                return { key, label, model, resId };
            }
            if (model) {
                return { key: `modelo:${model}`, label: action.name || model, model, resId };
            }
            if (tag) {
                return { key: `accion:${tag}`, label: action.name || tag, model: "", resId: 0 };
            }
        } catch {
            // El webclient cambió de forma: mejor "otro" que romper la sesión.
        }
        return { key: "otro", label: "Otro", model: "", resId: 0 };
    }

    // ── Acumulación ─────────────────────────────────────────────────────
    _tick() {
        const now = new Date();
        // El inicio del intervalo que se está cerrando. Se guarda ANTES de
        // avanzar el cursor: si el bucket naciera con `now`, su inicio y su
        // fin serían el mismo instante y el servidor lo descartaría por
        // "fin <= inicio".
        const from = this.intervalStart;
        const elapsed = Math.round((now - from) / 1000);
        this.intervalStart = now;

        if (elapsed <= 0) {
            return;
        }
        // Más de 15 min de un tick: la máquina se durmió. Se tira.
        const visible = document.visibilityState === "visible";
        if (!visible || elapsed > 900) {
            this.rpc = { count: 0, total: 0, max: 0 };
            return;
        }

        const screen = this._resolveScreen();
        const bucketKey = `${screen.key}|${screen.model}|${screen.resId}`;
        let bucket = this.buckets.get(bucketKey);
        if (!bucket) {
            bucket = {
                screen: screen.key,
                label: screen.label,
                model: screen.model,
                res_id: screen.resId,
                start: from.toISOString(),
                active: 0,
                idle: 0,
                rpc_count: 0,
                rpc_ms_total: 0,
                rpc_ms_max: 0,
            };
            this.buckets.set(bucketKey, bucket);
        }

        const isActive = (Date.now() - this.lastInputAt) < IDLE_AFTER_MS;
        if (isActive) {
            bucket.active += elapsed;
        } else {
            bucket.idle += elapsed;
        }
        bucket.rpc_count += this.rpc.count;
        bucket.rpc_ms_total += this.rpc.total;
        bucket.rpc_ms_max = Math.max(bucket.rpc_ms_max, this.rpc.max);
        bucket.end = now.toISOString();
        this.rpc = { count: 0, total: 0, max: 0 };
    }

    // ── Envío ───────────────────────────────────────────────────────────
    _flush(closing) {
        this._tick();
        if (!this.buckets.size) {
            return;
        }
        const events = [];
        for (const bucket of this.buckets.values()) {
            if (!bucket.end || (!bucket.active && !bucket.idle)) {
                continue;
            }
            events.push({ ...bucket });
        }
        this.buckets.clear();
        if (!events.length) {
            return;
        }

        const payload = {
            jsonrpc: "2.0",
            method: "call",
            params: {
                session: this.token,
                device: this.device,
                events,
            },
        };
        const body = JSON.stringify(payload);

        // Al cerrar, sendBeacon es lo único que el navegador garantiza.
        if (closing && browser.navigator?.sendBeacon) {
            try {
                browser.navigator.sendBeacon(
                    PING_URL, new Blob([body], { type: "application/json" }));
                return;
            } catch {
                // Cae al fetch de abajo.
            }
        }
        browser.fetch(PING_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body,
            keepalive: true,
        }).catch(() => {
            // Medir jamás puede estorbar: si el ping falla, se pierde ese
            // lote y ya. Nada de reintentos acumulando memoria.
        });
    }

    stop() {
        browser.clearInterval(this.tickTimer);
        browser.clearInterval(this.flushTimer);
        this.observer?.disconnect?.();
        this._flush(true);
    }
}

export const somActivityTrackerService = {
    dependencies: ["action"],
    start(env) {
        // El portal y los tableros standalone no montan este bundle; aquí
        // solo entra el webclient de usuarios internos.
        let tracker = null;
        try {
            tracker = new SomActivityTracker(env);
        } catch (error) {
            console.warn("[SOM Activity] Medición desactivada:", error);
        }
        return {
            get token() {
                return tracker?.token || "";
            },
            flush() {
                tracker?._flush(false);
            },
            stop() {
                tracker?.stop();
            },
        };
    },
};

registry.category("services").add("som_activity_tracker", somActivityTrackerService);
