// ─────────────────────────────────────────────────────────────────────────────
// Registro semántico de métricas (Fase 2 del rediseño).
//
// Una sola fuente de definición por métrica: unidad, dirección favorable,
// fórmula/metodología, modelos fuente y frescura. Los componentes KPI leen
// de aquí — nada de duplicar fórmulas o colores por página. La fórmula se
// expone al usuario en "explicar métrica" (tooltip).
// ─────────────────────────────────────────────────────────────────────────────

export type MetricUnit = "mxn" | "usd" | "sqm" | "pieces" | "boxes" | "days" | "hours" | "count" | "percent" | "rate";
export type MetricDirection = "higher_is_better" | "lower_is_better" | "contextual";

export type MetricDefinition = {
  id: string;
  label: string;
  description: string;      // fórmula/metodología en lenguaje llano
  unit: MetricUnit;
  direction: MetricDirection;
  sourceModels: string[];
  freshnessSeconds: number; // ticker 60 s · packs 300 s
};

const T = 60;    // ticker
const P = 300;   // packs de dominio

const DEFS: MetricDefinition[] = [
  // ── Ejecutivas (get_exec_summary) ──
  { id: "venta_hoy", label: "Venta de hoy", description: "Órdenes CONFIRMADAS hoy (state=sale), USD convertido al TC pactado de la orden o Banorte.", unit: "mxn", direction: "higher_is_better", sourceModels: ["sale.order"], freshnessSeconds: T },
  { id: "fact_real_mes", label: "Facturación real del mes", description: "Facturas de cliente PUBLICADAS (timbradas) del mes; amount_total_signed netea notas de crédito.", unit: "mxn", direction: "higher_is_better", sourceModels: ["account.move"], freshnessSeconds: T },
  { id: "fact_previa_mes", label: "Previas sin timbrar", description: "Facturas de cliente en BORRADOR del mes: separadas de la facturación real por regla contractual.", unit: "mxn", direction: "lower_is_better", sourceModels: ["account.move"], freshnessSeconds: T },
  { id: "venta_mes", label: "Pedidos del mes (sistema)", description: "Órdenes confirmadas del mes calendario; el cierre en el mes NO está garantizado.", unit: "mxn", direction: "higher_is_better", sourceModels: ["sale.order"], freshnessSeconds: T },
  { id: "utilidad_mes", label: "Utilidad del mes", description: "Venta − cantidad × costo all-in (x_costo_mayor, por compañía). Solo Autorizadores de Precios.", unit: "mxn", direction: "higher_is_better", sourceModels: ["sale.order.line", "product.template"], freshnessSeconds: T },
  { id: "m2_mes", label: "m² vendidos del mes", description: "Suma de cantidades de líneas con UoM de superficie; nunca se mezcla con piezas.", unit: "sqm", direction: "higher_is_better", sourceModels: ["sale.order.line"], freshnessSeconds: T },
  { id: "cajas_mes", label: "Cajas nacionales del mes", description: "Líneas confirmadas vendidas por empaque estándar (pack_qty).", unit: "boxes", direction: "higher_is_better", sourceModels: ["sale.order.line"], freshnessSeconds: T },
  { id: "bancos_mxn", label: "Dinero en bancos", description: "Balance contable de todos los diarios banco/efectivo (incluye Caja Nacional).", unit: "mxn", direction: "higher_is_better", sourceModels: ["account.move.line", "account.journal"], freshnessSeconds: T },
  { id: "por_cobrar", label: "Me deben", description: "Residual de facturas de cliente publicadas, MXN al TC del día de registro.", unit: "mxn", direction: "contextual", sourceModels: ["account.move"], freshnessSeconds: T },
  { id: "por_pagar", label: "Debo", description: "Residual de facturas de proveedor publicadas.", unit: "mxn", direction: "lower_is_better", sourceModels: ["account.move"], freshnessSeconds: T },
  { id: "inv_m2", label: "Inventario en patio", description: "m² en ubicaciones internas de productos de superficie.", unit: "sqm", direction: "contextual", sourceModels: ["stock.quant"], freshnessSeconds: T },
  { id: "inv_edad_dias", label: "Antigüedad de inventario", description: "Días desde la creación del lote, promedio ponderado por m² en stock.", unit: "days", direction: "lower_is_better", sourceModels: ["stock.quant", "stock.lot"], freshnessSeconds: T },
  { id: "m2_agua", label: "En el agua", description: "m² en viajes de tránsito no entregados/cancelados.", unit: "sqm", direction: "contextual", sourceModels: ["stock.transit.line"], freshnessSeconds: T },
  { id: "holds_activos", label: "Holds activos", description: "Apartados comerciales vivos; apartado ≠ vendido.", unit: "count", direction: "contextual", sourceModels: ["stock.lot.hold.order"], freshnessSeconds: T },
  { id: "auth_pendientes", label: "Autorizaciones pendientes", description: "Solicitudes de precio en estado pendiente.", unit: "count", direction: "lower_is_better", sourceModels: ["price.authorization"], freshnessSeconds: T },
  { id: "tc_banorte", label: "TC Banorte", description: "Tipo de cambio USD/MXN de referencia del día.", unit: "rate", direction: "contextual", sourceModels: [], freshnessSeconds: T },

  // ── Comerciales (get_dashboard 'comercial') ──
  { id: "venta_mxn", label: "Venta del periodo", description: "Líneas de órdenes confirmadas del rango filtrado, en MXN.", unit: "mxn", direction: "higher_is_better", sourceModels: ["sale.order.line"], freshnessSeconds: P },
  { id: "margen_pct", label: "Margen", description: "Utilidad all-in ÷ venta. Solo con permiso de costos.", unit: "percent", direction: "higher_is_better", sourceModels: ["sale.order.line", "product.template"], freshnessSeconds: P },
  { id: "conversion_pct", label: "Conversión", description: "Órdenes confirmadas ÷ (confirmadas + cotizaciones abiertas/enviadas) del periodo.", unit: "percent", direction: "higher_is_better", sourceModels: ["sale.order"], freshnessSeconds: P },
  { id: "cotizaciones_abiertas", label: "Cotizaciones abiertas", description: "Cotizaciones en borrador o enviadas sin confirmar.", unit: "count", direction: "contextual", sourceModels: ["sale.order"], freshnessSeconds: P },
  { id: "descuento_mxn", label: "Descuento otorgado", description: "Dinero descontado en líneas del periodo vs precio de lista.", unit: "mxn", direction: "lower_is_better", sourceModels: ["sale.order.line"], freshnessSeconds: P },
  { id: "realizacion_pct", label: "Realización de precio", description: "Precio real cobrado ÷ precio de lista.", unit: "percent", direction: "higher_is_better", sourceModels: ["sale.order.line"], freshnessSeconds: P },
  { id: "reincidencias_piso", label: "Reincidencias de piso", description: "Clientes que repiten compra a precio piso.", unit: "count", direction: "lower_is_better", sourceModels: ["sale.order.line"], freshnessSeconds: P },
  { id: "auth_delta_pct", label: "Δ autorizado", description: "Descuento promedio concedido en autorizaciones vs precio original.", unit: "percent", direction: "lower_is_better", sourceModels: ["price.authorization"], freshnessSeconds: P },
  { id: "bloqueadas_monto", label: "Dinero bloqueado", description: "Monto de órdenes detenidas esperando autorización de precio.", unit: "mxn", direction: "lower_is_better", sourceModels: ["price.authorization", "sale.order"], freshnessSeconds: P },
  { id: "auth_horas_resolucion", label: "Horas a resolver", description: "Tiempo promedio de resolución de autorizaciones del periodo.", unit: "hours", direction: "lower_is_better", sourceModels: ["price.authorization"], freshnessSeconds: P },
  { id: "comisiones_mxn", label: "Comisiones", description: "Comisiones devengadas del periodo (fecha plana), todos los roles.", unit: "mxn", direction: "contextual", sourceModels: ["commission.move"], freshnessSeconds: P },
  { id: "pct_via_arquitecto", label: "Vía embajador", description: "% de órdenes del periodo originadas por un embajador/especificador.", unit: "percent", direction: "contextual", sourceModels: ["sale.order"], freshnessSeconds: P },
  { id: "exposicion_usd", label: "Exposición USD", description: "Venta USD entregada aún no cobrada (riesgo cambiario).", unit: "usd", direction: "lower_is_better", sourceModels: ["sale.order", "account.move"], freshnessSeconds: P },
  { id: "fx_realizado_mxn", label: "FX realizado", description: "Resultado cambiario realizado: TC pactado vs TC del día de cobro.", unit: "mxn", direction: "contextual", sourceModels: ["sale.order", "account.payment"], freshnessSeconds: P },
];

export const METRICS: Record<string, MetricDefinition> = Object.fromEntries(DEFS.map((d) => [d.id, d]));

export function metricTooltip(id: string): string {
  const m = METRICS[id];
  if (!m) return "";
  const src = m.sourceModels.length ? ` · Fuente: ${m.sourceModels.join(", ")}` : "";
  return `${m.description}${src} · Actualiza cada ${m.freshnessSeconds >= 300 ? "5 min" : "60 s"}`;
}

// Estado semántico según la DIRECCIÓN declarada (jamás verde solo por subir).
export function deltaTone(id: string, deltaPct: number | null | undefined): "good" | "bad" | "mid" | "" {
  if (deltaPct == null || !isFinite(deltaPct) || deltaPct === 0) return "";
  const m = METRICS[id];
  if (!m || m.direction === "contextual") return "mid";
  const up = deltaPct > 0;
  return (m.direction === "higher_is_better") === up ? "good" : "bad";
}
