// SOM Dashboard Ejecutivo — bundle React standalone (patrón portal proveedores).
// Tres niveles de lectura: titular (Resumen/pantalla de dirección) → contexto
// (vistas por dominio) → explicación (drill con breadcrumbs).
import { StrictMode, useCallback, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import {
  arr, num, money, n0, n1, pct, monthLabel, marginTone, Rec, rpc, MONTHS_ES,
  fetchExec, fetchBanks, fetchOrderLines, fetchTimeToSell,
  fetchDashboard, fetchDrill,
} from "./api";
import { ChartBox, baseOptions, axisMoney, axisPlain, C, PALETTE } from "./charts";
import { NAV, domainOf, pageOf } from "./nav";
import { EChartBox, ecBase, ecAxis, ecInk, EC } from "./echarts";
import { METRICS, metricTooltip, deltaTone } from "./metrics";
import "./styles.css";

// ─────────────────────────────────────────────────────────────────────────────
// Tema: claro por default, oscuro con el toggle (persistido)
// ─────────────────────────────────────────────────────────────────────────────
type Theme = "light" | "dark";

function initTheme(): Theme {
  const saved = localStorage.getItem("som_theme");
  const t: Theme = saved === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = t;
  return t;
}

// ─────────────────────────────────────────────────────────────────────────────
// Estado de navegación serializable en el hash (#view=ventas&date_from=…)
// ─────────────────────────────────────────────────────────────────────────────
type ViewKey =
  | "inicio"
  | "resumen" | "ventas" | "materiales" | "inventario" | "compras"
  | "transito" | "recepciones" | "taller" | "entregas" | "finanzas"
  | "pronosticos" | "control"
  | "ventas_conversion" | "ventas_clientes" | "ventas_productos"
  | "ventas_precios" | "ventas_auth" | "ventas_equipo"
  | "ventas_canales" | "ventas_fx";

const VIEWS: Array<{ key: ViewKey; label: string }> = [
  { key: "inicio", label: "Command Center" },
  { key: "resumen", label: "Resumen" },
  { key: "ventas_conversion", label: "Conversión" },
  { key: "ventas_clientes", label: "Clientes" },
  { key: "ventas_productos", label: "Productos" },
  { key: "ventas_precios", label: "Precios" },
  { key: "ventas_auth", label: "Autorizaciones" },
  { key: "ventas_equipo", label: "Equipo" },
  { key: "ventas_canales", label: "Canales" },
  { key: "ventas_fx", label: "FX" },
  { key: "ventas", label: "Ventas" },
  { key: "materiales", label: "Materiales" },
  { key: "inventario", label: "Inventario" },
  { key: "compras", label: "Compras" },
  { key: "transito", label: "Tránsito" },
  { key: "recepciones", label: "Recepciones" },
  { key: "taller", label: "Taller" },
  { key: "entregas", label: "Entregas" },
  { key: "finanzas", label: "Finanzas" },
  { key: "pronosticos", label: "Pronósticos" },
  { key: "control", label: "Control" },
];

type Filters = { date_from?: string; date_to?: string; month?: string; granularity?: string; categ_id?: number; user_id?: number; partner_id?: number; product_id?: number };

function readHash(): { view: ViewKey; filters: Filters } {
  const p = new URLSearchParams(window.location.hash.slice(1));
  const view = (p.get("view") as ViewKey) || "inicio";
  const filters: Filters = {};
  for (const k of ["date_from", "date_to", "month", "granularity"] as const) {
    const v = p.get(k);
    if (v) filters[k] = v;
  }
  for (const k of ["categ_id", "user_id", "partner_id", "product_id"] as const) {
    const v = p.get(k);
    if (v) filters[k] = parseInt(v, 10);
  }
  return { view: VIEWS.some((x) => x.key === view) ? view : "inicio", filters };
}

function writeHash(view: ViewKey, filters: Filters) {
  const p = new URLSearchParams();
  p.set("view", view);
  Object.entries(filters).forEach(([k, v]) => v != null && p.set(k, String(v)));
  history.replaceState(null, "", "#" + p.toString());
}

// Fecha LOCAL en formato YYYY-MM-DD. OJO: toISOString() convierte a UTC —
// después de las 18:00 de Monterrey (UTC-6) devolvía la fecha de MAÑANA y
// el preset "Hoy" quedaba vacío.
function localYMD(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function defaultRange(): Filters {
  // Default: el MES en curso (del día 1 a hoy).
  const to = new Date();
  const from = new Date(to);
  from.setDate(1);
  return { date_from: localYMD(from), date_to: localYMD(to) };
}

// ─────────────────────────────────────────────────────────────────────────────
// Drill: pila navegable con breadcrumbs
// ─────────────────────────────────────────────────────────────────────────────
type DrillNode =
  | { kind: "entity"; entity: "month" | "seller" | "customer" | "product" | "category" | "level"; value: string | number; label: string }
  | { kind: "order"; orderId: number; label: string }
  | { kind: "bucket"; mode: "date" | "folio"; value: string; label: string }
  | { kind: "finpartner"; side: "ar" | "ap"; partnerId: number; label: string };

// ─────────────────────────────────────────────────────────────────────────────
// Bloques UI base
// ─────────────────────────────────────────────────────────────────────────────
function Stat(props: { label: string; value: string; sub?: string; tone?: "good" | "bad" | "mid" | "" }) {
  return (
    <div className={"stat " + (props.tone ?? "")}>
      <div className="stat-l">{props.label}</div>
      <div className="stat-v">{props.value}</div>
      {props.sub && <div className="stat-s">{props.sub}</div>}
    </div>
  );
}

function Panel(props: { title: string; hint?: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <section className={"panel" + (props.wide ? " wide" : "")}>
      <header className="panel-h">
        <h3>{props.title}</h3>
        {props.hint && <span className="hint">{props.hint}</span>}
      </header>
      {props.children}
    </section>
  );
}

function Skeleton(props: { h?: number }) {
  return <div className="sk" style={{ height: props.h ?? 260 }} />;
}

function ErrorBox(props: { msg: string; retry: () => void }) {
  return (
    <div className="errbox">
      <span>{props.msg}</span>
      <button onClick={props.retry}>Reintentar</button>
    </div>
  );
}

function Empty(props: { msg: string }) {
  return <div className="empty">{props.msg}</div>;
}

function Pill(props: { tone: "good" | "mid" | "bad"; children: React.ReactNode }) {
  return <span className={"pill " + props.tone}>{props.children}</span>;
}

// Hook de carga por bloque sobre TanStack Query: caché + dedupe + retry.
function useData<T>(key: unknown[], fn: () => Promise<T>, opts?: { refetchInterval?: number }): { data: T | null; loading: boolean; error: string; retry: () => void } {
  const q = useQuery({
    queryKey: key, queryFn: fn, staleTime: 60_000, retry: 1,
    refetchOnWindowFocus: false, refetchInterval: opts?.refetchInterval,
  });
  return {
    data: q.data ?? null,
    loading: q.isPending,
    error: q.error ? (q.error as Error).message : "",
    retry: () => void q.refetch(),
  };
}

function prettyStatus(s: unknown): string {
  const raw = String(s ?? "").replace(/_/g, " ");
  return raw ? raw[0].toUpperCase() + raw.slice(1) : "";
}

// ─────────────────────────────────────────────────────────────────────────────
// Tabla de órdenes (reutilizada en vistas y drill)
// ─────────────────────────────────────────────────────────────────────────────
function OrdersTable(props: { orders: Rec[]; onOrder: (id: number, name: string) => void }) {
  if (!props.orders.length) return <Empty msg="Sin órdenes en el corte actual" />;
  return (
    <div className="tablewrap">
      <table>
        <thead>
          <tr>
            <th>Orden</th><th>Fecha</th><th>Cliente</th><th>Vendedor</th>
            <th className="r">m²</th><th className="r">Venta</th><th className="r">Utilidad</th><th className="r">Margen</th>
          </tr>
        </thead>
        <tbody>
          {props.orders.map((o) => (
            <tr key={String(o.id)} className="click" onClick={() => props.onOrder(num(o.id), String(o.name))} tabIndex={0}
                onKeyDown={(e) => e.key === "Enter" && props.onOrder(num(o.id), String(o.name))}>
              <td className="mono">{String(o.name)}</td>
              <td className="mut">{String(o.date)}</td>
              <td className="ell">{String(o.partner)}</td>
              <td className="ell mut">{String(o.seller)}</td>
              <td className="r">{n1(o.m2)}</td>
              <td className="r strong">{money(o.venta)}</td>
              <td className={"r " + (num(o.utilidad) < 0 ? "neg" : "")}>{money(o.utilidad)}</td>
              <td className="r"><Pill tone={marginTone(num(o.margen))}>{pct(o.margen)}</Pill></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MiniTable(props: { head: [string, string, string]; rows: Array<{ key: React.Key; a: string; b: string; c: React.ReactNode; onClick?: () => void }> }) {
  if (!props.rows.length) return <Empty msg="Sin datos en el periodo" />;
  return (
    <div className="minitable">
      <div className="mrow head"><span>{props.head[0]}</span><b>{props.head[1]}</b><b>{props.head[2]}</b></div>
      {props.rows.map((r) => (
        <div key={r.key} className={"mrow" + (r.onClick ? " click" : "")} onClick={r.onClick} tabIndex={r.onClick ? 0 : -1}
             onKeyDown={(e) => e.key === "Enter" && r.onClick?.()}>
          <span className="ell" title={r.a}>{r.a}</span><b>{r.b}</b><b>{r.c}</b>
        </div>
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// RESUMEN: pantalla de dirección — rotación lenta entre dominios, cifras
// grandes, datos que se refrescan solos. Pensada para dejarse en un monitor.
// ─────────────────────────────────────────────────────────────────────────────
const TV_ROTATE_MS = 60_000;
const TV_REFRESH_MS = 300_000;

// La cifra completa SIEMPRE: si es larga, baja el tamaño de fuente en
// escalones (sz2/sz3) en vez de recortarla con elipsis.
function TvStat(props: { label: string; value: string; sub?: string; tone?: "good" | "bad" | "mid" | "" }) {
  const sz = props.value.length >= 15 ? " sz3" : props.value.length >= 11 ? " sz2" : "";
  return (
    <div className={"tv-stat " + (props.tone ?? "")}>
      <div className="l">{props.label}</div>
      <div className={"v" + sz}>{props.value}</div>
      {props.sub && <div className="s">{props.sub}</div>}
    </div>
  );
}

function TvExec() {
  const q = useData(["exec"], fetchExec, { refetchInterval: 60_000 });
  if (q.loading) return <><Skeleton h={140} /><Skeleton h={140} /></>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const d = q.data!;
  const mom = d.venta_mom_pct;
  return (
    <div className="tv-stats">
      <TvStat label="Venta de hoy" value={money(d.venta_hoy)} sub="MXN" />
      {/* Medición mensual diaria: facturación real vs previas vs pedidos */}
      <TvStat label="Facturación real del mes" value={money(d.fact_real_mes)} sub="timbrada (publicada), notas de crédito descontadas" tone="good" />
      <TvStat label="Previas sin timbrar" value={money(d.fact_previa_mes)} sub={`${n0(d.fact_previa_count)} facturas en borrador`} tone="mid" />
      <TvStat label="Venta cajas nacionales" value={`${n0(d.cajas_mes)} cajas`} sub={`${money(d.venta_cajas_mes)} en líneas por empaque estándar`} />
      <TvStat label="Pedidos del mes (sistema)" value={money(d.venta_mes)}
        sub={`${mom >= 0 ? "▲" : "▼"} ${pct(Math.abs(mom))} vs mes anterior · cierre en el mes no garantizado`} tone={mom >= 0 ? "good" : "bad"} />
      {d.perm_profit !== false && (
        <TvStat label="Utilidad del mes" value={money(d.utilidad_mes)} sub={`Margen ${pct(d.margen_mes)}`} tone={marginTone(d.margen_mes)} />
      )}
      <TvStat label="m² vendidos del mes" value={n1(d.m2_mes)} />
      <TvStat label="Dinero en bancos" value={money(d.bancos_mxn)} tone="good" />
      <TvStat label="Me deben" value={money(d.por_cobrar)} sub="MXN al TC del día de registro" />
      <TvStat label="Debo" value={money(d.por_pagar)} sub="MXN al TC del día de registro" tone="bad" />
      <TvStat label="Inventario en patio" value={`${n1(d.inv_m2)} m²`} sub={`${n0(d.holds_activos)} holds activos`} />
      <TvStat label="Antigüedad de inventario" value={`${n0(d.inv_edad_dias)} días`} sub="promedio desde creación del lote" tone={d.inv_edad_dias > 365 ? "bad" : d.inv_edad_dias > 180 ? "mid" : "good"} />
      <TvStat label="En el agua" value={`${n1(d.m2_agua)} m²`} sub={`${n0(d.contenedores_agua)} contenedores`} />
    </div>
  );
}

function TvVentas(props: { filters: Filters }) {
  const q = useData(["dashboard", "resumen", props.filters], () => fetchDashboard("resumen", props.filters as Rec), { refetchInterval: TV_REFRESH_MS });
  if (q.loading) return <><Skeleton h={140} /><Skeleton h={320} /></>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const d = q.data!;
  const k = (d.kpis ?? {}) as Rec;
  const months = arr(d.by_month);
  return (
    <>
      <div className="tv-stats">
        <TvStat label="Venta del periodo" value={money(k.venta_mxn)} sub={`${n0(k.ordenes)} órdenes`} />
        <TvStat label="Utilidad all-in" value={money(k.utilidad_mxn)} sub={`Margen ${pct(k.margen_pct)}`} tone={marginTone(num(k.margen_pct))} />
        <TvStat label="m² vendidos" value={n1(k.m2_vendidos)} sub={`${n0(k.piezas_vendidas)} piezas`} />
        <TvStat label="Inventario disponible" value={`${n1(k.inv_disponible_m2)} m²`} sub={`Valor ${money(k.inv_valor_mxn)}`} />
      </div>
      <Panel title="Venta y utilidad por mes" wide>
        <ChartBox height={340} deps={[months]} config={{
          type: "bar",
          data: {
            labels: months.map((r) => monthLabel(r.key)),
            datasets: [
              { label: "Venta", data: months.map((r) => num(r.venta)), backgroundColor: "rgba(11,87,208,.85)", borderRadius: 6, maxBarThickness: 40, isMoney: true },
              { label: "Utilidad", data: months.map((r) => num(r.utilidad)), backgroundColor: "rgba(5,150,105,.8)", borderRadius: 6, maxBarThickness: 40, isMoney: true },
            ],
          },
          options: { ...baseOptions(), interaction: { mode: "index", intersect: false }, scales: { y: axisMoney(), x: axisPlain(13) } },
        }} />
      </Panel>
    </>
  );
}

function TvInventario(props: { filters: Filters }) {
  const q = useData(["dashboard", "inventario", props.filters], () => fetchDashboard("inventario", props.filters as Rec), { refetchInterval: TV_REFRESH_MS });
  if (q.loading) return <><Skeleton h={140} /><Skeleton h={320} /></>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const d = q.data!;
  const k = (d.kpis ?? {}) as Rec;
  const agingDate = arr(d.aging_by_date);
  return (
    <>
      <div className="tv-stats">
        <TvStat label="Disponible" value={`${n1(k.disponible_m2)} m²`} sub={`${n0(k.lotes)} lotes`} />
        <TvStat label="Antigüedad de inventario" value={`${n0(k.edad_prom_dias)} días`} sub="promedio desde creación del lote" tone={num(k.edad_prom_dias) > 365 ? "bad" : num(k.edad_prom_dias) > 180 ? "mid" : "good"} />
        <TvStat label="En hold" value={`${n1(k.hold_m2)} m²`} sub={`${n0(k.holds_activos)} apartados`} />
        <TvStat label="Valor inmovilizado" value={money(k.valor_mxn)} />
        <TvStat label="Rotación 12 meses" value={`${n1(k.rotacion)}x`} sub={`${n1(k.meses_inventario)} meses de inventario`} tone={num(k.rotacion) < 2 ? "bad" : "good"} />
      </div>
      <Panel title="Antigüedad del inventario por fecha de creación del lote" wide>
        <ChartBox height={340} deps={[agingDate]} config={{
          type: "bar",
          data: { labels: agingDate.map((r) => String(r.bucket)), datasets: [{ label: "m²", data: agingDate.map((r) => num(r.m2)), backgroundColor: [C.green, "#22c55e", C.sky, C.amber, "#f97316", C.red, "#b91c1c", "#7f1d1d"], borderRadius: 8 }] },
          options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(12) } },
        }} />
      </Panel>
    </>
  );
}

function TvTransito() {
  const q = useData(["dashboard", "transito", {}], () => fetchDashboard("transito", {}), { refetchInterval: TV_REFRESH_MS });
  if (q.loading) return <><Skeleton h={140} /><Skeleton h={320} /></>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const d = q.data!;
  const k = (d.kpis ?? {}) as Rec;
  const st = arr(d.by_status);
  return (
    <>
      <div className="tv-stats">
        <TvStat label="m² en el agua" value={n1(k.total_m2)} sub={`${n0(k.embarques)} contenedores`} />
        <TvStat label="Pre-vendido" value={pct(k.prevendido_pct)} tone={num(k.prevendido_pct) > 50 ? "good" : ""} />
        <TvStat label="Desviación de ETA" value={`${n1(k.eta_desviacion_dias)} días`} sub={`${n0(k.eta_desviados)} viajes desviados`} />
        <TvStat label="Sin publicar" value={n0(k.pendientes_publicar)} sub="meta: 0" tone={num(k.pendientes_publicar) > 0 ? "bad" : "good"} />
      </div>
      <Panel title="Material en el agua por estatus del viaje" wide>
        <ChartBox height={340} deps={[st]} config={{
          type: "bar",
          data: { labels: st.map((r) => String(r.label)), datasets: [{ label: "m²", data: st.map((r) => num(r.m2)), backgroundColor: "rgba(2,132,199,.8)", borderRadius: 8 }] },
          options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(12) } },
        }} />
      </Panel>
    </>
  );
}

function TvFinanzas() {
  const banks = useData(["banks"], fetchBanks, { refetchInterval: TV_REFRESH_MS });
  const q = useData(["dashboard", "financiero", {}], () => fetchDashboard("financiero", {}), { refetchInterval: TV_REFRESH_MS });
  if (q.loading || banks.loading) return <><Skeleton h={140} /><Skeleton h={320} /></>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const d = q.data!;
  const k = (d.kpis ?? {}) as Rec;
  const arb = arr(d.ar_buckets);
  return (
    <>
      <div className="tv-stats">
        <TvStat label="Me deben" value={money(k.por_cobrar)} sub={`${n0(k.clientes_deudores)} clientes`} />
        <TvStat label="Debo" value={money(k.por_pagar)} tone="bad" />
        <TvStat label="Posición neta" value={money(k.neto)} tone={num(k.neto) >= 0 ? "good" : "bad"} />
        <TvStat label="Bancos y cajas" value={banks.data ? money(banks.data.total) : "—"} tone="good" />
      </div>
      <Panel title="Por cobrar por antigüedad" wide>
        <ChartBox height={340} deps={[arb]} config={{
          type: "bar",
          data: { labels: arb.map((r) => String(r.bucket)), datasets: [{ label: "MXN", data: arb.map((r) => num(r.monto)), backgroundColor: [C.green, "#84cc16", C.amber, "#f97316", C.red], borderRadius: 8, isMoney: true }] },
          options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(13) } },
        }} />
      </Panel>
    </>
  );
}

function TvCompras(props: { filters: Filters }) {
  const q = useData(["dashboard", "compras", props.filters], () => fetchDashboard("compras", props.filters as Rec), { refetchInterval: TV_REFRESH_MS });
  if (q.loading) return <><Skeleton h={140} /><Skeleton h={320} /></>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const d = q.data!;
  const k = (d.kpis ?? {}) as Rec;
  const months = arr(d.by_month);
  return (
    <>
      <div className="tv-stats">
        <TvStat label="Compras del periodo" value={money(k.compras_mxn)} sub={`${n0(k.proveedores)} proveedores`} />
        <TvStat label="Lead time OC → recepción" value={`${n1(k.lead_time_dias)} días`} sub={`${n0(k.lead_time_ocs)} órdenes medidas`} />
        <TvStat label="Costo logístico" value={`${money(k.costo_log_m2_mxn)} / m²`} />
        <TvStat label="Discrepancias PL" value={n0(k.discrepancias)} tone={num(k.discrepancias) > 0 ? "mid" : "good"} />
      </div>
      <Panel title="Compras por mes (MXN normalizado a TC actual)" wide>
        <ChartBox height={340} deps={[months]} config={{
          type: "bar",
          data: { labels: months.map((r) => monthLabel(r.key)), datasets: [{ label: "Compras", data: months.map((r) => num(r.mxn_norm)), backgroundColor: "rgba(124,58,237,.75)", borderRadius: 6, maxBarThickness: 40, isMoney: true }] },
          options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(13) } },
        }} />
      </Panel>
    </>
  );
}

function TvRecepciones(props: { filters: Filters }) {
  const q = useData(["dashboard", "recepciones", props.filters], () => fetchDashboard("recepciones", props.filters as Rec), { refetchInterval: TV_REFRESH_MS });
  if (q.loading) return <><Skeleton h={140} /><Skeleton h={320} /></>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const d = q.data!;
  const k = (d.kpis ?? {}) as Rec;
  const weeks = arr(d.by_week);
  return (
    <>
      <div className="tv-stats">
        <TvStat label="m² recibidos" value={n1(k.m2_recibidos)} sub={`${n0(k.entradas_compra)} entradas de compra`} />
        <TvStat label="Exactitud de recepción" value={pct(k.exactitud_pct)} sub={`${n0(k.con_devolucion)} con devolución`} tone={num(k.exactitud_pct) < 95 ? "mid" : "good"} />
        <TvStat label="Lotes con pedimento" value={pct(k.pedimento_pct)} sub={`${n0(k.lotes_sin_pedimento)} sin pedimento`} tone={num(k.pedimento_pct) < 90 ? "mid" : "good"} />
        <TvStat label="Faltantes vs worksheet" value={`${n1(k.faltantes_m2)} m²`} sub={`${n1(k.faltantes_piezas)} piezas`} tone={num(k.faltantes_m2) > 0 ? "bad" : "good"} />
      </div>
      <Panel title="m² recibidos por semana" wide>
        <ChartBox height={340} deps={[weeks]} config={{
          type: "bar",
          data: { labels: weeks.map((r) => String(r.week)), datasets: [{ label: "m²", data: weeks.map((r) => num(r.m2)), backgroundColor: "rgba(5,150,105,.75)", borderRadius: 6, maxBarThickness: 40 }] },
          options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(12) } },
        }} />
      </Panel>
    </>
  );
}

const TV_SLIDES: Array<{ key: string; label: string }> = [
  { key: "hoy", label: "Hoy" },
  { key: "ventas", label: "Ventas" },
  { key: "inventario", label: "Inventario" },
  { key: "transito", label: "Tránsito" },
  { key: "finanzas", label: "Finanzas" },
  { key: "compras", label: "Compras" },
  { key: "recepciones", label: "Recepciones" },
];

function useClock(): string {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(t);
  }, []);
  return now.toLocaleString("es-MX", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
}

function ResumenView(props: { filters: Filters; paused: boolean }) {
  const [idx, setIdx] = useState(0);
  const [cycle, setCycle] = useState(0);
  const clock = useClock();

  useEffect(() => {
    if (props.paused) return;
    const t = setInterval(() => {
      setIdx((i) => (i + 1) % TV_SLIDES.length);
      setCycle((c) => c + 1);
    }, TV_ROTATE_MS);
    return () => clearInterval(t);
  }, [props.paused, idx]);

  const slide = TV_SLIDES[idx];
  return (
    <>
      <div className="tv-head">
        <span className="tv-title">{slide.label}</span>
        <span className="tv-clock">{clock}</span>
        <span className="cur-chip" title="Los importes en USD se convierten al tipo de cambio que quedó registrado al facturar o entregar, no al de hoy">Cifras en MXN · USD al TC registrado</span>
        <div className="tv-dots" role="tablist" aria-label="Secciones del resumen">
          {TV_SLIDES.map((s, i) => (
            <button key={s.key} role="tab" aria-selected={i === idx} className={i === idx ? "on" : ""}
                    onClick={() => { setIdx(i); setCycle((c) => c + 1); }}>{s.label}</button>
          ))}
        </div>
      </div>
      <div className="tv-progress" aria-hidden="true">
        {!props.paused && <i key={cycle} style={{ animationDuration: `${TV_ROTATE_MS}ms` }} />}
      </div>
      <div className="tv-slide" key={slide.key}>
        {slide.key === "hoy" && <TvExec />}
        {slide.key === "ventas" && <TvVentas filters={props.filters} />}
        {slide.key === "inventario" && <TvInventario filters={props.filters} />}
        {slide.key === "transito" && <TvTransito />}
        {slide.key === "finanzas" && <TvFinanzas />}
        {slide.key === "compras" && <TvCompras filters={props.filters} />}
        {slide.key === "recepciones" && <TvRecepciones filters={props.filters} />}
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// VENTAS
// ─────────────────────────────────────────────────────────────────────────────
function VentasView(props: { filters: Filters; drill: (n: DrillNode) => void }) {
  // Origen: Odoo (default) vs SPS (legado Stone Profit — órdenes cuya
  // referencia trae el folio del sistema anterior). No se mezclan.
  const [source, setSource] = useState<"odoo" | "sps">("odoo");
  const filters = { ...props.filters, source } as Rec;
  const q = useData(["dashboard", "comercial", filters], () => fetchDashboard("comercial", filters));
  const srcSwitch = (
    <div className="src-switch" role="tablist" aria-label="Origen de las ventas">
      <button role="tab" aria-selected={source === "odoo"} className={source === "odoo" ? "on" : ""} onClick={() => setSource("odoo")}>Odoo</button>
      <button role="tab" aria-selected={source === "sps"} className={source === "sps" ? "on" : ""} onClick={() => setSource("sps")}>SPS (legado)</button>
    </div>
  );
  if (q.loading) return <>{srcSwitch}<div className="grid"><Skeleton h={90} /><Skeleton /><Skeleton /><Skeleton /></div></>;
  if (q.error) return <>{srcSwitch}<ErrorBox msg={q.error} retry={q.retry} /></>;
  const d = q.data!;
  const k = (d.kpis ?? {}) as Rec;
  const cats = arr(d.by_category);
  const sellers = arr(d.by_seller);
  const products = arr(d.top_products);
  const customers = arr(d.top_customers);
  return (
    <>
      {srcSwitch}
      <InsightStrip insights={[
        num(k.descuento_mxn) > 0 && num(k.venta_mxn) > 0 ? {
          metric_id: "descuento_mxn", severity: num(k.descuento_mxn) / (num(k.venta_mxn) + num(k.descuento_mxn)) > 0.08 ? "warn" : "info",
          text: `Se descontaron ${money(k.descuento_mxn)} (${pct((num(k.descuento_mxn) / (num(k.venta_mxn) + num(k.descuento_mxn))) * 100)} del precio de lista).`,
        } as Insight : null as unknown as Insight,
        customers.length > 0 && num(k.venta_mxn) > 0 && num(customers[0].venta) / num(k.venta_mxn) > 0.3 ? {
          metric_id: "venta_mxn", severity: "warn",
          text: `${String(customers[0].name)} concentra ${pct((num(customers[0].venta) / num(k.venta_mxn)) * 100)} de la venta del periodo.`,
        } as Insight : null as unknown as Insight,
        (d as Rec).perm_profit !== false && num(k.margen_pct) < 15 && num(k.venta_mxn) > 0 ? {
          metric_id: "margen_pct", severity: num(k.margen_pct) < 0 ? "crit" : "warn",
          text: `El margen all-in del periodo es ${pct(k.margen_pct)}.`,
        } as Insight : null as unknown as Insight,
      ].filter(Boolean) as Insight[]} />
      <div className="grid" style={{ marginBottom: 12 }}>
        <Panel title="De lista a utilidad: dónde se queda el dinero" hint="waterfall · precio lista → descuento → venta → costo → utilidad" wide>
          <EChartBox height={310} deps={[k.venta_mxn, k.descuento_mxn, k.utilidad_mxn]} option={(() => {
            const venta = num(k.venta_mxn);
            const desc = num(k.descuento_mxn);
            const lista = venta + desc;
            const canP = (d as Rec).perm_profit !== false;
            const util = num(k.utilidad_mxn);
            const costo = venta - util;
            const steps = canP ? [
              { label: "PRECIO LISTA", value: lista, base: 0, color: "blue" as const },
              { label: "− DESCUENTO", value: desc, base: venta, color: "amber" as const },
              { label: "VENTA", value: venta, base: 0, color: "blue" as const },
              { label: "− COSTO ALL-IN", value: costo, base: util, color: "red" as const },
              { label: "UTILIDAD", value: util, base: 0, color: "green" as const },
            ] : [
              { label: "PRECIO LISTA", value: lista, base: 0, color: "blue" as const },
              { label: "− DESCUENTO", value: desc, base: venta, color: "amber" as const },
              { label: "VENTA", value: venta, base: 0, color: "green" as const },
            ];
            return waterfallOpt(steps);
          })()} />
        </Panel>
        <Panel title="Realización de precio" hint="qué tanto se respeta la lista — subir es mejor">
          <EChartBox height={280} deps={[k.realizacion_pct]}
            option={gaugeOpt(num(k.realizacion_pct), "REALIZACIÓN", 110, (v) => pct(v),
              [[0.72, "#dc2626"], [0.86, "#d97706"], [1, "#059669"]])} />
        </Panel>
        <Panel title={(d as Rec).perm_profit !== false ? "Margen all-in del periodo" : "Conversión cotización → orden"} hint="semáforo ejecutivo">
          {(d as Rec).perm_profit !== false ? (
            <EChartBox height={280} deps={[k.margen_pct]}
              option={gaugeOpt(num(k.margen_pct), "MARGEN", 40, (v) => pct(v),
                [[0.15, "#dc2626"], [0.375, "#d97706"], [1, "#059669"]])} />
          ) : (
            <EChartBox height={280} deps={[k.conversion_pct]}
              option={gaugeOpt(num(k.conversion_pct), "CONVERSIÓN", 100, (v) => pct(v),
                [[0.3, "#dc2626"], [0.6, "#d97706"], [1, "#059669"]])} />
          )}
        </Panel>
      </div>
      <div className="grid" style={{ marginBottom: 12 }}>
        <Panel title="Carrera de venta mensual por categoría" hint="line race · top 6 categorías del periodo" wide>
          <EChartBox height={340} deps={[arr(d.categ_monthly)]} option={(() => {
            const cmr = arr(d.categ_monthly);
            if (!cmr.length) return { ...ecBase(), series: [] };
            const monthsRc = [...new Set(cmr.map((r) => String(r.month)))].sort();
            const categsRc = [...new Set(cmr.map((r) => String(r.categ)))];
            const valRc = new Map(cmr.map((r) => [`${r.categ}|${r.month}`, num(r.venta)]));
            const ents = categsRc.map((c2) => ({
              name: c2, serie: monthsRc.map((m) => valRc.get(`${c2}|${m}`) ?? 0),
            })) as unknown as Rec[];
            return raceOpt(ents, monthsRc, "name");
          })()} />
        </Panel>
      </div>
      <div className="stats">
        <Stat label="Venta" value={money(k.venta_mxn)} sub={`${n0(k.ordenes)} órdenes${source === "sps" ? " · LEGADO SPS" : ""}`} />
        <Stat label="Utilidad all-in" value={money(k.utilidad_mxn)} sub={`Margen ${pct(k.margen_pct)}`} tone={marginTone(num(k.margen_pct))} />
        <Stat label="Conversión cot→orden" value={pct(k.conversion_pct)} sub={`${n0(k.cotizaciones_abiertas)} abiertas`} />
        <Stat label="Descuentos" value={money(k.descuento_mxn)} sub={`Evitado ${money(k.descuento_evitado_mxn)}`} />
        <Stat label="Comisiones" value={money(k.comisiones_mxn)} />
        <Stat label="Realización de precio" value={pct(k.realizacion_pct)} sub="vs N1 de lista" />
      </div>
      <div className="grid">
        <Panel title="Venta por categoría de producto" hint="click = profundizar">
          <ChartBox height={300} deps={[cats]} config={{
            type: "bar",
            data: {
              labels: cats.map((r) => String(r.name)),
              datasets: [
                { label: "Venta", data: cats.map((r) => num(r.venta)), backgroundColor: "rgba(11,87,208,.85)", borderRadius: 6, maxBarThickness: 20, isMoney: true },
                { label: "Utilidad", data: cats.map((r) => num(r.utilidad)), backgroundColor: "rgba(5,150,105,.8)", borderRadius: 6, maxBarThickness: 20, isMoney: true },
              ],
            },
            options: {
              ...baseOptions((i) => {
                const r = cats[i];
                props.drill({ kind: "entity", entity: "category", value: num(r.key), label: `Categoría ${r.name}` });
              }),
              indexAxis: "y",
              scales: { x: axisMoney(), y: axisPlain(11) },
            },
          }} />
        </Panel>
        <Panel title="Vendedores" hint="click = profundizar">
          <ChartBox height={300} deps={[sellers]} config={{
            type: "bar",
            data: {
              labels: sellers.map((r) => String(r.name).split(" ")[0]),
              datasets: [
                { label: "Venta", data: sellers.map((r) => num(r.venta)), backgroundColor: "rgba(11,87,208,.85)", borderRadius: 6, isMoney: true },
                { label: "Utilidad", data: sellers.map((r) => num(r.utilidad)), backgroundColor: "rgba(5,150,105,.8)", borderRadius: 6, isMoney: true },
              ],
            },
            options: {
              ...baseOptions((i) => {
                const r = sellers[i];
                props.drill({ kind: "entity", entity: "seller", value: num(r.key), label: String(r.name) });
              }),
              interaction: { mode: "index", intersect: false },
              scales: { y: axisMoney(), x: axisPlain(11) },
            },
          }} />
        </Panel>
        <Panel title="Top materiales por utilidad" hint="click = profundizar">
          <ChartBox height={320} deps={[products]} config={{
            type: "bar",
            data: {
              labels: products.map((r) => String(r.name).slice(0, 38)),
              datasets: [{ label: "Utilidad", data: products.map((r) => num(r.utilidad)), backgroundColor: products.map((r) => (num(r.utilidad) >= 0 ? "rgba(5,150,105,.85)" : "rgba(220,38,38,.85)")), borderRadius: 5, maxBarThickness: 16, isMoney: true }],
            },
            options: {
              ...baseOptions((i) => {
                const r = products[i];
                props.drill({ kind: "entity", entity: "product", value: num(r.key), label: String(r.name) });
              }),
              indexAxis: "y",
              plugins: { ...baseOptions().plugins, legend: { display: false } },
              scales: { x: axisMoney(), y: axisPlain(10.5) },
            },
          }} />
        </Panel>
        <Panel title="Top clientes" hint="click = profundizar">
          <MiniTable head={["Cliente", "Venta", "Margen"]} rows={customers.map((c) => ({
            key: String(c.key), a: String(c.name), b: money(c.venta),
            c: <Pill tone={marginTone(num(c.margen))}>{pct(c.margen)}</Pill>,
            onClick: () => props.drill({ kind: "entity", entity: "customer", value: num(c.key), label: String(c.name) }),
          }))} />
        </Panel>
        <Panel title="Rendimiento del catálogo compartido" hint="links de galería, reservas que generó y clientes que compraron en 30 días" wide>
          {(() => {
            const cat = (d.catalogo ?? {}) as Rec;
            const bySeller = arr(d.catalogo_by_seller);
            return (
              <>
                <div className="stats" style={{ marginBottom: 12 }}>
                  <Stat label="Links creados" value={n0(cat.links)} sub={`${n0(cat.links_activos)} vigentes`} />
                  <Stat label="Reservas desde el catálogo" value={n0(cat.reservas)} sub={`${n0(cat.reservas_concretadas)} concretadas`} />
                  <Stat label="Clientes que compraron" value={n0(cat.clientes_compraron)} sub="con venta en 30 días tras el link" />
                  <Stat label="Conversión link → venta" value={pct(cat.conversion_pct)} tone={num(cat.conversion_pct) >= 30 ? "good" : num(cat.conversion_pct) > 0 ? "mid" : ""} />
                </div>
                <MiniTable head={["Vendedor", "Links", "Clientes que compraron"]} rows={bySeller.map((sr, i) => ({
                  key: i, a: String(sr.name), b: n0(sr.links), c: n0(sr.compraron),
                }))} />
              </>
            );
          })()}
        </Panel>
        <Panel title="Órdenes del corte" hint="click = utilidad por material" wide>
          <OrdersTable orders={arr(d.orders)} onOrder={(id, name) => props.drill({ kind: "order", orderId: id, label: name })} />
        </Panel>
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// MATERIALES
// ─────────────────────────────────────────────────────────────────────────────

// Edad legible en años y meses ("2 a 4 m"), nunca en días crudos.
function fmtAge(days: number): string {
  let y = Math.floor(days / 365);
  let m = Math.round((days % 365) / 30);
  if (m >= 12) { y += 1; m = 0; }
  if (!y) return `${m} m`;
  if (!m) return `${y} a`;
  return `${y} a ${m} m`;
}

// Lotes con fechas absurdas (> ~8 años) saturaban la gráfica de capital
// estancado: fuera del gráfico (siguen en la tabla de abajo).
const AGE_CAP_DAYS = 3000;

function MaterialesView(props: { filters: Filters; drill: (n: DrillNode) => void }) {
  const q = useData(["time_to_sell"], fetchTimeToSell);
  const inv = useData(["dashboard", "inventario", props.filters], () => fetchDashboard("inventario", props.filters as Rec));
  if (q.loading) return <div className="grid"><Skeleton h={90} /><Skeleton h={480} /></div>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const rows = arr(q.data);
  const slow = rows
    .filter((r) => r.edad_stock != null && num(r.edad_stock) <= AGE_CAP_DAYS)
    .sort((a, b) => num(b.edad_stock) - num(a.edad_stock))
    .slice(0, 12);
  const topStock = inv.data ? arr(inv.data.top_stock) : [];
  return (
    <>
      <div className="stats">
        <Stat label="Materiales analizados" value={n0(rows.length)} sub="con venta en 12 meses o stock con lote (top 200)" />
        <Stat label="Más lento en patio" value={slow[0] ? fmtAge(num(slow[0].edad_stock)) : "—"} sub={slow[0] ? String(slow[0].name).slice(0, 40) : ""} tone="bad" />
        <Stat label="m² en stock (analizados)" value={n1(rows.reduce((s, r) => s + num(r.m2_stock), 0))} />
        <Stat label="m² vendidos 12 meses" value={n1(rows.reduce((s, r) => s + num(r.m2_vendidos), 0))} />
      </div>
      {!rows.length && (
        <Empty msg="Sin datos de rotación: no hay lotes de material (m²) con movimientos a cliente en los últimos 12 meses ni stock actual con lote. Verifica que los productos de placa usen unidad de medida de área." />
      )}
      <div className="grid">
        {slow.length > 0 && (
          <Panel title="Capital estancado: edad del stock por material" hint="en años y meses · edades > 8 años fuera (lotes legacy) · click = profundizar" wide>
            <EChartBox height={Math.max(300, slow.length * 34 + 50)} deps={[slow]}
              onClick={(pm) => { const r = slow[pm.dataIndex]; if (r) props.drill({ kind: "entity", entity: "product", value: num(r.tmpl_id), label: String(r.name) }); }}
              option={(() => {
                const grad = (a: string, b: string) => ({
                  type: "linear", x: 0, y: 0, x2: 1, y2: 0,
                  colorStops: [{ offset: 0, color: a }, { offset: 1, color: b }],
                });
                return {
                  ...ecBase(),
                  grid: { left: 8, right: 84, top: 8, bottom: 8, containLabel: true },
                  tooltip: { ...(ecBase().tooltip as object),
                    formatter: (pm: { dataIndex: number }) => {
                      const r = slow[pm.dataIndex];
                      return r ? `${String(r.name)}<br/><b>${fmtAge(num(r.edad_stock))}</b> en patio · <b>${n1(r.m2_stock)} m²</b> detenidos · ${n0(r.lots_stock)} lotes` : "";
                    } },
                  xAxis: { type: "value",
                    splitLine: { lineStyle: { color: "rgba(100,116,139,.12)" } },
                    axisLabel: { fontSize: 10, fontFamily: "Inter",
                      formatter: (v: number) => fmtAge(v) },
                    interval: 365 },
                  yAxis: { type: "category", inverse: true,
                    data: slow.map((r) => String(r.name).slice(0, 34).toUpperCase()),
                    axisLine: { show: false }, axisTick: { show: false },
                    axisLabel: { fontSize: 10.5, fontFamily: "Inter" } },
                  series: [{
                    type: "bar", barMaxWidth: 20,
                    label: { show: true, position: "right", fontSize: 10.5, fontWeight: 800,
                             fontFamily: "Inter",
                             formatter: (pm: { dataIndex: number }) => {
                               const r = slow[pm.dataIndex];
                               return r ? `${fmtAge(num(r.edad_stock))} · ${n1(r.m2_stock)} m²` : "";
                             } },
                    data: slow.map((r) => {
                      const dias = num(r.edad_stock);
                      const color = dias > 730 ? grad("#f87171", "#dc2626")
                        : dias > 365 ? grad("#fb923c", "#ea580c")
                        : grad("#fbbf24", "#d97706");
                      return { value: dias, itemStyle: { color, borderRadius: [0, 8, 8, 0],
                        shadowBlur: 5, shadowColor: "rgba(15,23,42,.18)", shadowOffsetY: 2 } };
                    }),
                  }],
                };
              })()} />
          </Panel>
        )}
        {rows.length > 0 && (
          <Panel title="Tiempo de venta por material — los más lentos primero" hint="click en fila = profundizar" wide>
            <div className="tablewrap tall">
              <table>
                <thead>
                  <tr>
                    <th>Material</th>
                    <th className="r">Días prom. en vender</th><th className="r">m² vendidos 12m</th>
                    <th className="r">Edad stock</th><th className="r">m² en stock</th><th className="r">Lotes stock</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={String(r.tmpl_id)} className="click" onClick={() => props.drill({ kind: "entity", entity: "product", value: num(r.tmpl_id), label: String(r.name) })}>
                      <td className="ell">{String(r.name)}</td>
                      <td className="r">{r.dias_venta == null ? "—" : n1(r.dias_venta)}</td>
                      <td className="r">{n1(r.m2_vendidos)}</td>
                      <td className={"r " + (num(r.edad_stock) > 365 ? "neg" : "")}>{r.edad_stock == null ? "—" : fmtAge(num(r.edad_stock))}</td>
                      <td className="r strong">{n1(r.m2_stock)}</td>
                      <td className="r mut">{n0(r.lots_stock)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        )}
        {topStock.length > 0 && (
          <Panel title="Top materiales en stock (m² y valor all-in)" hint="click = profundizar" wide>
            <div className="tablewrap">
              <table>
                <thead>
                  <tr><th>Material</th><th className="r">m²</th><th className="r">Lotes</th><th className="r">Valor all-in</th></tr>
                </thead>
                <tbody>
                  {topStock.map((r) => (
                    <tr key={String(r.key)} className="click" onClick={() => props.drill({ kind: "entity", entity: "product", value: num(r.key), label: String(r.name) })}>
                      <td className="ell">{String(r.name)}</td>
                      <td className="r strong">{n1(r.m2)}</td>
                      <td className="r mut">{n0(r.lots)}</td>
                      <td className="r">{money(r.valor)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        )}
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// INVENTARIO
// ─────────────────────────────────────────────────────────────────────────────
function InventarioView(props: { filters: Filters; drill: (n: DrillNode) => void }) {
  const q = useData(["dashboard", "inventario", props.filters], () => fetchDashboard("inventario", props.filters as Rec));
  if (q.loading) return <div className="grid"><Skeleton h={90} /><Skeleton /><Skeleton /></div>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const d = q.data!;
  const k = (d.kpis ?? {}) as Rec;
  const aging = arr(d.aging);
  const top = arr(d.top_stock);
  const merma = arr(d.merma);
  return (
    <>
      <InsightStrip insights={[
        (() => { const old = aging.filter((b) => /180|365|\+/.test(String(b.bucket))).reduce((sm, b) => sm + num(b.m2), 0);
                 const tot = aging.reduce((sm, b) => sm + num(b.m2), 0);
                 return tot > 0 && old / tot > 0.35 ? {
          metric_id: "inv_edad_dias", severity: "warn",
          text: `${pct((old / tot) * 100)} del inventario (${n1(old)} m²) tiene más de 6 meses en patio.`,
        } as Insight : null as unknown as Insight; })(),
        num(k.hold_m2) > 0 && num(k.disponible_m2) > 0 ? {
          metric_id: "holds_activos", severity: "info",
          text: `${n1(k.hold_m2)} m² apartados (${n0(k.holds_activos)} holds) — apartado no es vendido.`,
        } as Insight : null as unknown as Insight,
        num(k.lotes_foto_pct) < 70 && num(k.lotes) > 0 ? {
          metric_id: "inv_m2", severity: "warn",
          text: `Solo ${pct(k.lotes_foto_pct)} de los lotes tiene fotografía: sin foto no se vende en catálogo.`,
        } as Insight : null as unknown as Insight,
      ].filter(Boolean) as Insight[]} />
      <div className="grid" style={{ marginBottom: 12 }}>
        <Panel title="Antigüedad del inventario" hint="semáforo — bajar es mejor">
          <EChartBox height={280} deps={[k.edad_prom_dias]}
            option={gaugeOpt(num(k.edad_prom_dias), "EDAD PROMEDIO", 540, (v) => `${n0(v)} d`,
              [[0.33, "#059669"], [0.67, "#d97706"], [1, "#dc2626"]])} />
        </Panel>
        <Panel title="Capital por antigüedad" hint="rosa · valor MXN por bucket de edad">
          <EChartBox height={280} deps={[aging]} option={{
            ...ecBase(),
            tooltip: { ...(ecBase().tooltip as object),
              formatter: (pm: { name: string; value: number; percent: number }) =>
                `${pm.name}<br/><b>${money(pm.value)}</b> · ${n1(pm.percent)}%` },
            series: [{
              type: "pie", roseType: "radius", radius: ["16%", "78%"], center: ["50%", "52%"],
              itemStyle: { borderRadius: 6, borderColor: "rgba(255,255,255,.4)", borderWidth: 2 },
              label: { fontSize: 10.5, fontWeight: 600 },
              data: aging.map((b, i) => ({ name: String(b.bucket).toUpperCase(), value: Math.max(num(b.valor), 0),
                itemStyle: { color: ["#059669", "#84cc16", "#d97706", "#f97316", "#dc2626"][i % 5] } })),
            }],
          }} />
        </Panel>
      </div>

      <div className="stats">
        <Stat label="Disponible" value={`${n1(k.disponible_m2)} m²`} sub={`${n0(k.lotes)} lotes`} />
        <Stat label="Antigüedad de inventario" value={`${n0(k.edad_prom_dias)} días`} sub="promedio desde creación del lote" tone={num(k.edad_prom_dias) > 365 ? "bad" : num(k.edad_prom_dias) > 180 ? "mid" : "good"} />
        <Stat label="En hold" value={`${n1(k.hold_m2)} m²`} sub={`${n0(k.holds_activos)} apartados · ${n1(k.holds_edad_dias)} días prom.`} />
        <Stat label="Valor all-in inmovilizado" value={money(k.valor_mxn)} />
        <Stat label="Rotación 12m" value={`${n1(k.rotacion)}x`} sub={`${n1(k.meses_inventario)} meses de inventario`} tone={num(k.rotacion) < 2 ? "bad" : "good"} />
        <Stat label="Committed en OV" value={`${n1(k.committed_m2)} m²`} />
        <Stat label="Conversión de holds" value={pct(k.holds_conversion_pct)} sub={`${n0(k.reservas_desplazadas)} reservas desplazadas`} />
        <Stat label="Bloques rotos" value={n0(k.bloques_rotos)} sub={`${n1(k.bloques_rotos_m2)} m² afectados de ${n0(k.bloques_activos)} bloques`} tone={num(k.bloques_rotos) > 0 ? "mid" : "good"} />
        <Stat label="Lotes con fotografía" value={pct(k.lotes_foto_pct)} sub={`${n0(k.lotes_con_foto)} con foto · ${n0(k.lotes_sin_foto)} sin foto`} tone={num(k.lotes_foto_pct) < 70 ? "bad" : num(k.lotes_foto_pct) < 90 ? "mid" : "good"} />
      </div>
      <div className="grid">
        <Panel title="Top materiales en stock" hint="click = profundizar">
          <ChartBox height={340} deps={[top]} config={{
            type: "bar",
            data: { labels: top.map((r) => String(r.name).slice(0, 36)), datasets: [{ label: "m²", data: top.map((r) => num(r.m2)), backgroundColor: "rgba(11,87,208,.85)", borderRadius: 5, maxBarThickness: 16 }] },
            options: {
              ...baseOptions((i) => {
                const r = top[i];
                props.drill({ kind: "entity", entity: "product", value: num(r.key), label: String(r.name) });
              }),
              indexAxis: "y",
              plugins: { ...baseOptions().plugins, legend: { display: false } },
              scales: { x: axisMoney(), y: axisPlain(10.5) },
            },
          }} />
        </Panel>
        <Panel title="Antigüedad por fecha de creación del lote" hint="click en una barra = qué materiales son">
          <ChartBox height={340} deps={[arr(d.aging_by_date)]} config={{
            type: "bar",
            data: { labels: arr(d.aging_by_date).map((r) => String(r.bucket)), datasets: [{ label: "m²", data: arr(d.aging_by_date).map((r) => num(r.m2)), backgroundColor: [C.green, "#22c55e", C.sky, C.amber, "#f97316", C.red, "#b91c1c", "#7f1d1d"], borderRadius: 6 }] },
            options: {
              ...baseOptions((i) => {
                const r = arr(d.aging_by_date)[i];
                props.drill({ kind: "bucket", mode: "date", value: String(r.bucket), label: `Antigüedad ${r.bucket}` });
              }),
              plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(9.5) },
            },
          }} />
        </Panel>
        <Panel title="Antigüedad por folio (regla Stone Profit)" hint="click en una barra = qué materiales son" wide>
          <ChartBox height={260} deps={[aging]} config={{
            type: "bar",
            data: { labels: aging.map((r) => String(r.bucket)), datasets: [{ label: "m²", data: aging.map((r) => num(r.m2)), backgroundColor: [C.green, C.sky, C.amber, C.red, "#64748b"], borderRadius: 6 }] },
            options: {
              ...baseOptions((i) => {
                const r = aging[i];
                props.drill({ kind: "bucket", mode: "folio", value: String(r.bucket), label: `Folios: ${r.bucket}` });
              }),
              plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(10) },
            },
          }} />
        </Panel>
        <Panel title="Merma dimensional por proveedor (PL vs medidas reales)" hint="m² facturados por el proveedor vs m² medidos en patio" wide>
          {!merma.length ? <Empty msg="Sin pares PL/medida real registrados aún" /> : (
            <div className="tablewrap">
              <table>
                <thead>
                  <tr><th>Proveedor</th><th className="r">m² según PL</th><th className="r">m² reales</th><th className="r">Merma m²</th><th className="r">Merma %</th></tr>
                </thead>
                <tbody>
                  {merma.map((m, i) => (
                    <tr key={i}>
                      <td className="ell">{String(m.name)}</td>
                      <td className="r">{n1(m.teorico)}</td>
                      <td className="r">{n1(m.real)}</td>
                      <td className={"r " + (num(m.merma_m2) > 0 ? "neg" : "")}>{n1(m.merma_m2)}</td>
                      <td className="r"><Pill tone={num(m.merma_pct) > 2 ? "bad" : num(m.merma_pct) > 0.5 ? "mid" : "good"}>{pct(m.merma_pct)}</Pill></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// COMPRAS
// ─────────────────────────────────────────────────────────────────────────────
function ComprasView(props: { filters: Filters }) {
  const q = useData(["dashboard", "compras", props.filters], () => fetchDashboard("compras", props.filters as Rec));
  if (q.loading) return <div className="grid"><Skeleton h={90} /><Skeleton /><Skeleton /></div>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const d = q.data!;
  const k = (d.kpis ?? {}) as Rec;
  const months = arr(d.by_month);
  const suppliers = arr(d.top_suppliers);
  const alloc = arr(d.allocations);
  return (
    <>
      <InsightStrip insights={[
        num(k.lead_time_dias) > 0 ? {
          metric_id: "venta_mes", severity: num(k.lead_time_dias) > 150 ? "warn" : "info",
          text: `Lead time medido de ${n1(k.lead_time_dias)} días (confirmar OC → recepción) sobre ${n0(k.lead_time_ocs)} órdenes.`,
        } as Insight : null as unknown as Insight,
        num(k.discrepancias) > 0 ? {
          metric_id: "venta_mes", severity: "warn",
          text: `${n0(k.discrepancias)} OC(s) con diferencias entre lo pedido y lo embarcado/recibido.`,
        } as Insight : null as unknown as Insight,
      ].filter(Boolean) as Insight[]} />
      <div className="grid" style={{ marginBottom: 12 }}>
        <Panel title="Lead time de abastecimiento" hint="OC confirmada → recepción validada — bajar es mejor">
          <EChartBox height={280} deps={[k.lead_time_dias]}
            option={gaugeOpt(num(k.lead_time_dias), "LEAD TIME", 240, (v) => `${n0(v)} d`,
              [[0.42, "#059669"], [0.71, "#d97706"], [1, "#dc2626"]])} />
        </Panel>
        <Panel title="Pareto de proveedores" hint="compra del periodo + % acumulado">
          <EChartBox height={280} deps={[suppliers]} option={(() => {
            const rowsS = suppliers.slice(0, 10);
            const total = rowsS.reduce((sm, r) => sm + num(r.mxn ?? r.monto ?? r.venta), 0);
            let acc = 0;
            const val = (r: Rec) => num(r.mxn ?? r.monto ?? r.venta);
            const cum = rowsS.map((r) => { acc += val(r); return total ? Math.round((acc / total) * 1000) / 10 : 0; });
            return {
              ...ecBase(),
              tooltip: { ...(ecBase().tooltip as object), trigger: "axis" },
              legend: { top: 0 },
              xAxis: { ...ecAxis("cat", rowsS.map((r) => String(r.name).slice(0, 14).toUpperCase())), axisLabel: { rotate: 28, fontSize: 9.5, color: ecInk().tick } },
              yAxis: [ecAxis("money"), { type: "value", max: 100, splitLine: { show: false }, axisLabel: { formatter: "{value}%", fontSize: 10, color: ecInk().tick } }],
              series: [
                { name: "Compra", type: "bar", barMaxWidth: 26, data: rowsS.map(val),
                  itemStyle: { borderRadius: [6, 6, 0, 0], color: WF_GRADS.violet } },
                { name: "% acumulado", type: "line", yAxisIndex: 1, smooth: true, symbolSize: 6, data: cum,
                  lineStyle: { width: 3, color: "#d97706" }, itemStyle: { color: "#d97706" } },
              ],
            };
          })()} />
        </Panel>
      </div>

      <div className="stats">
        <Stat label="Compras (MXN a TC actual)" value={money(k.compras_mxn)} sub={`TC usado ${n1(k.tc_usado)}`} />
        <Stat label="Proveedores activos" value={n0(k.proveedores)} />
        <Stat label="Lead time OC → recepción" value={`${n1(k.lead_time_dias)} días`} sub={`${n0(k.lead_time_ocs)} órdenes medidas`} />
        <Stat label="Costo logístico por m²" value={money(k.costo_log_m2_mxn)} sub="flete all-in / m² recibido" />
        <Stat label="Discrepancias PL portal" value={n0(k.discrepancias)} tone={num(k.discrepancias) > 0 ? "mid" : "good"} />
        <Stat label="Edad del pipeline" value={`${n1(k.pipeline_edad_dias)} días`} sub="allocations sin comprar" />
      </div>
      <div className="grid">
        <Panel title="Compras por mes (normalizado a MXN)" wide>
          <ChartBox height={300} deps={[months]} config={{
            type: "bar",
            data: {
              labels: months.map((r) => monthLabel(r.key)),
              datasets: [
                { label: "USD (convertido)", data: months.map((r) => num(r.usd) * num(k.tc_usado)), backgroundColor: "rgba(124,58,237,.75)", borderRadius: 6, maxBarThickness: 34, stack: "s", isMoney: true },
                { label: "MXN directo", data: months.map((r) => num(r.mxn)), backgroundColor: "rgba(11,87,208,.75)", borderRadius: 6, maxBarThickness: 34, stack: "s", isMoney: true },
              ],
            },
            options: { ...baseOptions(), interaction: { mode: "index", intersect: false }, scales: { y: { ...axisMoney(), stacked: true }, x: { ...axisPlain(), stacked: true } } },
          }} />
        </Panel>
        <Panel title="Top proveedores del periodo">
          <ChartBox height={300} deps={[suppliers]} config={{
            type: "bar",
            data: { labels: suppliers.map((r) => String(r.name).slice(0, 30)), datasets: [{ label: "MXN", data: suppliers.map((r) => num(r.mxn)), backgroundColor: "rgba(2,132,199,.8)", borderRadius: 5, maxBarThickness: 18, isMoney: true }] },
            options: { ...baseOptions(), indexAxis: "y", plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { x: axisMoney(), y: axisPlain(10.5) } },
          }} />
        </Panel>
        <Panel title="Pipeline de compra (To Be Purchased)">
          <MiniTable head={["Estado", "Solicitudes", "m²"]} rows={alloc.map((a, i) => ({
            key: i, a: prettyStatus(a.state), b: n0(a.count), c: n1(a.qty),
          }))} />
        </Panel>
        <Panel title="Top materiales comprados en el periodo" hint="normalizado a MXN">
          <ChartBox height={320} deps={[arr(d.top_products)]} config={{
            type: "bar",
            data: { labels: arr(d.top_products).map((r) => String(r.name).slice(0, 34)), datasets: [{ label: "MXN", data: arr(d.top_products).map((r) => num(r.mxn)), backgroundColor: "rgba(11,87,208,.8)", borderRadius: 5, maxBarThickness: 18, isMoney: true }] },
            options: { ...baseOptions(), indexAxis: "y", plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { x: axisMoney(), y: axisPlain(10.5) } },
          }} />
        </Panel>
        <Panel title="OCs abiertas con material pendiente de recibir" hint="el backlog vivo de compras" wide>
          {!arr(d.open_pos).length ? <Empty msg="Todo lo comprado ya se recibió" /> : (
            <div className="tablewrap">
              <table>
                <thead>
                  <tr><th>Orden</th><th>Proveedor</th><th>Confirmada</th><th>Divisa</th><th className="r">Monto</th><th className="r">% recibido</th></tr>
                </thead>
                <tbody>
                  {arr(d.open_pos).map((o) => (
                    <tr key={String(o.id)}>
                      <td className="mono">{String(o.name)}</td>
                      <td className="ell">{String(o.partner)}</td>
                      <td className="mut">{String(o.date)}</td>
                      <td>{String(o.currency)}</td>
                      <td className="r strong">{money(o.total)}</td>
                      <td className="r"><Pill tone={num(o.recibido_pct) >= 75 ? "good" : num(o.recibido_pct) > 0 ? "mid" : "bad"}>{pct(o.recibido_pct)}</Pill></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// TRÁNSITO
// ─────────────────────────────────────────────────────────────────────────────
function TransitoView() {
  const q = useData(["dashboard", "transito", {}], () => fetchDashboard("transito", {}));
  if (q.loading) return <div className="grid"><Skeleton h={90} /><Skeleton /><Skeleton /></div>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const d = q.data!;
  const k = (d.kpis ?? {}) as Rec;
  const st = arr(d.by_status);
  const voyages = arr(d.voyages);
  return (
    <>
      <div className="stats">
        <Stat label="m² en el agua" value={n1(k.total_m2)} sub={`${n0(k.embarques)} embarques activos`} />
        <Stat label="Pre-vendido" value={pct(k.prevendido_pct)} tone={num(k.prevendido_pct) > 50 ? "good" : ""} />
        <Stat label="Pendientes de publicar" value={n0(k.pendientes_publicar)} sub="meta: 0" tone={num(k.pendientes_publicar) > 0 ? "bad" : "good"} />
        <Stat label="Desviación de ETA" value={`${n1(k.eta_desviacion_dias)} días`} sub={`${n0(k.eta_desviados)} viajes desviados`} />
        <Stat label="Días a publicar inventario" value={n1(k.dias_a_publicar)} />
        <Stat label="Ligas de portal" value={n0(k.ligas_portal)} sub={`${n0(k.ligas_sin_acceso_7d)} sin acceso en 7 días`} />
        <Stat label="Avance de captura" value={pct(k.ligas_avance_pct)} sub={`${n0(k.ligas_terminadas)} ligas terminadas`} />
      </div>
      <div className="grid">
        <Panel title="m² por estatus del viaje" wide>
          <EChartBox height={300} deps={[st]} option={{
            ...ecBase(),
            tooltip: { ...(ecBase().tooltip as object), trigger: "axis" },
            legend: { top: 0 },
            xAxis: ecAxis("cat", st.map((r) => String(r.label).toUpperCase())),
            yAxis: [ecAxis("money"), { type: "value", position: "right", splitLine: { show: false },
              axisLabel: { color: "#d97706", fontSize: 10.5 } }],
            series: [
              { name: "m²", type: "bar", barMaxWidth: 46,
                label: { show: true, position: "top", fontSize: 10.5, fontWeight: 700,
                         formatter: (pm: { value: number }) => n1(pm.value) },
                data: st.map((r) => num(r.m2)),
                itemStyle: { borderRadius: [7, 7, 0, 0],
                  color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1,
                    colorStops: [{ offset: 0, color: "#38bdf8" }, { offset: 1, color: "#0b57d0" }] } } },
              { name: "Contenedores", type: "line", yAxisIndex: 1, smooth: true, symbolSize: 7,
                data: st.map((r) => num(r.count)),
                lineStyle: { width: 3, color: "#d97706" }, itemStyle: { color: "#d97706" },
                areaStyle: { opacity: 0.08, color: "#d97706" } },
            ],
          }} />
        </Panel>
        <Panel title="m² en el agua por proveedor" hint="quién trae qué tanto material">
          <ChartBox height={280} deps={[arr(d.by_supplier)]} config={{
            type: "bar",
            data: { labels: arr(d.by_supplier).map((r) => String(r.name).slice(0, 28)), datasets: [{ label: "m²", data: arr(d.by_supplier).map((r) => num(r.m2)), backgroundColor: "rgba(124,58,237,.75)", borderRadius: 5, maxBarThickness: 18 }] },
            options: { ...baseOptions(), indexAxis: "y", plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { x: axisMoney(), y: axisPlain(10.5) } },
          }} />
        </Panel>
        <Panel title="Cuándo llega: m² por mes de ETA" hint="lo que viene en camino">
          <EChartBox height={300} deps={[arr(d.eta_months)]} option={{
            ...ecBase(),
            tooltip: { ...(ecBase().tooltip as object), trigger: "axis" },
            legend: { top: 0 },
            xAxis: ecAxis("cat", arr(d.eta_months).map((r) => monthLabel(r.key).toUpperCase())),
            yAxis: [ecAxis("money"), { type: "value", position: "right", splitLine: { show: false },
              axisLabel: { color: "#d97706", fontSize: 10.5 } }],
            series: [
              { name: "m²", type: "bar", barMaxWidth: 40,
                label: { show: true, position: "top", fontSize: 10.5, fontWeight: 700,
                         formatter: (pm: { value: number }) => n1(pm.value) },
                data: arr(d.eta_months).map((r) => num(r.m2)),
                itemStyle: { borderRadius: [7, 7, 0, 0],
                  color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1,
                    colorStops: [{ offset: 0, color: "#34d399" }, { offset: 1, color: "#059669" }] } } },
              { name: "Contenedores", type: "line", yAxisIndex: 1, smooth: true, symbolSize: 7,
                data: arr(d.eta_months).map((r) => num(r.count)),
                lineStyle: { width: 3, color: "#d97706" }, itemStyle: { color: "#d97706" } },
            ],
          }} />
        </Panel>
        <Panel title="Lo que ya llegó: m² recibidos por mes (12 meses)" wide>
          <ChartBox height={260} deps={[arr(d.arrived_monthly)]} config={{
            type: "bar",
            data: { labels: arr(d.arrived_monthly).map((r) => monthLabel(r.key)), datasets: [{ label: "m²", data: arr(d.arrived_monthly).map((r) => num(r.m2)), backgroundColor: "rgba(5,150,105,.75)", borderRadius: 6, maxBarThickness: 36 }] },
            options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(11) } },
          }} />
        </Panel>
        <Panel title="Línea de tiempo de embarques (ETD → ETA)" hint="gantt · barra = viaje en el agua · rojo = ETA vencida · rueda = zoom" wide>
          {(() => {
            const ts = (v: unknown) => {
              const t = Date.parse(String(v ?? ""));
              return Number.isFinite(t) ? t : null;
            };
            const gv = voyages
              .map((v) => ({ v, t0: ts(v.etd), t1: ts(v.eta) }))
              .filter((g) => g.t0 != null && g.t1 != null && (g.t1 as number) > (g.t0 as number))
              .slice(0, 18) as Array<{ v: Rec; t0: number; t1: number }>;
            if (!gv.length) {
              return <Empty msg="Sin embarques con ETD y ETA capturados (requiere backend v19.0.52.1+ desplegado y viajes con ambas fechas)." />;
            }
            const today = Date.now();
            const names = gv.map((g) => `${String(g.v.name)} · ${String(g.v.supplier).slice(0, 16)}`.toUpperCase());
            return (
              <EChartBox height={Math.max(240, gv.length * 34 + 70)} deps={[voyages]} option={{
                ...ecBase(),
                grid: { left: 8, right: 30, top: 10, bottom: 30, containLabel: true },
                dataZoom: [{ type: "inside", xAxisIndex: 0, filterMode: "weakFilter" }],
                tooltip: { ...(ecBase().tooltip as object),
                  formatter: (pm: { dataIndex: number }) => {
                    const g = gv[pm.dataIndex];
                    return g ? `${String(g.v.name)} · ${String(g.v.supplier)}<br/>${String(g.v.etd)} → ${String(g.v.eta)}<br/><b>${n1(g.v.m2)} m²</b> · ${String(g.v.status)}` : "";
                  } },
                xAxis: { type: "time",
                  axisLabel: { fontSize: 10, fontFamily: "Inter", formatter: (val: number) => {
                    const dd = new Date(val); return `${dd.getDate()}/${MONTHS_ES[dd.getMonth()]}`;
                  } },
                  splitLine: { lineStyle: { color: "rgba(100,116,139,.12)" } } },
                yAxis: { type: "category", data: names, inverse: true,
                  axisLine: { show: false }, axisTick: { show: false },
                  axisLabel: { fontSize: 10, fontFamily: "Inter" } },
                series: [{
                  type: "custom",
                  encode: { x: [0, 1], y: 2 },
                  renderItem: (params: { dataIndex: number }, api: { value: (i: number) => number; coord: (v: [number, number]) => [number, number] }) => {
                    const i = params.dataIndex;
                    const g = gv[i];
                    if (!g) return null as never;
                    const start2 = api.coord([g.t0, i]);
                    const end2 = api.coord([g.t1, i]);
                    const h = 14;
                    const overdue = g.t1 < today && !String(g.v.status).toLowerCase().includes("recep");
                    const fill = overdue
                      ? { type: "linear", x: 0, y: 0, x2: 1, y2: 0, colorStops: [{ offset: 0, color: "#f87171" }, { offset: 1, color: "#dc2626" }] }
                      : { type: "linear", x: 0, y: 0, x2: 1, y2: 0, colorStops: [{ offset: 0, color: "#38bdf8" }, { offset: 1, color: "#0b57d0" }] };
                    return {
                      type: "group",
                      children: [
                        { type: "rect",
                          shape: { x: start2[0], y: start2[1] - h / 2, width: Math.max(end2[0] - start2[0], 3), height: h, r: 7 },
                          style: { fill, shadowBlur: 5, shadowColor: "rgba(15,23,42,.2)", shadowOffsetY: 2 } },
                        { type: "circle",
                          shape: { cx: end2[0], cy: start2[1], r: 4.5 },
                          style: { fill: overdue ? "#dc2626" : "#0b57d0", stroke: "#fff", lineWidth: 1.5 } },
                      ],
                    };
                  },
                  // Datos REALES en el dataset (timestamps + índice): el eje de
                  // tiempo calcula su rango de aquí — con datos vacíos el eje
                  // no tenía extent y el render moría en silencio.
                  data: gv.map((g, i) => [g.t0, g.t1, i]),
                }],
              }} />
            );
          })()}
        </Panel>
        <Panel title="Embarques activos — proveedor, contenedor, ETA y avance de venta" wide>
          {!voyages.length ? <Empty msg="Sin embarques activos" /> : (
            <div className="tablewrap tall">
              <table>
                <thead>
                  <tr><th>Embarque</th><th>Proveedor</th><th>Contenedor</th><th>Estatus</th><th>ETA</th><th className="r">m²</th><th className="r">% vendido</th></tr>
                </thead>
                <tbody>
                  {voyages.map((v) => (
                    <tr key={String(v.id)}>
                      <td className="mono">{String(v.name)}</td>
                      <td className="ell">{String(v.supplier)}</td>
                      <td className="mut">{String(v.container) || "—"}</td>
                      <td>{String(v.status)}</td>
                      <td className="mut">{String(v.eta) || "—"}</td>
                      <td className="r strong">{n1(v.m2)}</td>
                      <td className="r"><Pill tone={num(v.alloc_pct) >= 50 ? "good" : num(v.alloc_pct) > 0 ? "mid" : "bad"}>{pct(v.alloc_pct)}</Pill></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// RECEPCIONES
// ─────────────────────────────────────────────────────────────────────────────
function RecepcionesView(props: { filters: Filters }) {
  const q = useData(["dashboard", "recepciones", props.filters], () => fetchDashboard("recepciones", props.filters as Rec));
  if (q.loading) return <div className="grid"><Skeleton h={90} /><Skeleton /></div>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const d = q.data!;
  const k = (d.kpis ?? {}) as Rec;
  const weeks = arr(d.by_week);
  return (
    <>
      <InsightStrip insights={[
        num(k.lotes_sin_pedimento) > 0 ? {
          metric_id: "inv_m2", severity: "crit",
          text: `${n0(k.lotes_sin_pedimento)} lote(s) SIN pedimento — riesgo aduanal directo.`,
        } as Insight : null as unknown as Insight,
        num(k.faltantes_m2) > 0 || num(k.faltantes_piezas) > 0 ? {
          metric_id: "inv_m2", severity: "warn",
          text: `Faltantes del periodo: ${n1(k.faltantes_m2)} m² y ${n1(k.faltantes_piezas)} piezas detectados en worksheet.`,
        } as Insight : null as unknown as Insight,
      ].filter(Boolean) as Insight[]} />
      <div className="grid" style={{ marginBottom: 12 }}>
        <Panel title="Cobertura de pedimento" hint="obligación aduanal — subir es mejor">
          <EChartBox height={270} deps={[k.pedimento_pct]}
            option={gaugeOpt(num(k.pedimento_pct), "PEDIMENTO", 100, (v) => pct(v),
              [[0.7, "#dc2626"], [0.92, "#d97706"], [1, "#059669"]])} />
        </Panel>
        <Panel title="Exactitud de recepción" hint="entradas sin devolución posterior">
          <EChartBox height={270} deps={[k.exactitud_pct]}
            option={gaugeOpt(num(k.exactitud_pct), "EXACTITUD", 100, (v) => pct(v),
              [[0.85, "#dc2626"], [0.95, "#d97706"], [1, "#059669"]])} />
        </Panel>
      </div>

      <div className="stats">
        <Stat label="m² recibidos" value={n1(k.m2_recibidos)} sub={`${n0(k.fisicas_periodo)} recepciones físicas`} />
        <Stat label="Entradas de compra validadas" value={n0(k.entradas_compra)} />
        <Stat label="Exactitud de recepción" value={pct(k.exactitud_pct)} sub={`${n0(k.con_devolucion)} con devolución posterior`} tone={num(k.exactitud_pct) < 95 ? "mid" : "good"} />
        <Stat label="Lotes con pedimento" value={pct(k.pedimento_pct)} sub={`${n0(k.lotes_sin_pedimento)} sin pedimento`} tone={num(k.pedimento_pct) < 90 ? "mid" : "good"} />
        <Stat label="Etiquetado ZPL en 24h" value={pct(k.etiquetado_24h_pct)} sub={`${n0(k.lotes_periodo)} lotes creados`} />
        <Stat label="Faltantes vs worksheet" value={`${n1(k.faltantes_m2)} m²`} sub={`${n1(k.faltantes_piezas)} piezas`} tone={num(k.faltantes_m2) > 0 ? "bad" : "good"} />
        <Stat label="Bajas por desecho" value={`${n1(k.bajas_scrap)} m²`} tone={num(k.bajas_scrap) > 0 ? "mid" : ""} />
        <Stat label="Puerto → stock" value={`${n1(k.puerto_a_stock_dias)} días`} sub="llegada a puerto vs validación" />
      </div>
      <div className="grid">
        <Panel title="Recepciones por semana — m² y número de recepciones" wide>
          <ChartBox height={320} deps={[weeks]} config={{
            type: "bar",
            data: {
              labels: weeks.map((r) => String(r.week)),
              datasets: [
                { label: "m²", data: weeks.map((r) => num(r.m2)), backgroundColor: "rgba(5,150,105,.75)", borderRadius: 6, maxBarThickness: 34 },
                { type: "line", label: "Recepciones", data: weeks.map((r) => num(r.count)), borderColor: C.blue, borderWidth: 2.5, pointRadius: 3, tension: 0.3, yAxisID: "y1" },
              ],
            },
            options: { ...baseOptions(), interaction: { mode: "index", intersect: false }, scales: { y: axisMoney(), y1: { beginAtZero: true, position: "right", grid: { display: false }, border: { display: false }, ticks: { color: C.blue, font: { size: 11 } } }, x: axisPlain(10.5) } },
          }} />
        </Panel>
        <Panel title="m² recibidos por proveedor (periodo)">
          <ChartBox height={280} deps={[arr(d.by_supplier)]} config={{
            type: "bar",
            data: { labels: arr(d.by_supplier).map((r) => String(r.name).slice(0, 28)), datasets: [{ label: "m²", data: arr(d.by_supplier).map((r) => num(r.m2)), backgroundColor: "rgba(11,87,208,.8)", borderRadius: 5, maxBarThickness: 18 }] },
            options: { ...baseOptions(), indexAxis: "y", plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { x: axisMoney(), y: axisPlain(10.5) } },
          }} />
        </Panel>
        <Panel title="Tendencia anual de recepciones">
          <ChartBox height={280} deps={[arr(d.by_month12)]} config={{
            type: "line",
            data: { labels: arr(d.by_month12).map((r) => monthLabel(r.key)), datasets: [{ label: "m²", data: arr(d.by_month12).map((r) => num(r.m2)), borderColor: C.green, backgroundColor: "rgba(5,150,105,.08)", borderWidth: 2.5, tension: 0.4, fill: true }] },
            options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(10.5) } },
          }} />
        </Panel>
        <Panel title="Últimas recepciones validadas" wide>
          {!arr(d.recent).length ? <Empty msg="Sin recepciones aún" /> : (
            <div className="tablewrap">
              <table>
                <thead><tr><th>Folio</th><th>Proveedor</th><th>Origen</th><th>Fecha</th><th className="r">m²</th></tr></thead>
                <tbody>
                  {arr(d.recent).map((r) => (
                    <tr key={String(r.id)}>
                      <td className="mono">{String(r.name)}</td>
                      <td className="ell">{String(r.partner)}</td>
                      <td className="ell mut">{String(r.origin)}</td>
                      <td className="mut">{String(r.date)}</td>
                      <td className="r strong">{n1(r.m2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// TALLER
// ─────────────────────────────────────────────────────────────────────────────
function TallerView(props: { filters: Filters }) {
  const q = useData(["dashboard", "taller", props.filters], () => fetchDashboard("taller", props.filters as Rec));
  if (q.loading) return <div className="grid"><Skeleton h={90} /><Skeleton /><Skeleton /></div>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const d = q.data!;
  const k = (d.kpis ?? {}) as Rec;
  const states = arr(d.by_state);
  const weekly = arr(d.weekly_done);
  const pasadas = arr(d.pasadas);
  return (
    <>
      <InsightStrip insights={[
        num(k.merma_pct) > 0 ? {
          metric_id: "m2_mes", severity: num(k.merma_pct) > 8 ? "crit" : num(k.merma_pct) > 4 ? "warn" : "info",
          text: `Merma del periodo: ${pct(k.merma_pct)} (${n1(k.merma_m2)} m² perdidos de ${n1(k.area_in_m2)} m² procesados).`,
        } as Insight : null as unknown as Insight,
        num(k.backlog_dias) > 10 ? {
          metric_id: "m2_mes", severity: "warn",
          text: `Backlog promedio de ${n1(k.backlog_dias)} días en órdenes abiertas.`,
        } as Insight : null as unknown as Insight,
      ].filter(Boolean) as Insight[]} />
      <div className="grid" style={{ marginBottom: 12 }}>
        <Panel title="Merma del taller" hint="% del área procesada — bajar es mejor">
          <EChartBox height={270} deps={[k.merma_pct]}
            option={gaugeOpt(num(k.merma_pct), "MERMA", 15, (v) => pct(v),
              [[0.27, "#059669"], [0.53, "#d97706"], [1, "#dc2626"]])} />
        </Panel>
        <Panel title="Del área que entra a la que sale" hint="waterfall · entrada → merma → salida útil">
          <EChartBox height={270} deps={[k.area_in_m2, k.merma_m2, k.area_out_m2]} option={(() => {
            const ain = num(k.area_in_m2);
            const mer = num(k.merma_m2);
            const aout = num(k.area_out_m2);
            const opt = waterfallOpt([
              { label: "ENTRA", value: ain, base: 0, color: "blue" },
              { label: "− MERMA", value: mer, base: Math.max(ain - mer, 0), color: "red" },
              { label: "SALE ÚTIL", value: aout, base: 0, color: "green" },
            ]);
            (opt.tooltip as Rec).formatter = (pm: { dataIndex: number }) => {
              const labels = ["ENTRA", "− MERMA", "SALE ÚTIL"]; const vals = [ain, mer, aout];
              return `${labels[pm.dataIndex]}<br/><b>${n1(vals[pm.dataIndex])} m²</b>`;
            };
            ((opt.series as Rec[])[1] as Rec).label = { show: true, position: "top", fontWeight: 800, fontSize: 11.5,
              formatter: (pm: { dataIndex: number }) => `${n1([ain, mer, aout][pm.dataIndex])} m²` };
            (opt.yAxis as Rec).axisLabel = { fontSize: 10, formatter: (v: number) => n0(v) };
            return opt;
          })()} />
        </Panel>
      </div>

      <div className="stats">
        <Stat label="OTs en taller" value={n0(k.en_taller)} sub={`${n1(k.backlog_dias)} días promedio en proceso`} tone={num(k.backlog_dias) > 14 ? "mid" : ""} />
        <Stat label="Terminadas en el periodo" value={n0(k.terminadas)} sub={`Lead time ${n1(k.lead_time_dias)} días`} />
        <Stat label="m² procesados" value={n1(k.area_in_m2)} sub={`Salieron ${n1(k.area_out_m2)} m²`} />
        <Stat label="Merma de proceso" value={`${n1(k.merma_m2)} m²`} sub={pct(k.merma_pct)} tone={num(k.merma_pct) > 8 ? "bad" : num(k.merma_pct) > 4 ? "mid" : "good"} />
        <Stat label="Reclasificaciones" value={n0(k.reclasificaciones)} />
      </div>
      <div className="grid">
        <Panel title="Órdenes terminadas por semana" wide>
          <ChartBox height={280} deps={[weekly]} config={{
            type: "bar",
            data: { labels: weekly.map((r) => String(r.week)), datasets: [{ label: "OTs", data: weekly.map((r) => num(r.count)), backgroundColor: "rgba(11,87,208,.8)", borderRadius: 6, maxBarThickness: 34 }] },
            options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(10.5) } },
          }} />
        </Panel>
        <Panel title="OTs por estado">
          <MiniTable head={["Estado", "", "Órdenes"]} rows={states.map((s, i) => ({
            key: i, a: String(s.state), b: "", c: n0(s.count),
          }))} />
        </Panel>
        <Panel title="Repetición de proceso (pasadas por lote)" hint="folios -R2, -R3, -R4+">
          <MiniTable head={["Pasada", "", "Lotes"]} rows={pasadas.map((p, i) => ({
            key: i, a: String(p.label), b: "", c: n0(p.count),
          }))} />
        </Panel>
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// ENTREGAS
// ─────────────────────────────────────────────────────────────────────────────
function EntregasView(props: { filters: Filters }) {
  const q = useData(["dashboard", "entregas", props.filters], () => fetchDashboard("entregas", props.filters as Rec));
  if (q.loading) return <div className="grid"><Skeleton h={90} /><Skeleton /><Skeleton /></div>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const d = q.data!;
  if (d.unavailable) return <Empty msg="El módulo de entregas no está instalado en este servidor" />;
  const k = (d.kpis ?? {}) as Rec;
  const st = arr(d.by_status);
  const returns = arr(d.returns);
  const auth = arr(d.auth_sin_pago);
  return (
    <>
      <InsightStrip insights={[
        num(k.credito_informal_mxn) > 0 ? {
          metric_id: "por_cobrar", severity: "crit",
          text: `${money(k.credito_informal_mxn)} de material entregado con autorización manual SIN pago completo (crédito informal).`,
        } as Insight : null as unknown as Insight,
        num(k.ocupacion_pct) > 0 && num(k.ocupacion_pct) < 60 ? {
          metric_id: "m2_mes", severity: "warn",
          text: `Ocupación vehicular promedio de ${pct(k.ocupacion_pct)}: hay viajes saliendo a media capacidad.`,
        } as Insight : null as unknown as Insight,
      ].filter(Boolean) as Insight[]} />
      <div className="grid" style={{ marginBottom: 12 }}>
        <Panel title="Ocupación vehicular" hint="m² cargados vs capacidad — subir es mejor">
          <EChartBox height={270} deps={[k.ocupacion_pct]}
            option={gaugeOpt(num(k.ocupacion_pct), "OCUPACIÓN", 100, (v) => pct(v),
              [[0.5, "#dc2626"], [0.75, "#d97706"], [1, "#059669"]])} />
        </Panel>
        <Panel title="Cobrado al entregar" hint="% del total pagado al firmar — subir es mejor">
          <EChartBox height={270} deps={[k.cobrado_al_entregar_pct]}
            option={gaugeOpt(num(k.cobrado_al_entregar_pct), "COBRADO AL FIRMAR", 100, (v) => pct(v),
              [[0.6, "#dc2626"], [0.85, "#d97706"], [1, "#059669"]])} />
        </Panel>
      </div>

      <div className="stats">
        <Stat label="En ruta ahora" value={n0(k.en_ruta)} />
        <Stat label="Firmadas en app" value={n0(k.firmadas_app)} sub={`${n0(k.manuales)} marcadas manualmente`} />
        <Stat label="Ciclo pedido → entrega" value={`${n1(k.ciclo_dias)} días`} sub={`${n0(k.ciclo_muestras)} entregas medidas`} />
        <Stat label="Devoluciones" value={n0(k.devoluciones)} tone={num(k.devoluciones) > 0 ? "mid" : "good"} />
        <Stat label="Crédito informal" value={money(k.credito_informal_mxn)} sub="entregado sin pago completo" tone={num(k.credito_informal_mxn) > 0 ? "bad" : "good"} />
        <Stat label="Cobrado al entregar" value={pct(k.cobrado_al_entregar_pct)} />
        <Stat label="Ocupación de vehículo" value={pct(k.ocupacion_pct)} />
        <Stat label="Paradas GPS registradas" value={n0(k.paradas_gps)} />
      </div>
      <div className="grid">
        <Panel title="Remisiones por estatus">
          <ChartBox height={280} deps={[st]} config={{
            type: "bar",
            data: { labels: st.map((r) => prettyStatus(r.status)), datasets: [{ label: "Remisiones", data: st.map((r) => num(r.count)), backgroundColor: "rgba(11,87,208,.8)", borderRadius: 6, maxBarThickness: 44 }] },
            options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(11) } },
          }} />
        </Panel>
        <Panel title="Devoluciones por motivo">
          {!returns.length ? <Empty msg="Sin devoluciones en el periodo" /> : (
            <ChartBox height={280} deps={[returns]} config={{
              type: "bar",
              data: { labels: returns.map((r) => String(r.reason).slice(0, 28)), datasets: [{ label: "Devoluciones", data: returns.map((r) => num(r.count)), backgroundColor: "rgba(220,38,38,.75)", borderRadius: 5, maxBarThickness: 18 }] },
              options: { ...baseOptions(), indexAxis: "y", plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { x: axisMoney(), y: axisPlain(10.5) } },
            }} />
          )}
        </Panel>
        <Panel title="Entregas autorizadas sin pago completo (crédito informal vivo)" wide>
          {!auth.length ? <Empty msg="Nada entregado sin pagar — sano" /> : (
            <div className="tablewrap">
              <table>
                <thead>
                  <tr><th>Orden</th><th>Cliente</th><th>Autorizó</th><th>Fecha</th><th className="r">Saldo pendiente</th></tr>
                </thead>
                <tbody>
                  {auth.map((a, i) => (
                    <tr key={i}>
                      <td className="mono">{String(a.order)}</td>
                      <td className="ell">{String(a.partner)}</td>
                      <td className="ell mut">{String(a.approver)}</td>
                      <td className="mut">{String(a.date)}</td>
                      <td className="r neg">{money(a.residual)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// FINANZAS
// ─────────────────────────────────────────────────────────────────────────────
function FinanzasView(props: { filters: Filters; drill: (n: DrillNode) => void }) {
  const banks = useData(["banks"], fetchBanks);
  const fin = useData(["dashboard", "financiero", props.filters], () => fetchDashboard("financiero", props.filters as Rec));
  if (fin.loading) return <div className="grid"><Skeleton h={90} /><Skeleton /><Skeleton /></div>;
  if (fin.error) return <ErrorBox msg={fin.error} retry={fin.retry} />;
  const d = fin.data!;
  const k = (d.kpis ?? {}) as Rec;
  const arb = arr(d.ar_buckets);
  const apb = arr(d.ap_buckets);
  const bm = arr(d.by_month);
  const cash = arr(d.cash_month);
  const pago = arr(d.pago_post_entrega);
  const arCur = arr(d.ar_currency);
  const apCur = arr(d.ap_currency);
  const due = arr(d.due_flow);
  const arTop = arr(d.ar_top);
  const apTop = arr(d.ap_top);

  const mom = num(k.facturado_mom_pct);

  const finInsights: Insight[] = [];
  if (num(k.vencido_pct) > 25) {
    finInsights.push({ metric_id: "por_cobrar", severity: "crit",
      text: `La cartera vencida es ${pct(k.vencido_pct)} del total por cobrar (${money(k.vencido_mxn)}): prioridad de cobranza.` });
  } else if (num(k.vencido_mxn) > 0) {
    finInsights.push({ metric_id: "por_cobrar", severity: "warn",
      text: `${money(k.vencido_mxn)} vencidos (${pct(k.vencido_pct)} de la cartera).` });
  }
  if (isFinite(mom) && mom !== 0) {
    finInsights.push({ metric_id: "fact_real_mes", severity: mom < 0 ? "warn" : "info",
      text: `La facturación del mes va ${mom >= 0 ? "▲" : "▼"} ${pct(Math.abs(mom))} contra el mes anterior (${money(k.facturado_mes)} vs ${money(k.facturado_mes_prev)}).` });
  }
  if (num(k.dso_dias) > 60) {
    finInsights.push({ metric_id: "por_cobrar", severity: "warn",
      text: `DSO de ${n1(k.dso_dias)} días: la venta tarda más de 2 meses en volverse efectivo.` });
  }
  if (num(k.efectivo_sin_aplicar) > 0) {
    finInsights.push({ metric_id: "bancos_mxn", severity: "info",
      text: `${money(k.efectivo_sin_aplicar)} de efectivo recibido sin aplicar contablemente (${n0(k.recibos_sin_aplicar)} recibos).` });
  }

  return (
    <>
      <div className="bento">
        <div className={"tv-stat hero " + (num(k.neto) >= 0 ? "good" : "bad")}>
          <div className="l">Posición neta</div>
          <div className="v">{money(k.neto)}</div>
          <div className="s">Me deben {money(k.por_cobrar)} · Debo {money(k.por_pagar)} — MXN al TC de registro</div>
        </div>
        <Stat label="ME DEBEN" value={money(k.por_cobrar)} sub={`${n0(k.clientes_deudores)} clientes`} tone="good" />
        <Stat label="DEBO" value={money(k.por_pagar)} tone="bad" />
        <Stat label="Cartera vencida" value={money(k.vencido_mxn)} sub={`${pct(k.vencido_pct)} del total`} tone={num(k.vencido_pct) > 30 ? "bad" : num(k.vencido_pct) > 10 ? "mid" : "good"} />
        <Stat label="Días de cobro (DSO)" value={`${n0(k.dso_dias)} días`} tone={num(k.dso_dias) > 60 ? "bad" : num(k.dso_dias) > 30 ? "mid" : "good"} />
        <Stat label="Facturado este mes" value={money(k.facturado_mes)} sub={`${mom >= 0 ? "▲" : "▼"} ${pct(Math.abs(mom))} vs mes anterior`} tone={mom >= 0 ? "good" : "bad"} />
        <Stat label="Bancos y cajas" value={banks.data ? money(banks.data.total) : "…"} tone="good" />
        <Stat label="Efectivo sin aplicar" value={money(k.efectivo_sin_aplicar)} sub={`${n0(k.recibos_sin_aplicar)} recibos · aplicado ${money(k.efectivo_aplicado)}`} tone={num(k.efectivo_sin_aplicar) > 0 ? "mid" : ""} />
        <Stat label="Comprobantes por validar" value={n0(k.comprobantes_pendientes)} sub={money(k.comprobantes_monto)} tone={num(k.comprobantes_pendientes) > 0 ? "mid" : "good"} />
      </div>

      <div style={{ marginTop: 12 }}><InsightStrip insights={finInsights} /></div>

      <div className="grid">
        {banks.data && (
          <Panel title="Puente de liquidez: de bancos a posición total" hint="waterfall · liquidez + exigible − obligaciones" wide>
            <EChartBox height={320} deps={[banks.data.total, k.por_cobrar, k.por_pagar]} option={(() => {
              const b = num(banks.data!.total);
              const ar2 = num(k.por_cobrar);
              const ap2 = num(k.por_pagar);
              const pos = b + ar2 - ap2;
              const cats = ["BANCOS Y CAJAS", "+ POR COBRAR", "− POR PAGAR", "POSICIÓN TOTAL"];
              const base = [0, b, b + ar2 - ap2, 0];
              const vals = [b, ar2, ap2, pos];
              const colors = [
                { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "#60a5fa" }, { offset: 1, color: "#0b57d0" }] },
                { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "#34d399" }, { offset: 1, color: "#059669" }] },
                { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "#f87171" }, { offset: 1, color: "#dc2626" }] },
                { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "#a78bfa" }, { offset: 1, color: "#6d28d9" }] },
              ];
              return {
                ...ecBase(),
                tooltip: { ...(ecBase().tooltip as object),
                  formatter: (pm: { dataIndex: number }) => `${cats[pm.dataIndex]}<br/><b>${money(vals[pm.dataIndex])}</b>` },
                xAxis: ecAxis("cat", cats),
                yAxis: ecAxis("money"),
                series: [
                  { type: "bar", stack: "w", barMaxWidth: 72, silent: true,
                    itemStyle: { color: "transparent" }, data: base, tooltip: { show: false } },
                  { type: "bar", stack: "w", barMaxWidth: 72,
                    label: { show: true, position: "top", fontWeight: 800, fontSize: 12,
                             formatter: (pm: { dataIndex: number }) => money(vals[pm.dataIndex]) },
                    data: vals.map((v, i) => ({ value: v, itemStyle: { color: colors[i], borderRadius: [7, 7, 0, 0] } })) },
                ],
              };
            })()} />
          </Panel>
        )}
        <Panel title="Me deben — por divisa original" hint="el MXN es al TC del día de registro, no al de hoy">
          {!arCur.length ? <Empty msg="Sin cartera abierta" /> : (
            <div className="tablewrap">
              <table>
                <thead><tr><th>Divisa</th><th className="r">Saldo en divisa</th><th className="r">Equivale MXN</th><th className="r">Facturas</th></tr></thead>
                <tbody>
                  {arCur.map((c, i) => (
                    <tr key={i}>
                      <td className="strong">{String(c.currency)}</td>
                      <td className="r">{money(c.monto_divisa)}</td>
                      <td className="r strong">{money(c.monto_mxn)}</td>
                      <td className="r mut">{n0(c.facturas)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <Panel title="Debo — por divisa original" hint="mismo criterio: TC del registro">
          {!apCur.length ? <Empty msg="Sin deudas abiertas" /> : (
            <div className="tablewrap">
              <table>
                <thead><tr><th>Divisa</th><th className="r">Saldo en divisa</th><th className="r">Equivale MXN</th><th className="r">Facturas</th></tr></thead>
                <tbody>
                  {apCur.map((c, i) => (
                    <tr key={i}>
                      <td className="strong">{String(c.currency)}</td>
                      <td className="r">{money(c.monto_divisa)}</td>
                      <td className="r strong">{money(c.monto_mxn)}</td>
                      <td className="r mut">{n0(c.facturas)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel title="Salud de cobranza" hint="DSO y % vencido — bajar es mejor">
          <EChartBox height={300} deps={[k.dso_dias, k.vencido_pct]} option={{
            ...ecBase(),
            series: [
              { type: "gauge", center: ["27%", "56%"], radius: "82%", startAngle: 210, endAngle: -30,
                min: 0, max: 120, splitNumber: 4,
                axisLine: { lineStyle: { width: 14, color: [[0.375, "#059669"], [0.625, "#d97706"], [1, "#dc2626"]] } },
                pointer: { itemStyle: { color: "auto" }, width: 4 },
                axisTick: { show: false }, splitLine: { show: false },
                axisLabel: { fontSize: 9, distance: 18 },
                title: { offsetCenter: [0, "72%"], fontSize: 11, fontWeight: 700 },
                detail: { fontSize: 22, fontWeight: 800, offsetCenter: [0, "38%"], color: "auto",
                          formatter: (v: number) => `${n1(v)} d` },
                data: [{ value: num(k.dso_dias), name: "DSO" }] },
              { type: "gauge", center: ["73%", "56%"], radius: "82%", startAngle: 210, endAngle: -30,
                min: 0, max: 100, splitNumber: 4,
                axisLine: { lineStyle: { width: 14, color: [[0.1, "#059669"], [0.3, "#d97706"], [1, "#dc2626"]] } },
                pointer: { itemStyle: { color: "auto" }, width: 4 },
                axisTick: { show: false }, splitLine: { show: false },
                axisLabel: { fontSize: 9, distance: 18 },
                title: { offsetCenter: [0, "72%"], fontSize: 11, fontWeight: 700 },
                detail: { fontSize: 22, fontWeight: 800, offsetCenter: [0, "38%"], color: "auto",
                          formatter: (v: number) => pct(v) },
                data: [{ value: num(k.vencido_pct), name: "VENCIDO" }] },
            ],
          }} />
        </Panel>
        <Panel title="Flujo por vencimiento: entra vs sale" hint="verde arriba = cobros · rojo abajo = pagos">
          <EChartBox height={300} deps={[due]} option={{
            ...ecBase(),
            tooltip: { ...(ecBase().tooltip as object), trigger: "axis",
              formatter: (params: Array<{ marker: string; seriesName: string; value: number; name: string }>) => {
                const list = Array.isArray(params) ? params : [params];
                return `${list[0]?.name}<br/>` + list.map((pp) =>
                  `${pp.marker} ${pp.seriesName}: <b>${money(Math.abs(num(pp.value)))}</b>`).join("<br/>");
              } },
            legend: { top: 0 },
            xAxis: ecAxis("cat", due.map((r) => String(r.bucket).toUpperCase())),
            yAxis: { ...ecAxis("money"), axisLabel: { fontSize: 10,
              formatter: (v: number) => new Intl.NumberFormat("en-US", { notation: "compact" }).format(Math.abs(v)) } },
            series: [
              { name: "Entra (por cobrar)", type: "bar", stack: "flow", barMaxWidth: 46,
                data: due.map((r) => num(r.entra)),
                itemStyle: { borderRadius: [7, 7, 0, 0],
                  color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1,
                    colorStops: [{ offset: 0, color: "#34d399" }, { offset: 1, color: "#059669" }] } } },
              { name: "Sale (por pagar)", type: "bar", stack: "flow", barMaxWidth: 46,
                data: due.map((r) => -num(r.sale)),
                itemStyle: { borderRadius: [0, 0, 7, 7],
                  color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1,
                    colorStops: [{ offset: 0, color: "#f87171" }, { offset: 1, color: "#dc2626" }] } } },
            ],
          }} />
        </Panel>

        <Panel title="Tornado de antigüedad: cobrar ↔ pagar" hint="izquierda = me deben · derecha = debo · por edad" wide>
          <EChartBox height={300} deps={[arb, apb]} option={(() => {
            const buckets = [...new Set([...arb.map((r) => String(r.bucket)), ...apb.map((r) => String(r.bucket))])];
            const arMap = new Map(arb.map((r) => [String(r.bucket), num(r.monto)]));
            const apMap = new Map(apb.map((r) => [String(r.bucket), num(r.monto)]));
            return {
              ...ecBase(),
              tooltip: { ...(ecBase().tooltip as object), trigger: "axis",
                formatter: (params: Array<{ marker: string; seriesName: string; value: number; name: string }>) => {
                  const list = Array.isArray(params) ? params : [params];
                  return `${list[0]?.name}<br/>` + list.map((pp) =>
                    `${pp.marker} ${pp.seriesName}: <b>${money(Math.abs(num(pp.value)))}</b>`).join("<br/>");
                } },
              legend: { top: 0 },
              xAxis: { ...ecAxis("money"), axisLabel: { fontSize: 10,
                formatter: (v: number) => new Intl.NumberFormat("en-US", { notation: "compact" }).format(Math.abs(v)) } },
              yAxis: { type: "category", data: buckets.map((b) => b.toUpperCase()), inverse: true,
                axisLine: { show: false }, axisTick: { show: false },
                axisLabel: { fontSize: 11, fontFamily: "Inter", fontWeight: 700 } },
              series: [
                { name: "Me deben", type: "bar", stack: "t", barMaxWidth: 26,
                  label: { show: true, position: "left", fontSize: 10,
                           formatter: (pm: { value: number }) => money(Math.abs(num(pm.value))) },
                  data: buckets.map((b) => -(arMap.get(b) ?? 0)),
                  itemStyle: { borderRadius: [7, 0, 0, 7],
                    color: { type: "linear", x: 1, y: 0, x2: 0, y2: 0,
                      colorStops: [{ offset: 0, color: "#34d399" }, { offset: 1, color: "#059669" }] } } },
                { name: "Debo", type: "bar", stack: "t", barMaxWidth: 26,
                  label: { show: true, position: "right", fontSize: 10,
                           formatter: (pm: { value: number }) => money(num(pm.value)) },
                  data: buckets.map((b) => apMap.get(b) ?? 0),
                  itemStyle: { borderRadius: [0, 7, 7, 0],
                    color: { type: "linear", x: 0, y: 0, x2: 1, y2: 0,
                      colorStops: [{ offset: 0, color: "#f87171" }, { offset: 1, color: "#dc2626" }] } } },
              ],
            };
          })()} />
        </Panel>

        <Panel title="Pareto de deudores" hint="barras = saldo · línea = % acumulado · click = factura por factura" wide>
          <EChartBox height={320} deps={[arTop]}
            onClick={(pm) => { const c2 = arTop[pm.dataIndex]; if (c2) props.drill({ kind: "finpartner", side: "ar", partnerId: num(c2.key), label: String(c2.name) }); }}
            option={(() => {
              const total = arTop.reduce((sm, c2) => sm + num(c2.monto), 0);
              let acc = 0;
              const cum = arTop.map((c2) => { acc += num(c2.monto); return total ? Math.round((acc / total) * 1000) / 10 : 0; });
              return {
                ...ecBase(),
                tooltip: { ...(ecBase().tooltip as object), trigger: "axis" },
                legend: { top: 0 },
                xAxis: { ...ecAxis("cat", arTop.map((c2) => String(c2.name).slice(0, 18).toUpperCase())),
                  axisLabel: { rotate: 28, fontSize: 9.5, color: ecInk().tick } },
                yAxis: [ecAxis("money"), { type: "value", max: 100, splitLine: { show: false },
                  axisLabel: { formatter: "{value}%", fontSize: 10, color: ecInk().tick } }],
                series: [
                  { name: "Saldo", type: "bar", barMaxWidth: 28,
                    data: arTop.map((c2) => num(c2.monto)),
                    itemStyle: { borderRadius: [6, 6, 0, 0],
                      color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1,
                        colorStops: [{ offset: 0, color: "#38bdf8" }, { offset: 1, color: "#0b57d0" }] } } },
                  { name: "% acumulado", type: "line", yAxisIndex: 1, smooth: true, symbolSize: 6,
                    data: cum, lineStyle: { width: 3, color: "#d97706" }, itemStyle: { color: "#d97706" } },
                ],
              };
            })()} />
        </Panel>
        <Panel title="Quién me debe" hint="click en un cliente = factura por factura">
          {!arTop.length ? <Empty msg="Nadie me debe" /> : (
            <div className="tablewrap">
              <table>
                <thead><tr><th>Cliente</th><th className="r">Saldo</th><th className="r">Facturas</th><th>Vence desde</th></tr></thead>
                <tbody>
                  {arTop.map((c) => (
                    <tr key={String(c.key)} className="click" onClick={() => props.drill({ kind: "finpartner", side: "ar", partnerId: num(c.key), label: String(c.name) })}>
                      <td className="ell">{String(c.name)}</td>
                      <td className="r strong">{money(c.monto)}</td>
                      <td className="r mut">{n0(c.facturas)}</td>
                      <td className="mut">{String(c.oldest ?? "")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <Panel title="A quién le debo" hint="click en un proveedor = factura por factura">
          {!apTop.length ? <Empty msg="No debo nada" /> : (
            <div className="tablewrap">
              <table>
                <thead><tr><th>Proveedor</th><th className="r">Saldo</th><th className="r">Facturas</th><th>Vence desde</th></tr></thead>
                <tbody>
                  {apTop.map((pv) => (
                    <tr key={String(pv.key)} className="click" onClick={() => props.drill({ kind: "finpartner", side: "ap", partnerId: num(pv.key), label: String(pv.name) })}>
                      <td className="ell">{String(pv.name)}</td>
                      <td className="r strong">{money(pv.monto)}</td>
                      <td className="r mut">{n0(pv.facturas)}</td>
                      <td className="mut">{String(pv.oldest ?? "")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        {pago.length > 0 && (
          <Panel title="Días de entrega a pago por cliente (beeswarm)" hint="cada punto = un cliente · tamaño = entregas · verde <15d, ámbar <30d, rojo 30d+" wide>
            <EChartBox height={300} deps={[pago]} option={(() => {
              const maxE = Math.max(...pago.map((r) => num(r.entregas)), 1);
              return {
                ...ecBase(),
                grid: { left: 8, right: 24, top: 16, bottom: 8, containLabel: true },
                tooltip: { ...(ecBase().tooltip as object),
                  formatter: (pm: { dataIndex: number }) => {
                    const r = pago[pm.dataIndex];
                    return r ? `${String(r.name)}<br/><b>${n1(r.dias)} días</b> promedio · ${n0(r.entregas)} entregas` : "";
                  } },
                xAxis: { type: "value",
                  splitLine: { lineStyle: { color: "rgba(100,116,139,.12)" } },
                  axisLabel: { fontSize: 10, fontFamily: "Inter", formatter: (v: number) => `${n0(v)} d` } },
                yAxis: { show: false, min: -1, max: 1, type: "value" },
                series: [{
                  type: "scatter",
                  data: pago.map((r, i) => [num(r.dias), ((i * 41) % 19 - 9) / 11]),
                  symbolSize: (val: [number, number], pm: { dataIndex: number }) =>
                    10 + Math.sqrt(num(pago[pm.dataIndex]?.entregas) / maxE) * 22,
                  itemStyle: {
                    color: (pm: { dataIndex: number }) => {
                      const dd = num(pago[pm.dataIndex]?.dias);
                      const c2 = dd < 15 ? ["#34d399", "#059669"] : dd < 30 ? ["#fbbf24", "#d97706"] : ["#f87171", "#dc2626"];
                      return { type: "radial", x: 0.4, y: 0.4, r: 1,
                        colorStops: [{ offset: 0, color: c2[0] }, { offset: 1, color: c2[1] }] };
                    },
                    opacity: 0.88, shadowBlur: 6, shadowColor: "rgba(15,23,42,.25)",
                  },
                  labelLayout: { hideOverlap: true },
                  label: { show: true, position: "top", fontSize: 9, color: ecInk().tick,
                           formatter: (pm: { dataIndex: number }) => String(pago[pm.dataIndex]?.name ?? "").slice(0, 14) },
                }],
              };
            })()} />
          </Panel>
        )}
        <Panel title="Facturado vs comprado (12 meses)" wide>
          <ChartBox height={280} deps={[bm]} config={{
            type: "line",
            data: {
              labels: bm.map((r) => monthLabel(r.key)),
              datasets: [
                { label: "Facturado a clientes", data: bm.map((r) => num(r.facturado)), borderColor: C.green, backgroundColor: "rgba(5,150,105,.08)", borderWidth: 2.5, tension: 0.4, fill: true, isMoney: true },
                { label: "Comprado a proveedores", data: bm.map((r) => num(r.comprado)), borderColor: C.red, backgroundColor: "rgba(220,38,38,.06)", borderWidth: 2.5, tension: 0.4, fill: true, isMoney: true },
              ],
            },
            options: { ...baseOptions(), interaction: { mode: "index", intersect: false }, scales: { y: axisMoney(), x: axisPlain() } },
          }} />
        </Panel>
        {cash.length > 0 && (
          <Panel title="Caja manual: entradas vs salidas por mes" wide>
            <ChartBox height={260} deps={[cash]} config={{
              type: "bar",
              data: {
                labels: cash.map((r) => monthLabel(r.key)),
                datasets: [
                  { label: "Entradas", data: cash.map((r) => num(r.entradas)), backgroundColor: "rgba(5,150,105,.75)", borderRadius: 6, maxBarThickness: 30, isMoney: true },
                  { label: "Salidas", data: cash.map((r) => num(r.salidas)), backgroundColor: "rgba(220,38,38,.7)", borderRadius: 6, maxBarThickness: 30, isMoney: true },
                ],
              },
              options: { ...baseOptions(), interaction: { mode: "index", intersect: false }, scales: { y: axisMoney(), x: axisPlain() } },
            }} />
          </Panel>
        )}
        {!banks.loading && !banks.error && banks.data && banks.data.journals.length > 0 && (
          <Panel title="Dónde está el dinero" hint="balance contable por diario, incluye pagos sin conciliar" wide>
            <div className="stats" style={{ marginBottom: 0 }}>
              {banks.data.journals.map((j) => (
                <Stat key={String(j.id)} label={String(j.name)} value={money(j.balance)} sub={j.type === "cash" ? "caja" : "banco"} />
              ))}
            </div>
          </Panel>
        )}
        {pago.length > 0 && (
          <Panel title="Días de pago después de la entrega (los más lentos)" wide>
            <MiniTable head={["Cliente", "Entregas", "Días promedio"]} rows={pago.map((pp, i) => ({
              key: i, a: String(pp.name), b: n0(pp.entregas), c: `${n1(pp.dias)} días`,
            }))} />
          </Panel>
        )}
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// PRONÓSTICOS — proyecciones honestas sobre la historia disponible
// ─────────────────────────────────────────────────────────────────────────────
function PronosticosView() {
  const q = useData(["dashboard", "pronosticos", {}], () => fetchDashboard("pronosticos", {}));
  if (q.loading) return <div className="grid"><Skeleton h={90} /><Skeleton /><Skeleton /></div>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const d = q.data!;
  const k = (d.kpis ?? {}) as Rec;
  const hist = arr(d.forecast);
  const proy = arr(d.proyeccion);
  const cob = arr(d.cobertura);
  const labels = [...hist.map((r) => monthLabel(r.key)), ...proy.map((r) => monthLabel(r.key))];
  const nulls = (n: number) => Array.from({ length: n }, () => null);
  return (
    <>
      <InsightStrip insights={[
        num(k.flujo_90d) !== 0 ? {
          metric_id: "bancos_mxn", severity: num(k.flujo_90d) < 0 ? "crit" : "info",
          text: `Flujo proyectado a 90 días: ${money(k.flujo_90d)} (entra ${money(k.entra_90d)} · sale ${money(k.sale_90d)}).`,
        } as Insight : null as unknown as Insight,
        num(k.tendencia_pct) !== 0 ? {
          metric_id: "venta_mes", severity: num(k.tendencia_pct) < 0 ? "warn" : "info",
          text: `Tendencia de venta ${num(k.tendencia_pct) >= 0 ? "▲" : "▼"} ${pct(Math.abs(num(k.tendencia_pct)))} sobre ${n0(k.meses_historia)} meses de historia.`,
        } as Insight : null as unknown as Insight,
      ].filter(Boolean) as Insight[]} />
      <div className="grid" style={{ marginBottom: 12 }}>
        <Panel title="Caja proyectada a 90 días" hint="waterfall · entra → sale → flujo neto" wide>
          <EChartBox height={280} deps={[k.entra_90d, k.sale_90d]} option={waterfallOpt([
            { label: "ENTRA (COBROS)", value: num(k.entra_90d), base: 0, color: "green" },
            { label: "− SALE (PAGOS)", value: num(k.sale_90d), base: Math.max(num(k.entra_90d) - num(k.sale_90d), 0), color: "red" },
            { label: "FLUJO NETO", value: num(k.flujo_90d), base: 0, color: num(k.flujo_90d) >= 0 ? "blue" : "red" },
          ])} />
        </Panel>
      </div>

      <div className="stats">
        <Stat label="Venta esperada próximo mes" value={money(k.venta_proximo_mes)} sub="tendencia lineal sobre 12 meses" />
        <Stat label="Venta esperada 3 meses" value={money(k.venta_3m)} />
        <Stat label="Tendencia de venta" value={`${num(k.tendencia_pct) >= 0 ? "▲" : "▼"} ${pct(Math.abs(num(k.tendencia_pct)))}`} sub="primeros 3 meses vs últimos 3" tone={num(k.tendencia_pct) >= 0 ? "good" : "bad"} />
        <Stat label="Entra en 90 días" value={money(k.entra_90d)} sub="cobros por vencer" tone="good" />
        <Stat label="Sale en 90 días" value={money(k.sale_90d)} sub="pagos por vencer" tone="bad" />
        <Stat label="Flujo neto esperado 90 días" value={money(k.flujo_90d)} tone={num(k.flujo_90d) >= 0 ? "good" : "bad"} />
        <Stat label="Historia disponible" value={`${n0(k.meses_historia)} meses`} sub="la proyección afina sola con más historia" />
      </div>
      <div className="grid">
        <Panel title="Venta: historia y proyección a 3 meses" hint="banda = ±1 desviación de los residuos de la tendencia" wide>
          <ChartBox height={340} deps={[hist, proy]} config={{
            type: "line",
            data: {
              labels,
              datasets: [
                { label: "Venta real", data: [...hist.map((r) => num(r.real)), ...nulls(proy.length)], borderColor: C.blue, backgroundColor: "rgba(11,87,208,.08)", borderWidth: 2.5, tension: 0.35, fill: true, isMoney: true },
                { label: "Proyección", data: [...nulls(Math.max(hist.length - 1, 0)), ...(hist.length ? [num(hist[hist.length - 1].real)] : []), ...proy.map((r) => num(r.proyectado))], borderColor: C.violet, borderDash: [6, 5], borderWidth: 2.5, pointRadius: 4, tension: 0.2, isMoney: true },
                { label: "Escenario alto", data: [...nulls(hist.length), ...proy.map((r) => num(r.banda_sup))], borderColor: "rgba(124,58,237,.35)", borderDash: [3, 4], borderWidth: 1.5, pointRadius: 0, isMoney: true },
                { label: "Escenario bajo", data: [...nulls(hist.length), ...proy.map((r) => num(r.banda_inf))], borderColor: "rgba(124,58,237,.35)", borderDash: [3, 4], borderWidth: 1.5, pointRadius: 0, isMoney: true },
              ],
            },
            options: { ...baseOptions(), interaction: { mode: "index", intersect: false }, scales: { y: axisMoney(), x: axisPlain(11) } },
          }} />
        </Panel>
        <Panel title="Qué comprar primero: cobertura de inventario por material" hint="meses que dura el stock al ritmo de venta de 12 meses — los de arriba se agotan antes" wide>
          {!cob.length ? <Empty msg="Sin historia suficiente de ventas por material" /> : (
            <div className="tablewrap tall">
              <table>
                <thead>
                  <tr><th>Material</th><th className="r">m² en stock</th><th className="r">Venta mensual (m²)</th><th className="r">Cobertura</th></tr>
                </thead>
                <tbody>
                  {cob.map((r) => (
                    <tr key={String(r.key)}>
                      <td className="ell">{String(r.name)}</td>
                      <td className="r">{n1(r.m2_stock)}</td>
                      <td className="r">{n1(r.venta_mensual)}</td>
                      <td className="r">
                        {r.meses == null ? "—" : <Pill tone={num(r.meses) < 2 ? "bad" : num(r.meses) < 4 ? "mid" : "good"}>{`${n1(r.meses)} meses`}</Pill>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}

// Editor inline del costo all-in en DIVISA (EUR/USD/MXN). Se captura en
// la divisa del producto y se guarda en MXN: USD × Banorte; EUR primero
// a USD y luego a MXN (regla de la casa). Solo Autorizadores.
type Fx = { usd_mxn: number; eur_usd: number; eur_mxn: number };

function fxToMxn(cur: string, fx: Fx): number {
  if (cur === "USD") return fx.usd_mxn || 0;
  if (cur === "EUR") return fx.eur_mxn || 0;
  return 1;
}

// La divisa la controla la FILA: este editor solo captura el monto en esa
// divisa y guarda (el backend convierte a MXN con la cadena de la casa).
function CostEditor(props: { tmplId: number; costMxn: number; currency: string; fx: Fx; onSaved: () => void }) {
  const factor = fxToMxn(props.currency, props.fx) || 1;
  const [v, setV] = useState(props.costMxn ? (props.costMxn / factor).toFixed(2) : "");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  const mxnEquiv = (parseFloat(v) || 0) * factor;

  const save = async () => {
    setSaving(true);
    setErr("");
    try {
      const r = (await rpc<Rec>("set_cost", [props.tmplId, parseFloat(v) || 0, props.currency])) as Rec;
      if (r && r.error) throw new Error(String(r.error));
      props.onSaved();
    } catch (e) {
      setErr((e as Error).message || "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <span className={"costcell" + (err ? " err" : "")} title={err || undefined}>
      <input
        value={v}
        inputMode="decimal"
        onChange={(e) => setV(e.target.value.replace(/[^0-9.]/g, ""))}
        onKeyDown={(e) => e.key === "Enter" && !saving && save()}
        aria-label="Costo all-in"
      />
      <button onClick={save} disabled={saving} title="Guardar costo">
        {saving ? "…" : "✓"}
      </button>
      {props.currency !== "MXN" && mxnEquiv > 0 && (
        <small className="costmxn">= {money(mxnEquiv)} MXN</small>
      )}
    </span>
  );
}

// Fila completa del ajuste de precios: la divisa seleccionada re-expresa
// TODO — promedio vendido, N1/N2/N3, diferencia y costo (pivote MXN).
function PriceAdjustRow(props: { r: Rec; fx: Fx; onSaved: () => void }) {
  const { r, fx } = props;
  const [cur, setCur] = useState(String(r.costo_divisa || "USD"));
  const saleFactor = fxToMxn(String(r.cur || "MXN"), fx) || 1;
  const dispFactor = fxToMxn(cur, fx) || 1;
  const cv = (v: unknown) => (num(v) * saleFactor) / dispFactor;
  const diff = cv(r.diff_n1);
  // Utilidad por nivel: precio (en la divisa elegida) menos el costo
  // all-in (MXN → divisa elegida). Sin costo capturado no se inventa.
  const costDisp = num(r.costo) / dispFactor;
  const lvl = (priceDisp: number, strong = false) => {
    if (!num(r.costo) || priceDisp <= 0) {
      return <span className={strong ? "strong" : undefined}>{money(priceDisp)}</span>;
    }
    const util = priceDisp - costDisp;
    const mpct = (util / priceDisp) * 100;
    return (
      <span className="lvlcell">
        <span className={strong ? "strong" : undefined}>{money(priceDisp)}</span>
        <small className={"lvlutil " + (util < 0 ? "bad" : mpct < 15 ? "mid" : "good")}>
          {(util >= 0 ? "+" : "−") + money(Math.abs(util))} · {pct(Math.abs(mpct))}
        </small>
      </span>
    );
  };
  return (
    <tr>
      <td className="ell">{String(r.name)}</td>
      <td>
        <select className="rowcur" value={cur} onChange={(e) => setCur(e.target.value)} aria-label="Divisa de la fila"
                title={`Vendido en ${String(r.cur)}; toda la fila se muestra en la divisa elegida`}>
          <option value="USD">USD</option>
          <option value="EUR" disabled={!fx.eur_usd}>EUR</option>
          <option value="MXN">MXN</option>
        </select>
      </td>
      <td className="r">{n1(r.qty)}</td>
      <td className="r mut">{n0(r.ordenes)}</td>
      <td className="r">{lvl(cv(r.avg), true)}</td>
      <td className="r">{lvl(cv(r.n1))}</td>
      <td className="r mut">{num(r.n2) ? lvl(cv(r.n2)) : "—"}</td>
      <td className="r mut">{num(r.n3) ? lvl(cv(r.n3)) : "—"}</td>
      <td className="r">
        <Pill tone={num(r.diff_n1) < 0 ? "bad" : "good"}>
          {(diff >= 0 ? "+" : "−") + money(Math.abs(diff))} · {pct(Math.abs(num(r.diff_pct)))}
        </Pill>
      </td>
      <td className="r">
        <CostEditor key={cur} tmplId={num(r.key)} costMxn={num(r.costo)} currency={cur} fx={fx} onSaved={props.onSaved} />
      </td>
    </tr>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// CONTROL
// ─────────────────────────────────────────────────────────────────────────────
function ControlView(props: { filters: Filters }) {
  const q = useData(["dashboard", "control", props.filters], () => fetchDashboard("control", props.filters as Rec));
  if (q.loading) return <div className="grid"><Skeleton h={90} /><Skeleton /><Skeleton /></div>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const d = q.data!;
  const k = (d.kpis ?? {}) as Rec;
  const bandeja = arr(d.bandeja);
  const ficha = arr(d.ficha);
  return (
    <>
      <InsightStrip insights={[
        num(k.pendientes_total) > 0 ? {
          metric_id: "auth_pendientes", severity: num(k.pendientes_total) > 20 ? "crit" : "warn",
          text: `${n0(k.pendientes_total)} pendientes operativos activos en la bandeja de control.`,
        } as Insight : null as unknown as Insight,
        num(k.sin_proyecto_pct) > 20 ? {
          metric_id: "inv_m2", severity: "warn",
          text: `${pct(k.sin_proyecto_pct)} de los lotes en stock sin proyecto y ${pct(k.sin_referencia_pct)} sin referencia: deuda de datos.`,
        } as Insight : null as unknown as Insight,
        num(k.productos_ajuste_precio) > 0 ? {
          metric_id: "realizacion_pct", severity: "warn",
          text: `${n0(k.productos_ajuste_precio)} producto(s) vendidos fuera de la escalera de precios vigente.`,
        } as Insight : null as unknown as Insight,
      ].filter(Boolean) as Insight[]} />
      <div className="grid" style={{ marginBottom: 12 }}>
        <Panel title="Cobertura fotográfica de lotes" hint="sin foto no se vende — subir es mejor">
          <EChartBox height={260} deps={[k.lotes_foto_pct]}
            option={gaugeOpt(num(k.lotes_foto_pct), "LOTES CON FOTO", 100, (v) => pct(v),
              [[0.5, "#dc2626"], [0.8, "#d97706"], [1, "#059669"]])} />
        </Panel>
        <Panel title="Cobertura fotográfica de placas" hint="catálogo completo — subir es mejor">
          <EChartBox height={260} deps={[k.placas_foto_pct]}
            option={gaugeOpt(num(k.placas_foto_pct), "PLACAS CON FOTO", 100, (v) => pct(v),
              [[0.5, "#dc2626"], [0.8, "#d97706"], [1, "#059669"]])} />
        </Panel>
      </div>

      <div className="stats">
        <Stat label="Pendientes totales" value={n0(k.pendientes_total)} sub="meta: bandeja en cero" tone={num(k.pendientes_total) > 0 ? "mid" : "good"} />
        <Stat label="Productos con ajuste de precio" value={n0(k.productos_ajuste_precio)} sub="vendidos fuera de N1/N2" tone={num(k.productos_ajuste_precio) > 0 ? "mid" : "good"} />
        <Stat label="Lotes en stock" value={n0(k.lotes_en_stock)} />
        <Stat label="Lotes con fotografía" value={pct(k.lotes_foto_pct)} sub={`${n0(k.lotes_con_foto)} lotes en stock con foto`} tone={num(k.lotes_foto_pct) < 70 ? "bad" : num(k.lotes_foto_pct) < 90 ? "mid" : "good"} />
        <Stat label="Fotos de lote este mes" value={n0(k.fotos_lote_mes)} sub={`${n0(k.fotos_lote_total)} en total`} />
        <Stat label="Fotos de bloque este mes" value={n0(k.fotos_mes)} sub={`${n0(k.fotos_total)} en total · ${pct(k.placas_foto_pct)} placas con foto de bloque`} />
        <Stat label="Órdenes sin proyecto" value={pct(k.sin_proyecto_pct)} tone={num(k.sin_proyecto_pct) > 30 ? "mid" : ""} />
        <Stat label="Sin referencia del cliente" value={pct(k.sin_referencia_pct)} tone={num(k.sin_referencia_pct) > 30 ? "mid" : ""} />
      </div>
      <div className="grid">
        <Panel title="Ajuste de precios — vendidos fuera de N1/N2 en el periodo" hint={`edita el costo en la divisa del producto — TC Banorte ${n1((d.fx as Rec)?.usd_mxn)} · EUR/USD ${n1((d.fx as Rec)?.eur_usd)}`} wide>
          {!arr(d.price_adjust).length ? <Empty msg="Todo lo vendido en el periodo salió a N1 o N2 — sin ajustes pendientes" /> : (
            <div className="tablewrap tall">
              <table>
                <thead>
                  <tr>
                    <th>Producto</th><th>Div</th>
                    <th className="r">Cant.</th><th className="r">Órdenes</th>
                    <th className="r">Prom. vendido</th>
                    <th className="r">N1</th><th className="r">N2</th><th className="r">N3</th>
                    <th className="r">Dif vs N1</th>
                    <th className="r">Costo all-in</th>
                  </tr>
                </thead>
                <tbody>
                  {arr(d.price_adjust).map((r) => (
                    <PriceAdjustRow
                      key={String(r.key) + String(r.cur)}
                      r={r}
                      fx={{ usd_mxn: num((d.fx as Rec)?.usd_mxn), eur_usd: num((d.fx as Rec)?.eur_usd), eur_mxn: num((d.fx as Rec)?.eur_mxn) }}
                      onSaved={() => q.retry()}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <Panel title="Bandeja de pendientes — todo lo que espera una decisión" wide>
          <div className="tablewrap">
            <table>
              <thead><tr><th>Pendiente</th><th className="r">Cuántos</th><th className="r">Más viejo (días)</th></tr></thead>
              <tbody>
                {bandeja.map((b, i) => (
                  <tr key={i}>
                    <td className="ell" style={{ maxWidth: 420 }}>{String(b.label)}</td>
                    <td className="r">{num(b.count) > 0 ? <Pill tone={num(b.age) > 7 ? "bad" : "mid"}>{n0(b.count)}</Pill> : <Pill tone="good">0</Pill>}</td>
                    <td className="r mut">{num(b.count) > 0 ? n1(b.age) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        <Panel title="Completitud de ficha de lote (stock actual)" wide>
          <div className="bars">
            {ficha.map((fch, i) => {
              const p = num(fch.pct);
              return (
                <div key={i} className="bar-row">
                  <span className="t">{String(fch.campo)}</span>
                  <div className="track"><div className={"fill" + (p < 50 ? " bad" : p < 80 ? " mid" : "")} style={{ width: `${Math.min(100, p)}%` }} /></div>
                  <b>{pct(p)}</b>
                </div>
              );
            })}
          </div>
        </Panel>
        <Panel title="Quién sube fotos de LOTE — el ranking a premiar" hint="fotos en la ficha del lote (stock.lot.image)" wide>
          {!arr(d.lot_photo_uploaders).length ? <Empty msg="Aún no hay fotos de lote registradas" /> : (
            <div className="tablewrap">
              <table>
                <thead><tr><th>Quién</th><th className="r">Este mes</th><th className="r">Total histórico</th><th>Última foto</th></tr></thead>
                <tbody>
                  {arr(d.lot_photo_uploaders).map((u, i) => (
                    <tr key={i}>
                      <td className="ell">{i === 0 && num(u.mes) > 0 ? "🏆 " : ""}{String(u.name)}</td>
                      <td className="r strong">{n0(u.mes)}</td>
                      <td className="r">{n0(u.total)}</td>
                      <td className="mut">{String(u.ultima ?? "")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <Panel title="Quién sube fotos de bloque" hint="fotos de bloque del embarque (portal y torre)">
          {!arr(d.photo_uploaders).length ? <Empty msg="Aún no hay fotos de bloque registradas" /> : (
            <div className="tablewrap">
              <table>
                <thead><tr><th>Quién</th><th className="r">Este mes</th><th className="r">Total</th><th>Última foto</th></tr></thead>
                <tbody>
                  {arr(d.photo_uploaders).map((u, i) => (
                    <tr key={i}>
                      <td className="ell">{String(u.name)}</td>
                      <td className="r strong">{n0(u.mes)}</td>
                      <td className="r">{n0(u.total)}</td>
                      <td className="mut">{String(u.ultima ?? "")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <Panel title="Fotos subidas por mes (12 meses)">
          <ChartBox height={280} deps={[arr(d.lot_photos_by_month), arr(d.photos_by_month)]} config={(() => {
            const lot = arr(d.lot_photos_by_month);
            const blk = arr(d.photos_by_month);
            const keys = Array.from(new Set([...lot.map((r) => String(r.key)), ...blk.map((r) => String(r.key))])).sort();
            const lm = new Map(lot.map((r) => [String(r.key), num(r.fotos)]));
            const bm2 = new Map(blk.map((r) => [String(r.key), num(r.fotos)]));
            return {
              type: "bar",
              data: {
                labels: keys.map(monthLabel),
                datasets: [
                  { label: "Fotos de lote", data: keys.map((kk) => lm.get(kk) ?? 0), backgroundColor: "rgba(11,87,208,.85)", borderRadius: 5, maxBarThickness: 22 },
                  { label: "Fotos de bloque", data: keys.map((kk) => bm2.get(kk) ?? 0), backgroundColor: "rgba(124,58,237,.7)", borderRadius: 5, maxBarThickness: 22 },
                ],
              },
              options: { ...baseOptions(), interaction: { mode: "index", intersect: false }, scales: { y: axisMoney(), x: axisPlain(10.5) } },
            };
          })()} />
        </Panel>
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Drill panel: pila con breadcrumbs; venta → líneas → material → historia
// ─────────────────────────────────────────────────────────────────────────────
function DrillPanel(props: { stack: DrillNode[]; filters: Filters; push: (n: DrillNode) => void; popTo: (i: number) => void; close: () => void }) {
  const node = props.stack[props.stack.length - 1];
  const key =
    node.kind === "order" ? ["order_lines", node.orderId]
    : node.kind === "bucket" ? ["drill", "aging", node.mode, node.value]
    : node.kind === "finpartner" ? ["drill", "fin", node.side, node.partnerId]
    : ["drill", node.entity, node.value, props.filters];
  const q = useData<Rec>(key, () =>
    node.kind === "order"
      ? (fetchOrderLines(node.orderId) as unknown as Promise<Rec>)
      : node.kind === "bucket"
        ? fetchDrill(node.mode === "date" ? "aging_date" : "aging_folio", node.value, node.label, {})
        : node.kind === "finpartner"
          ? fetchDrill(node.side === "ar" ? "partner_ar" : "partner_ap", node.partnerId, node.label, {})
          : (fetchDrill(node.entity, node.value, node.label, props.filters as Rec) as Promise<Rec>),
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && props.close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [props]);

  return (
    <div className="drill-bg" onClick={props.close}>
      <aside className="drill" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={node.label}>
        <header className="drill-h">
          <nav className="crumbs" aria-label="Ruta de profundización">
            <button onClick={props.close}>Tablero</button>
            {props.stack.map((n, i) => (
              <span key={i}>
                <i>›</i>
                {i === props.stack.length - 1 ? <b>{n.label}</b> : <button onClick={() => props.popTo(i)}>{n.label}</button>}
              </span>
            ))}
          </nav>
          <button className="x" onClick={props.close} aria-label="Cerrar">✕</button>
        </header>

        {q.loading && <><Skeleton h={80} /><Skeleton h={220} /><Skeleton h={220} /></>}
        {q.error && <ErrorBox msg={q.error} retry={q.retry} />}

        {!q.loading && !q.error && node.kind === "order" && (() => {
          const d = q.data!;
          const o = (d.order ?? {}) as Rec;
          const lines = arr(d.lines);
          // Sin permiso de costos el servidor enmascara utilidad/costo/margen
          // (viajan null): aquí solo se decide NO pintarlos — cero ceros falsos.
          const canProfit = (d as Rec).perm_profit !== false;
          const venta = lines.reduce((s, l) => s + num(l.venta), 0);
          const util = lines.reduce((s, l) => s + num(l.utilidad), 0);
          return (
            <>
              <div className="stats drill-stats">
                <Stat label="Cliente" value={String(o.partner)} sub={`${o.date} · ${o.seller} · ${o.currency}`} />
                <Stat label="Venta MXN" value={money(venta)} />
                {canProfit && <Stat label="Utilidad all-in" value={money(util)} tone={util < 0 ? "bad" : "good"} />}
                {canProfit && <Stat label="Margen" value={venta ? pct((util / venta) * 100) : "—"} tone={marginTone(venta ? (util / venta) * 100 : 0)} />}
              </div>
              <Panel title={canProfit ? "Utilidad por material de esta venta" : "Materiales de esta venta"} hint="click en material = seguir profundizando">
                <div className="tablewrap tall">
                  <table>
                    <thead>
                      <tr><th>Material</th><th>Categoría</th><th>Nivel</th><th className="r">Cant.</th><th className="r">Venta</th>{canProfit && <th className="r">Costo all-in</th>}{canProfit && <th className="r">Utilidad</th>}{canProfit && <th className="r">Margen</th>}</tr>
                    </thead>
                    <tbody>
                      {lines.map((l, i) => (
                        <tr key={i} className="click" onClick={() => props.push({ kind: "entity", entity: "product", value: num(l.tmpl_id), label: String(l.product) })}>
                          <td className="ell">{String(l.product)}</td>
                          <td className="ell mut">{String(l.categ)}</td>
                          <td>{String(l.level)}</td>
                          <td className="r">{n1(l.qty)}{l.is_area ? " m²" : " pz"}</td>
                          <td className="r strong">{money(l.venta)}</td>
                          {canProfit && <td className="r mut">{money(l.costo)}</td>}
                          {canProfit && <td className={"r " + (num(l.utilidad) < 0 ? "neg" : "")}>{money(l.utilidad)}</td>}
                          {canProfit && <td className="r"><Pill tone={marginTone(num(l.margen))}>{pct(l.margen)}</Pill></td>}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Panel>
            </>
          );
        })()}

        {!q.loading && !q.error && node.kind === "bucket" && (() => {
          const d = q.data!;
          const k = (d.kpis ?? {}) as Rec;
          const mats = arr(d.materials);
          const cats = arr(d.categories);
          return (
            <>
              <div className="stats drill-stats">
                <Stat label="m² en el bucket" value={n1(k.m2)} />
                <Stat label="Lotes" value={n0(k.lots)} />
                <Stat label="Valor all-in" value={money(k.valor)} />
                <Stat label="Materiales distintos" value={n0(k.materiales)} />
              </div>
              <Panel title="Qué materiales son" hint="click = historia completa del material">
                {!mats.length ? <Empty msg="Nada en este rango de antigüedad" /> : (
                  <div className="tablewrap tall">
                    <table>
                      <thead><tr><th>Material</th><th className="r">m²</th><th className="r">Lotes</th><th className="r">Valor all-in</th></tr></thead>
                      <tbody>
                        {mats.map((m) => (
                          <tr key={String(m.key)} className="click" onClick={() => props.push({ kind: "entity", entity: "product", value: num(m.key), label: String(m.name) })}>
                            <td className="ell">{String(m.name)}</td>
                            <td className="r strong">{n1(m.m2)}</td>
                            <td className="r mut">{n0(m.lots)}</td>
                            <td className="r">{money(m.valor)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Panel>
              <Panel title="Por categoría">
                <MiniTable head={["Categoría", "m²", "Valor"]} rows={cats.map((c, i) => ({
                  key: i, a: String(c.name), b: n1(c.m2), c: money(c.valor),
                }))} />
              </Panel>
            </>
          );
        })()}

        {!q.loading && !q.error && node.kind === "finpartner" && (() => {
          const d = q.data!;
          const k = (d.kpis ?? {}) as Rec;
          const invs = arr(d.invoices);
          const deuda = node.side === "ar";
          return (
            <>
              <div className="stats drill-stats">
                <Stat label={deuda ? "Me debe" : "Le debo"} value={money(k.total_mxn)} sub="MXN al TC de registro" />
                <Stat label="Ya vencido" value={money(k.vencido_mxn)} sub={pct(k.vencido_pct)} tone={num(k.vencido_mxn) > 0 ? "bad" : "good"} />
                <Stat label="Facturas abiertas" value={n0(k.facturas)} />
                <Stat label={deuda ? "Me paga en" : "Le pago en"} value={num(k.pago_prom_dias) ? `${n1(k.pago_prom_dias)} días` : "—"} sub="promedio últimos 12 meses" />
              </div>
              <Panel title="Factura por factura" hint="saldo en su divisa original y en MXN al TC del día de registro">
                {!invs.length ? <Empty msg="Sin facturas abiertas" /> : (
                  <div className="tablewrap tall">
                    <table>
                      <thead>
                        <tr><th>Factura</th><th>Fecha</th><th>Vence</th><th className="r">Atraso</th><th>Divisa</th><th className="r">Saldo divisa</th><th className="r">Saldo MXN</th></tr>
                      </thead>
                      <tbody>
                        {invs.map((i, ix) => (
                          <tr key={ix}>
                            <td className="mono">{String(i.name)}</td>
                            <td className="mut">{String(i.date ?? "")}</td>
                            <td className="mut">{String(i.due ?? "")}</td>
                            <td className={"r " + (num(i.atraso) > 0 ? "neg" : "")}>{num(i.atraso) > 0 ? `${n0(i.atraso)} días` : "Al corriente"}</td>
                            <td>{String(i.currency)}</td>
                            <td className="r">{money(i.residual_divisa)}</td>
                            <td className="r strong">{money(i.residual_mxn)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Panel>
            </>
          );
        })()}

        {!q.loading && !q.error && node.kind === "entity" && (() => {
          const d = q.data!;
          const k = (d.kpis ?? {}) as Rec;
          const bm = arr(d.by_month);
          const dim = node.entity === "product" || node.entity === "level" ? arr(d.by_seller) : arr(d.by_product);
          const dimTitle = node.entity === "product" || node.entity === "level" ? "Quién lo vende" : "Qué materiales lleva";
          const cats = arr(d.by_category);
          return (
            <>
              <div className="stats drill-stats">
                <Stat label="Venta" value={money(k.venta_mxn)} />
                <Stat label="Utilidad all-in" value={money(k.utilidad_mxn)} sub={`Margen ${pct(k.margen_pct)}`} tone={marginTone(num(k.margen_pct))} />
                <Stat label="m²" value={n1(k.m2)} />
                <Stat label="Órdenes" value={n0(k.ordenes)} />
                {d.stock != null && <Stat label="Stock actual" value={`${n1((d.stock as Rec).disponible_m2)} m²`} sub={`Valor ${money((d.stock as Rec).valor_mxn)}`} />}
                {d.por_cobrar != null && <Stat label="Me debe" value={money(d.por_cobrar)} tone={num(d.por_cobrar) > 0 ? "mid" : "good"} />}
              </div>
              <Panel title="Su tendencia mensual">
                <ChartBox height={200} deps={[bm]} config={{
                  type: "line",
                  data: {
                    labels: bm.map((r) => monthLabel(r.key)),
                    datasets: [
                      { label: "Venta", data: bm.map((r) => num(r.venta)), borderColor: C.blue, backgroundColor: "rgba(11,87,208,.08)", borderWidth: 2.5, tension: 0.4, fill: true, isMoney: true },
                      { label: "Utilidad", data: bm.map((r) => num(r.utilidad)), borderColor: C.green, backgroundColor: "rgba(5,150,105,.07)", borderWidth: 2.5, tension: 0.4, fill: true, isMoney: true },
                    ],
                  },
                  options: { ...baseOptions(), interaction: { mode: "index", intersect: false }, scales: { y: axisMoney(), x: axisPlain(10) } },
                }} />
              </Panel>
              <div className="drill-2col">
                <Panel title={dimTitle} hint="click = seguir">
                  <MiniTable head={["", "Venta", "Margen"]} rows={dim.slice(0, 8).map((r) => ({
                    key: String(r.key), a: String(r.name), b: money(r.venta),
                    c: <Pill tone={marginTone(num(r.margen))}>{pct(r.margen)}</Pill>,
                    onClick: () => props.push({
                      kind: "entity",
                      entity: node.entity === "product" || node.entity === "level" ? "seller" : "product",
                      value: num(r.key), label: String(r.name),
                    }),
                  }))} />
                </Panel>
                <Panel title="Por categoría">
                  <MiniTable head={["Categoría", "Venta", "Margen"]} rows={cats.slice(0, 8).map((r) => ({
                    key: String(r.key), a: String(r.name), b: money(r.venta),
                    c: <Pill tone={marginTone(num(r.margen))}>{pct(r.margen)}</Pill>,
                  }))} />
                </Panel>
              </div>
              <Panel title="Órdenes" hint="click = utilidad por material">
                <OrdersTable orders={arr(d.orders)} onOrder={(id, name) => props.push({ kind: "order", orderId: id, label: name })} />
              </Panel>
            </>
          );
        })()}
      </aside>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// App shell
// ─────────────────────────────────────────────────────────────────────────────
const PRESETS: Array<[string, string]> = [["hoy", "Hoy"], ["sem", "Semana"], ["mes", "Mes"], ["trim", "Trimestre"], ["anio", "Año"]];

// ═════════════════════════════════════════════════════════════════════════════
// FASE 2 — Componentes base del rediseño (registro semántico + storytelling)
// ═════════════════════════════════════════════════════════════════════════════

// KPI semántico: color por DIRECCIÓN declarada en el registro (bajar cartera
// vencida es bueno; subir merma es malo) y "explicar métrica" en el tooltip.
function Kpi(props: { id: string; value: string; sub?: string; deltaPct?: number | null; drillTo?: () => void }) {
  const def = METRICS[props.id];
  const tone = deltaTone(props.id, props.deltaPct);
  const deltaTxt = props.deltaPct != null && isFinite(props.deltaPct)
    ? `${props.deltaPct >= 0 ? "▲" : "▼"} ${pct(Math.abs(props.deltaPct))} vs base`
    : undefined;
  return (
    <div
      className={`kpi-sem${props.drillTo ? " click" : ""}`}
      title={metricTooltip(props.id)}
      onClick={props.drillTo}
      role={props.drillTo ? "button" : undefined}
      tabIndex={props.drillTo ? 0 : undefined}
      onKeyDown={(e) => { if (props.drillTo && (e.key === "Enter" || e.key === " ")) props.drillTo(); }}
    >
      <Stat label={def?.label ?? props.id} value={props.value} sub={props.sub ?? deltaTxt} tone={tone} />
    </div>
  );
}

// Narrativa determinística: estructura calculada → texto legible. Nunca
// atribuye causalidad; usa "se concentra en" / "coincide con".
type Insight = {
  metric_id: string;
  severity: "info" | "warn" | "crit";
  text: string;
  drillTo?: () => void;
};

function InsightStrip(props: { insights: Insight[] }) {
  const items = props.insights.filter(Boolean).slice(0, 5);
  if (!items.length) return null;
  return (
    <div className="insight-strip" aria-label="Hallazgos">
      {items.map((i, idx) => (
        <button key={idx} className={`insight insight--${i.severity}${i.drillTo ? "" : " static"}`}
                onClick={i.drillTo} disabled={!i.drillTo}>
          {i.text}
        </button>
      ))}
    </div>
  );
}

// Barra VITAL contextual: máximo 4 señales según el dominio activo, comparte
// la caché del exec (misma query key) y se pausa con la pestaña oculta
// (react-query no refetchea en background por default).
const VITAL_BY_DOMAIN: Record<string, string[]> = {
  inicio: ["venta_hoy", "fact_real_mes", "bancos_mxn", "auth_pendientes"],
  ventas: ["venta_hoy", "venta_mes", "m2_mes", "auth_pendientes"],
  inventario: ["inv_m2", "holds_activos", "inv_edad_dias", "m2_agua"],
  abastecimiento: ["m2_agua", "tc_banorte", "inv_m2", "fact_previa_mes"],
  operaciones: ["venta_hoy", "m2_mes", "inv_m2", "holds_activos"],
  finanzas: ["bancos_mxn", "por_cobrar", "por_pagar", "fact_previa_mes"],
  inteligencia: ["venta_mes", "m2_agua", "inv_m2", "bancos_mxn"],
  control: ["auth_pendientes", "fact_previa_mes", "holds_activos", "inv_edad_dias"],
};

function vitalValue(id: string, d: Rec): string {
  const v = d[id];
  switch (id) {
    case "m2_mes": case "inv_m2": case "m2_agua": return `${n1(v)} m²`;
    case "inv_edad_dias": return `${n0(v)} días`;
    case "auth_pendientes": case "holds_activos": return n0(v);
    case "tc_banorte": return n1(v);
    default: return money(v);
  }
}

function VitalBar(props: { domainId: string; goHome: () => void }) {
  const q = useData(["exec"], fetchExec, { refetchInterval: 60_000 });
  if (!q.data) return null;
  const d = q.data as Rec;
  const ids = VITAL_BY_DOMAIN[props.domainId] ?? VITAL_BY_DOMAIN.inicio;
  return (
    <div className="vitalbar" aria-label="Señales vitales">
      {ids.map((id) => (
        <span key={id} className="vital" title={metricTooltip(id)}>
          <span className="vital-l">{METRICS[id]?.label ?? id}</span>
          <strong className="vital-v">{vitalValue(id, d)}</strong>
        </span>
      ))}
      <button className="vital-more" onClick={props.goHome}>Ver resumen →</button>
    </div>
  );
}

// Fábricas de visuales ejecutivos reutilizables (patrón Finanzas).
function gaugeOpt(value: number, name: string, max: number,
                  fmt: (v: number) => string,
                  bands: Array<[number, string]>): Record<string, unknown> {
  return {
    ...ecBase(),
    series: [{
      type: "gauge", startAngle: 210, endAngle: -30, min: 0, max,
      radius: "92%", center: ["50%", "58%"],
      axisLine: { lineStyle: { width: 14, color: bands } },
      pointer: { itemStyle: { color: "auto" }, width: 4 },
      axisTick: { show: false }, splitLine: { show: false },
      axisLabel: { fontSize: 9, distance: 16 },
      title: { offsetCenter: [0, "70%"], fontSize: 11, fontWeight: 700 },
      detail: { fontSize: 24, fontWeight: 800, offsetCenter: [0, "36%"],
                color: "auto", formatter: (v: number) => fmt(v) },
      data: [{ value, name }],
    }],
  };
}

const WF_GRADS = {
  blue: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "#60a5fa" }, { offset: 1, color: "#0b57d0" }] },
  green: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "#34d399" }, { offset: 1, color: "#059669" }] },
  red: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "#f87171" }, { offset: 1, color: "#dc2626" }] },
  violet: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "#a78bfa" }, { offset: 1, color: "#6d28d9" }] },
  amber: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "#fbbf24" }, { offset: 1, color: "#d97706" }] },
};

function waterfallOpt(steps: Array<{ label: string; value: number; base: number; color: keyof typeof WF_GRADS }>): Record<string, unknown> {
  return {
    ...ecBase(),
    tooltip: { ...(ecBase().tooltip as object),
      formatter: (pm: { dataIndex: number }) => {
        const st = steps[pm.dataIndex];
        return st ? `${st.label}<br/><b>${money(st.value)}</b>` : "";
      } },
    xAxis: ecAxis("cat", steps.map((st) => st.label)),
    yAxis: ecAxis("money"),
    series: [
      { type: "bar", stack: "w", barMaxWidth: 68, silent: true,
        itemStyle: { color: "transparent" }, data: steps.map((st) => st.base), tooltip: { show: false } },
      { type: "bar", stack: "w", barMaxWidth: 68,
        label: { show: true, position: "top", fontWeight: 800, fontSize: 11.5,
                 formatter: (pm: { dataIndex: number }) => money(steps[pm.dataIndex]?.value) },
        data: steps.map((st) => ({ value: st.value, itemStyle: { color: WF_GRADS[st.color], borderRadius: [7, 7, 0, 0] } })) },
    ],
  };
}

// Line race reutilizable: series mensuales por entidad con endLabel
// perseguidor y anti-encimado (labelLayout hideOverlap + shiftY).
function raceOpt(entities: Rec[], months: string[], nameKey: string): Record<string, unknown> {
  if (!entities.length || months.length < 2) return { ...ecBase(), series: [] };
  const RACE = ["#0b57d0", "#0ea5e9", "#059669", "#d97706", "#7c3aed", "#db2777"];
  return {
    ...ecBase(),
    grid: { left: 8, right: 150, top: 14, bottom: 8, containLabel: true },
    tooltip: { ...(ecBase().tooltip as object), trigger: "axis" },
    xAxis: ecAxis("cat", months.map((m) => monthLabel(m).toUpperCase())),
    yAxis: ecAxis("money"),
    animationDuration: 2600,
    animationEasing: "cubicOut",
    series: entities.map((sr, i) => ({
      name: String(sr[nameKey]).slice(0, 18),
      type: "line", smooth: true, symbolSize: 6,
      data: arr<number>(sr.serie),
      lineStyle: { width: 3, color: RACE[i % 6] },
      itemStyle: { color: RACE[i % 6] },
      emphasis: { focus: "series", lineStyle: { width: 5 } },
      endLabel: { show: true, fontSize: 11, fontWeight: 800, fontFamily: "Inter",
                  color: RACE[i % 6],
                  formatter: (pm: { seriesName: string; value: number }) =>
                    `${pm.seriesName} · ${money(pm.value)}` },
      labelLayout: { hideOverlap: true, moveOverlap: "shiftY" },
    })),
  };
}

// ═════════ COMMAND CENTER (portada universal, responsivo) ═════════
function CommandCenterView(props: { filters: Filters; drill: (n: DrillNode) => void; go: (v: ViewKey) => void }) {
  const ex = useData(["exec"], fetchExec, { refetchInterval: 60_000 });
  const rz = useData(["dashboard", "resumen", props.filters], () => fetchDashboard("resumen", props.filters as Rec), { refetchInterval: TV_REFRESH_MS });
  const cm = useData(["dashboard", "comercial", { ...props.filters, source: "odoo" }], () => fetchDashboard("comercial", { ...props.filters, source: "odoo" } as Rec), { refetchInterval: TV_REFRESH_MS });
  const ct = useData(["dashboard", "control", props.filters], () => fetchDashboard("control", props.filters as Rec), { refetchInterval: TV_REFRESH_MS });

  if (ex.loading) return <div className="grid"><Skeleton h={140} /><Skeleton h={280} /></div>;
  if (ex.error) return <ErrorBox msg={ex.error} retry={ex.retry} />;
  const d = ex.data! as Rec;
  const canProfit = d.perm_profit !== false;
  const rk = (rz.data?.kpis ?? {}) as Rec;
  const months = rz.data ? arr(rz.data.by_month) : [];
  const bandeja = ct.data ? arr(ct.data.bandeja) : [];
  const pendientes = bandeja
    .map((b) => ({ label: String(b.label ?? b.name ?? ""), count: num(b.count) }))
    .filter((b) => b.count > 0)
    .sort((a, b) => b.count - a.count);

  const mom = num(d.venta_mom_pct);
  const insights: Insight[] = [];
  if (isFinite(mom) && mom !== 0) {
    insights.push({
      metric_id: "venta_mes", severity: mom < 0 ? "warn" : "info",
      text: `Los pedidos del mes van ${mom >= 0 ? "▲" : "▼"} ${pct(Math.abs(mom))} contra el mes anterior (${money(d.venta_mes)} vs ${money(d.venta_mes_prev)}).`,
      drillTo: () => props.go("ventas"),
    });
  }
  const gapFact = num(d.venta_mes) - num(d.fact_real_mes);
  if (gapFact > 0 && num(d.venta_mes) > 0) {
    insights.push({
      metric_id: "fact_real_mes", severity: "info",
      text: `${money(gapFact)} de pedidos del mes aún no se reflejan como facturación timbrada (previas: ${money(d.fact_previa_mes)} en ${n0(d.fact_previa_count)} borradores).`,
      drillTo: () => props.go("finanzas"),
    });
  }
  if (num(d.auth_pendientes) > 0) {
    insights.push({
      metric_id: "auth_pendientes", severity: "warn",
      text: `${n0(d.auth_pendientes)} autorización(es) de precio pendientes deteniendo negocio.`,
      drillTo: () => props.go("ventas_auth"),
    });
  }

  const core: Array<{ id: string; value: string; sub?: string; deltaPct?: number | null; go?: ViewKey }> = [
    { id: "venta_hoy", value: money(d.venta_hoy), sub: "MXN", go: "ventas" },
    { id: "fact_real_mes", value: money(d.fact_real_mes), sub: "timbrada, NC descontadas", go: "finanzas" },
    { id: "fact_previa_mes", value: money(d.fact_previa_mes), sub: `${n0(d.fact_previa_count)} borradores`, go: "finanzas" },
    { id: "venta_mes", value: money(d.venta_mes), deltaPct: mom, go: "ventas" },
    { id: "bancos_mxn", value: money(d.bancos_mxn), go: "finanzas" },
    { id: "por_cobrar", value: money(d.por_cobrar), sub: "al TC del registro", go: "finanzas" },
    { id: "inv_m2", value: `${n1(d.inv_m2)} m²`, sub: `${n0(d.holds_activos)} holds`, go: "inventario" },
    { id: "m2_agua", value: `${n1(d.m2_agua)} m²`, sub: `${n0(d.contenedores_agua)} contenedores`, go: "transito" },
  ];
  const extra: typeof core = [
    ...(canProfit ? [{ id: "utilidad_mes", value: money(d.utilidad_mes), sub: `Margen ${pct(d.margen_mes)}` }] : []),
    { id: "m2_mes", value: n1(d.m2_mes) },
    { id: "cajas_mes", value: `${n0(d.cajas_mes)} cajas`, sub: money(d.venta_cajas_mes) },
    { id: "por_pagar", value: money(d.por_pagar), go: "finanzas" },
    { id: "inv_edad_dias", value: `${n0(d.inv_edad_dias)} días`, go: "materiales" },
    { id: "auth_pendientes", value: n0(d.auth_pendientes), go: "ventas_auth" },
  ];

  return (
    <>
      <InsightStrip insights={insights} />
      <div className="cc-score cc-score--full">
        {[...core, ...extra].map((k) => (
          <Kpi key={k.id} id={k.id} value={k.value} sub={k.sub} deltaPct={k.deltaPct}
               drillTo={k.go ? () => props.go(k.go!) : undefined} />
        ))}
      </div>

      <div className="grid">
        {months.length > 0 && (
          <Panel title="Venta y utilidad por mes" hint="pack Resumen · 5 min">
            <EChartBox height={340} deps={[months, canProfit]} option={{
              ...ecBase(),
              tooltip: { ...(ecBase().tooltip as object), trigger: "axis" },
              legend: { top: 0, textStyle: { color: "inherit" } },
              xAxis: ecAxis("cat", months.map((r) => monthLabel(String(r.key)))),
              yAxis: ecAxis("money"),
              series: [
                { name: "Venta", type: "bar", data: months.map((r) => num(r.venta)),
                  itemStyle: { borderRadius: [6, 6, 0, 0], color: EC.blue }, barMaxWidth: 36 },
                ...(canProfit ? [{ name: "Utilidad", type: "line", smooth: true,
                  data: months.map((r) => num(r.utilidad)), lineStyle: { width: 3, color: EC.green },
                  itemStyle: { color: EC.green }, areaStyle: { opacity: 0.12, color: EC.green } }] : []),
              ],
            }} />
          </Panel>
        )}

        <Panel title="Capital comprometido" hint="liquidez vs activos no líquidos">
          <MiniTable head={["Componente", "Monto", "Naturaleza"]} rows={[
            { key: "b", a: "Bancos y cajas", b: money(d.bancos_mxn), c: "Líquido" },
            { key: "ar", a: "Por cobrar", b: money(d.por_cobrar), c: "Exigible" },
            { key: "ap", a: "Por pagar", b: `− ${money(d.por_pagar)}`, c: "Obligación" },
            { key: "inv", a: `Inventario (${n1(d.inv_m2)} m²)`, b: money(rk.inv_valor_mxn), c: "No líquido" },
            { key: "tr", a: `En el agua (${n1(d.m2_agua)} m²)`, b: `${n0(d.contenedores_agua)} contenedores`, c: "No líquido" },
          ]} />
        </Panel>

        {cm.data && arr(cm.data.daily_sales).length > 0 && (
          <Panel title="Actividad diaria de pedidos" hint="calendario del periodo filtrado · intensidad = venta del día" wide>
            <EChartBox height={210} deps={[arr(cm.data.daily_sales)]} option={(() => {
              const daily = arr(cm.data!.daily_sales);
              const values = daily.map((dd) => [String(dd.date), num(dd.amount)]);
              const dates = daily.map((dd) => String(dd.date)).sort();
              const maxV = Math.max(...daily.map((dd) => num(dd.amount)), 1);
              return {
                ...ecBase(),
                tooltip: { ...(ecBase().tooltip as object),
                  formatter: (pm: { value: [string, number] }) => {
                    const dd = daily.find((x) => String(x.date) === pm.value[0]);
                    return `${pm.value[0]}<br/><b>${money(pm.value[1])}</b> · ${n0(dd?.orders)} pedidos`;
                  } },
                visualMap: {
                  min: 0, max: maxV, orient: "horizontal", left: "center", bottom: 0,
                  itemWidth: 10, itemHeight: 90, calculable: false,
                  inRange: { color: ["#e0edf9", "#93c5fd", "#3b82f6", "#0b57d0", "#062e6f"] },
                  textStyle: { fontSize: 10 },
                },
                calendar: {
                  range: [dates[0], dates[dates.length - 1]],
                  top: 24, left: 40, right: 10, cellSize: ["auto", 15],
                  dayLabel: { nameMap: ["D", "L", "M", "M", "J", "V", "S"], fontSize: 10 },
                  monthLabel: { nameMap: MONTHS_ES, fontSize: 10.5 },
                  yearLabel: { show: false },
                  itemStyle: { borderWidth: 2.5, borderColor: "rgba(255,255,255,.9)", borderRadius: 4 },
                  splitLine: { show: false },
                },
                series: [{ type: "heatmap", coordinateSystem: "calendar", data: values }],
              };
            })()} />
          </Panel>
        )}
        <Panel title="Atención hoy" hint="bandeja de control · click = ir" wide>
          {pendientes.length === 0 && <Empty msg="Sin pendientes accionables en la bandeja de control." />}
          {pendientes.length > 0 && (
            <div className="cc-attn">
              {pendientes.slice(0, 10).map((p, i) => (
                <button key={i} className="cc-attn-item" onClick={() => props.go("control")}>
                  <span className="cc-attn-count">{n0(p.count)}</span>
                  <span className="cc-attn-label">{p.label}</span>
                </button>
              ))}
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}

// ═════════ SUBPÁGINAS DE VENTAS (todas reutilizan el pack 'comercial':
// misma query key ⇒ una sola consulta compartida entre páginas) ═════════
function useComercial(filters: Filters) {
  const f = { ...filters, source: "odoo" } as Rec;
  return useData(["dashboard", "comercial", f], () => fetchDashboard("comercial", f));
}

function SalesSub(props: { filters: Filters; children: (k: Rec, d: Rec) => React.ReactNode }) {
  const q = useComercial(props.filters);
  if (q.loading) return <div className="grid"><Skeleton h={120} /><Skeleton h={300} /></div>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const d = q.data! as Rec;
  return <>{props.children((d.kpis ?? {}) as Rec, d)}</>;
}

function VentasConversionView(props: { filters: Filters; go: (v: ViewKey) => void }) {
  return (
    <SalesSub filters={props.filters}>{(k, d) => {
      const funnel = arr(d.funnel).filter((s) => String(s.stage) !== "Cancelada");
      const cancel = arr(d.funnel).find((s) => String(s.stage) === "Cancelada");
      const aging = arr(d.quotes_aging);
      const stalled = arr(d.stalled_quotes);
      return (
        <>
          <InsightStrip insights={[
            num(k.bloqueadas_monto) > 0 ? {
              metric_id: "bloqueadas_monto", severity: "warn",
              text: `${money(k.bloqueadas_monto)} en ${n0(k.bloqueadas_precio)} orden(es) detenidas por autorización de precio.`,
              drillTo: () => props.go("ventas_auth"),
            } : null as unknown as Insight,
            stalled.length > 0 && num(stalled[0].days) > 30 ? {
              metric_id: "cotizaciones_abiertas", severity: "warn",
              text: `La cotización abierta de mayor monto (${money(stalled[0].amount)}, ${String(stalled[0].partner)}) lleva ${n0(stalled[0].days)} días sin resolverse.`,
            } as Insight : null as unknown as Insight,
          ].filter(Boolean) as Insight[]} />
          <div className="cc-score">
            <Kpi id="conversion_pct" value={pct(k.conversion_pct)} />
            <Kpi id="cotizaciones_abiertas" value={n0(k.cotizaciones_abiertas)} />
            <Kpi id="bloqueadas_monto" value={money(k.bloqueadas_monto)} sub={`${n0(k.bloqueadas_precio)} órdenes`} drillTo={() => props.go("ventas_auth")} />
            <Kpi id="venta_mxn" value={money(k.venta_mxn)} sub={`${n0(k.ordenes)} órdenes confirmadas`} drillTo={() => props.go("ventas")} />
          </div>
          <div className="grid">
            <Panel title="Tasa de conversión del periodo" hint="confirmadas ÷ (confirmadas + abiertas)">
              <EChartBox height={420} deps={[k.conversion_pct]} option={{
                ...ecBase(),
                series: [{
                  type: "gauge", startAngle: 210, endAngle: -30, min: 0, max: 100,
                  progress: { show: true, width: 16, roundCap: true, itemStyle: { color: EC.blue } },
                  axisLine: { lineStyle: { width: 16, color: [[1, "rgba(100,116,139,.15)"]] } },
                  pointer: { show: false }, axisTick: { show: false },
                  splitLine: { show: false }, axisLabel: { show: false },
                  detail: { valueAnimation: true, fontSize: 34, fontWeight: 800,
                            offsetCenter: [0, "5%"], formatter: (v: number) => `${n1(v)}%`,
                            color: "inherit" },
                  data: [{ value: num(k.conversion_pct) }],
                }],
              }} />
            </Panel>
            <Panel title="Funnel de cotizaciones creadas en el periodo" hint={cancel ? `+ ${n0(cancel.count)} canceladas (${money(cancel.amount)})` : "solo etapas registradas"}>
              <EChartBox height={420} deps={[funnel]} option={(() => {
                const stages = funnel;
                const n = stages.length || 1;
                const widths = stages.map((_s2, i) => 1 - i * (0.62 / n));
                const colors = [
                  ["#a78bfa", "#7c3aed"], ["#38bdf8", "#0284c7"],
                  ["#60a5fa", "#0b57d0"], ["#34d399", "#059669"],
                ];
                const totalCount = num(stages[0]?.count) + num(stages[1]?.count) + num(stages[2]?.count);
                return {
                  ...ecBase(),
                  xAxis: { show: false, min: 0, max: 1, type: "value" },
                  yAxis: { show: false, min: 0, max: 1, type: "value", inverse: true },
                  grid: { left: 6, right: 92, top: 8, bottom: 8 },
                  tooltip: { ...(ecBase().tooltip as object),
                    formatter: (pm: { dataIndex: number }) => {
                      const st = stages[pm.dataIndex];
                      return st ? `${String(st.stage)}<br/><b>${n0(st.count)}</b> · <b>${money(st.amount)}</b>` : "";
                    } },
                  series: [{
                    type: "custom",
                    renderItem: (params: { dataIndex: number }, api: { coord: (v: [number, number]) => [number, number] }) => {
                      const i = params.dataIndex;
                      const st = stages[i];
                      const gap = 0.012;
                      const yT = i / n + gap, yB = (i + 1) / n - gap;
                      const hw = (w: number) => w * 0.5 * 0.96;
                      const wT = widths[i];
                      const wB = widths[i + 1] ?? widths[i] * 0.66;
                      const pTL = api.coord([0.5 - hw(wT), yT]);
                      const pTR = api.coord([0.5 + hw(wT), yT]);
                      const pBR = api.coord([0.5 + hw(wB), yB]);
                      const pBL = api.coord([0.5 - hw(wB), yB]);
                      const cx = (pTL[0] + pTR[0]) / 2;
                      const cy = (pTL[1] + pBL[1]) / 2;
                      const [c1, c2] = colors[i] ?? colors[colors.length - 1];
                      // % honesto: participación del total creado; entre
                      // Confirmada→Cobrado sí es tasa real (subconjunto).
                      const isCobro = String(st.stage) === "Cobrado";
                      const conf = stages.find((x) => String(x.stage) === "Confirmada");
                      const sidePct = isCobro && conf && num(conf.amount) > 0
                        ? `cobro ${pct((num(st.amount) / num(conf.amount)) * 100)}`
                        : totalCount > 0 ? `${pct((num(st.count) / totalCount) * 100)} del total` : "";
                      return {
                        type: "group",
                        children: [
                          { type: "polygon",
                            shape: { points: [pTL, pTR, pBR, pBL] },
                            style: {
                              fill: { type: "linear", x: 0, y: 0, x2: 1, y2: 0,
                                colorStops: [{ offset: 0, color: c1 }, { offset: 1, color: c2 }] },
                              shadowBlur: 10, shadowColor: "rgba(15,23,42,.22)", shadowOffsetY: 4,
                            } },
                          { type: "text",
                            style: { x: cx, y: cy - 9, text: String(st.stage).toUpperCase(),
                              textAlign: "center", fill: "#fff", fontSize: 13, fontWeight: 800,
                              fontFamily: "Inter" } },
                          { type: "text",
                            style: { x: cx, y: cy + 9, text: `${n0(st.count)} · ${money(st.amount)}`,
                              textAlign: "center", fill: "rgba(255,255,255,.92)", fontSize: 11.5,
                              fontFamily: "Inter" } },
                          { type: "text",
                            style: { x: pTR[0] + 10, y: cy, text: sidePct,
                              textAlign: "left", fill: c2, fontSize: 10.5, fontWeight: 700,
                              fontFamily: "Inter" } },
                        ],
                      };
                    },
                    data: stages.map((_s2, i) => i),
                  }],
                };
              })()} />
            </Panel>
            <Panel title="Antigüedad del backlog abierto" hint="cotizaciones vivas hoy">
              <EChartBox height={380} deps={[aging]} option={{
                ...ecBase(),
                xAxis: ecAxis("cat", aging.map((b) => String(b.bucket))),
                yAxis: ecAxis("money"),
                series: [{
                  type: "bar", barMaxWidth: 56,
                  label: { show: true, position: "top", fontWeight: 700,
                           formatter: (p: { value: number }) => money(p.value) },
                  data: aging.map((b, i) => ({
                    value: num(b.amount),
                    itemStyle: { color: [EC.green, EC.sky, EC.amber, EC.red][i] ?? EC.blue, borderRadius: [7, 7, 0, 0] },
                  })),
                }],
              }} />
            </Panel>
            <Panel title="Flujo del dinero cotizado en el periodo" hint="creadas → estado actual → cobro (montos reales)">
              <EChartBox height={380} deps={[funnel]} option={(() => {
                const stg = (name: string) => funnel.find((x) => String(x.stage) === name);
                const draft = num(stg("Borrador")?.amount);
                const sent = num(stg("Enviada")?.amount);
                const conf = num(stg("Confirmada")?.amount);
                const cobr = Math.min(num(stg("Cobrado")?.amount), conf);
                const porCobrar = Math.max(conf - cobr, 0);
                const nodes = [
                  { name: "Creadas", itemStyle: { color: ecInk().tick } },
                  { name: "Borrador", itemStyle: { color: "#7c3aed" } },
                  { name: "Enviada", itemStyle: { color: "#0284c7" } },
                  { name: "Confirmada", itemStyle: { color: "#0b57d0" } },
                  { name: "Cobrado", itemStyle: { color: "#059669" } },
                  { name: "Por cobrar", itemStyle: { color: "#d97706" } },
                ];
                const links = [
                  { source: "Creadas", target: "Borrador", value: draft },
                  { source: "Creadas", target: "Enviada", value: sent },
                  { source: "Creadas", target: "Confirmada", value: conf },
                  { source: "Confirmada", target: "Cobrado", value: cobr },
                  { source: "Confirmada", target: "Por cobrar", value: porCobrar },
                ].filter((l) => l.value > 0);
                return {
                  ...ecBase(),
                  tooltip: { ...(ecBase().tooltip as object),
                    formatter: (pm: { data: Rec; name: string }) =>
                      pm.data && (pm.data as Rec).value != null
                        ? `${String((pm.data as Rec).source)} → ${String((pm.data as Rec).target)}<br/><b>${money((pm.data as Rec).value)}</b>`
                        : String(pm.name) },
                  series: [{
                    type: "sankey", left: 10, right: 90, top: 14, bottom: 14,
                    nodeWidth: 14, nodeGap: 22,
                    data: nodes, links,
                    label: { fontSize: 12, fontWeight: 700, fontFamily: "Inter" },
                    lineStyle: { color: "gradient", opacity: 0.45, curveness: 0.5 },
                    itemStyle: { borderRadius: 4 },
                    emphasis: { focus: "adjacency" },
                  }],
                };
              })()} />
            </Panel>
            <Panel title="Cotizaciones estancadas de mayor monto" hint="abiertas hoy · mayor monto primero" wide>
              <MiniTable head={["Cotización · Cliente · Vendedor", "Monto", "Días abierta"]} rows={stalled.map((s, i) => ({
                key: i,
                a: `${String(s.name)} · ${String(s.partner)}${s.seller ? ` · ${String(s.seller)}` : ""}`,
                b: money(s.amount),
                c: <span className={num(s.days) > 30 ? "neg" : undefined}>{n0(s.days)}</span>,
              }))} />
            </Panel>
          </div>
        </>
      );
    }}</SalesSub>
  );
}

function VentasClientesView(props: { filters: Filters; drill: (n: DrillNode) => void }) {
  return (
    <SalesSub filters={props.filters}>{(k, d) => {
      const cust = arr(d.top_customers);
      const total = num(k.venta_mxn);
      const top5 = cust.slice(0, 5).reduce((s, c) => s + num(c.venta), 0);
      return (
        <>
          <InsightStrip insights={[
            total > 0 && cust.length > 0 ? {
              metric_id: "venta_mxn", severity: top5 / total > 0.6 ? "warn" : "info",
              text: `El Top 5 de clientes concentra ${pct((top5 / total) * 100)} de la venta del periodo.`,
            } as Insight : null as unknown as Insight,
          ].filter(Boolean) as Insight[]} />
          <div className="cc-score">
            <Kpi id="venta_mxn" value={money(k.venta_mxn)} drillTo={cust[0] ? () => props.drill({ kind: "entity", entity: "customer", value: num(cust[0].key), label: String(cust[0].name) }) : undefined} />
            {(d as Rec).perm_profit !== false && <Kpi id="margen_pct" value={pct(k.margen_pct)} />}
          </div>
          <div className="grid">
            <Panel title="Chord: clientes ↔ categorías" hint="grosor = venta del cruce · ECharts 6">
              <EChartBox height={420} deps={[arr(d.client_categ)]} option={(() => {
                const pairs = arr(d.client_categ);
                const custTotals = new Map<string, number>();
                const catTotals = new Map<string, number>();
                for (const pr of pairs) {
                  custTotals.set(String(pr.customer), (custTotals.get(String(pr.customer)) ?? 0) + num(pr.venta));
                  catTotals.set(String(pr.categ), (catTotals.get(String(pr.categ)) ?? 0) + num(pr.venta));
                }
                const topCust = [...custTotals.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8).map((e) => e[0]);
                const topCat = [...catTotals.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6).map((e) => e[0]);
                // Nombres de nodo ÚNICOS: recortar a 20 chars puede colisionar
                // (dos clientes que empiezan igual) y el chord truena al
                // inicializar. Mapa nombre→alias único compartido por nodos
                // y links.
                const alias = new Map<string, string>();
                const used = new Set<string>();
                const uniq = (full: string) => {
                  if (alias.has(full)) return alias.get(full)!;
                  let base = full.slice(2, 22);
                  let cand = base, k = 2;
                  while (used.has(cand)) { cand = `${base.slice(0, 17)}·${k}`; k += 1; }
                  used.add(cand); alias.set(full, cand);
                  return cand;
                };
                const custNames = topCust.map((c2) => uniq(`C:${c2}`));
                const catNames = topCat.map((c2) => uniq(`K:${c2}`));
                const links = pairs
                  .filter((pr) => topCust.includes(String(pr.customer)) && topCat.includes(String(pr.categ)) && num(pr.venta) > 0)
                  .map((pr) => ({
                    source: alias.get(`C:${String(pr.customer)}`)!,
                    target: alias.get(`K:${String(pr.categ)}`)!,
                    value: num(pr.venta),
                  }));
                const nodes = [
                  ...custNames.map((nm, i) => ({ name: nm, itemStyle: { color: ["#0b57d0", "#0ea5e9", "#7c3aed", "#0e7490", "#db2777", "#2563eb", "#4f46e5", "#0891b2"][i % 8] } })),
                  ...catNames.map((nm, i) => ({ name: nm, itemStyle: { color: ["#059669", "#d97706", "#dc2626", "#65a30d", "#ea580c", "#ca8a04"][i % 6] } })),
                ];
                if (!links.length) return { ...ecBase(), series: [] };
                return {
                  ...ecBase(),
                  tooltip: { ...(ecBase().tooltip as object),
                    formatter: (pm: { data: Rec; name: string }) =>
                      pm.data && (pm.data as Rec).value != null && (pm.data as Rec).source
                        ? `${String((pm.data as Rec).source)} ↔ ${String((pm.data as Rec).target)}<br/><b>${money((pm.data as Rec).value)}</b>`
                        : String(pm.name) },
                  series: [{
                    type: "chord",
                    data: nodes, links,
                    label: { show: true, fontSize: 10.5, fontFamily: "Inter" },
                    lineStyle: { opacity: 0.5 },
                    itemStyle: { borderRadius: 4 },
                    emphasis: { focus: "adjacency" },
                  }],
                };
              })()} />
            </Panel>
            <Panel title="Distribución de órdenes (beeswarm)" hint="cada punto = una orden · click = radiografía">
              <EChartBox height={420} deps={[arr(d.orders)]}
                onClick={(pm) => { const o = arr(d.orders)[pm.dataIndex]; if (o?.id) props.drill({ kind: "order", orderId: num(o.id), label: String(o.name) }); }}
                option={(() => {
                  const orders = arr(d.orders);
                  return {
                    ...ecBase(),
                    grid: { left: 8, right: 20, top: 16, bottom: 8, containLabel: true },
                    xAxis: { ...ecAxis("money"), name: "" },
                    yAxis: { show: false, min: -1, max: 1, type: "value" },
                    tooltip: { ...(ecBase().tooltip as object),
                      formatter: (pm: { dataIndex: number }) => {
                        const o = orders[pm.dataIndex];
                        return o ? `${String(o.name)} · ${String(o.partner ?? "")}<br/><b>${money(o.venta)}</b>` : "";
                      } },
                    series: [{
                      type: "scatter",
                      // beeswarm determinístico: dispersión vertical estable
                      data: orders.map((o, i) => [num(o.venta), ((i * 37) % 17 - 8) / 10]),
                      symbolSize: (val: [number, number]) => Math.max(8, Math.min(26, Math.sqrt(Math.abs(val[0])) / 18)),
                      itemStyle: {
                        color: { type: "radial", x: 0.4, y: 0.4, r: 1,
                          colorStops: [{ offset: 0, color: "#60a5fa" }, { offset: 1, color: "#0b57d0" }] },
                        opacity: 0.82, shadowBlur: 6, shadowColor: "rgba(11,87,208,.35)",
                      },
                    }],
                  };
                })()} />
            </Panel>
          </div>
          <Panel title="Pareto de clientes del periodo" hint="click = profundizar" wide>
            <EChartBox height={460} deps={[cust]}
              onClick={(pm) => { const c = cust[pm.dataIndex]; if (c) props.drill({ kind: "entity", entity: "customer", value: num(c.key), label: String(c.name) }); }}
              option={(() => {
                const total = cust.reduce((sm, c) => sm + num(c.venta), 0);
                let acc = 0;
                const cum = cust.map((c) => { acc += num(c.venta); return total ? Math.round((acc / total) * 1000) / 10 : 0; });
                return {
                  ...ecBase(),
                  tooltip: { ...(ecBase().tooltip as object), trigger: "axis" },
                  legend: { top: 0 },
                  xAxis: { ...ecAxis("cat", cust.map((c) => String(c.name).slice(0, 22))), axisLabel: { rotate: 32, fontSize: 10.5, color: ecInk().tick } },
                  yAxis: [ecAxis("money"), { type: "value", max: 100, splitLine: { show: false }, axisLabel: { formatter: "{value}%", fontSize: 10.5, color: ecInk().tick } }],
                  series: [
                    { name: "Venta", type: "bar", barMaxWidth: 30, data: cust.map((c) => num(c.venta)),
                      itemStyle: { color: EC.blue, borderRadius: [6, 6, 0, 0] } },
                    { name: "% acumulado", type: "line", yAxisIndex: 1, smooth: true, data: cum,
                      lineStyle: { width: 3, color: EC.amber }, itemStyle: { color: EC.amber } },
                  ],
                };
              })()} />
          </Panel>
        </>
      );
    }}</SalesSub>
  );
}

function ProductosJerarquia(props: { data: Rec }) {
  const [mode, setMode] = useState<"treemap" | "sunburst">("sunburst");
  const cats = arr(props.data.categ_products);
  if (!cats.length) return <Empty msg="Sin jerarquía de venta en el periodo." />;
  const CAT_COLORS = ["#0b57d0", "#0ea5e9", "#059669", "#d97706", "#7c3aed", "#db2777"];
  const treeData = cats.map((c2, i) => ({
    name: String(c2.categ).slice(0, 26),
    value: num(c2.total),
    itemStyle: { color: CAT_COLORS[i % CAT_COLORS.length] },
    children: arr(c2.products).map((pr) => ({
      name: String(pr.name).slice(0, 30),
      value: num(pr.venta),
    })),
  }));
  return (
    <>
      <div className="srk-toggle-row">
        <button className={mode === "sunburst" ? "on" : ""} onClick={() => setMode("sunburst")}>Sunburst</button>
        <button className={mode === "treemap" ? "on" : ""} onClick={() => setMode("treemap")}>Treemap</button>
      </div>
      <EChartBox height={440} deps={[cats, mode]} option={{
        ...ecBase(),
        tooltip: { ...(ecBase().tooltip as object),
          formatter: (pm: { name: string; value: number }) => `${pm.name}<br/><b>${money(pm.value)}</b>` },
        series: [mode === "sunburst" ? {
          type: "sunburst", radius: ["18%", "92%"],
          data: treeData,
          itemStyle: { borderRadius: 7, borderWidth: 2, borderColor: "rgba(255,255,255,.55)" },
          label: { fontSize: 10.5, fontFamily: "Inter", minAngle: 8 },
          emphasis: { focus: "ancestor" },
        } : {
          type: "treemap", roam: false, breadcrumb: { show: true, bottom: 0 },
          top: 6, bottom: 26, left: 6, right: 6,
          data: treeData,
          itemStyle: { borderColor: "rgba(255,255,255,.4)", borderWidth: 2, gapWidth: 2 },
          label: { fontSize: 11.5, fontWeight: 600 },
          upperLabel: { show: true, height: 22, fontSize: 11, fontWeight: 800, color: "#fff" },
          levels: [{}, { itemStyle: { gapWidth: 1 } }],
        }],
      }} />
    </>
  );
}

function VentasProductosView(props: { filters: Filters; drill: (n: DrillNode) => void }) {
  return (
    <SalesSub filters={props.filters}>{(k, d) => {
      const prods = arr(d.top_products);
      const cats = arr(d.by_category);
      const canProfit = (d as Rec).perm_profit !== false;
      return (
        <>
          <div className="cc-score">
            <Kpi id="venta_mxn" value={money(k.venta_mxn)} />
            <Kpi id="m2_mes" value={`${n1(k.m2_vendidos)} m²`} sub={`${n0(k.piezas_vendidas)} piezas (unidad separada)`} />
            {canProfit && <Kpi id="margen_pct" value={pct(k.margen_pct)} />}
          </div>
          <div className="grid">
            <Panel title={canProfit ? "Top productos por utilidad" : "Top productos por venta"} hint="click = profundizar">
              <EChartBox height={420} deps={[prods, canProfit]}
                onClick={(pm) => { const d0 = pm.data as Rec; if (d0?.key) props.drill({ kind: "entity", entity: "product", value: num(d0.key), label: String(d0.name) }); }}
                option={{
                  ...ecBase(),
                  tooltip: { ...(ecBase().tooltip as object),
                    formatter: (pm: { data: Rec }) => {
                      const it = pm.data ?? {};
                      const mg = it.margen == null ? "" : ` · margen ${pct(it.margen)}`;
                      return `${String(it.name)}<br/>${money(it.value)}${mg}`;
                    } },
                  series: [{
                    type: "treemap", roam: false, nodeClick: false,
                    breadcrumb: { show: false }, top: 6, bottom: 6, left: 6, right: 6,
                    label: { show: true, fontSize: 12, fontWeight: 600, formatter: (pm: { name: string }) => pm.name },
                    itemStyle: { borderColor: "rgba(255,255,255,.35)", borderWidth: 2, gapWidth: 2 },
                    data: prods.map((pr, i) => {
                      const mg = canProfit && num(pr.venta) > 0 ? (num(pr.utilidad) / num(pr.venta)) * 100 : null;
                      const color = mg == null
                        ? ["#0b57d0", "#0ea5e9", "#0e7490", "#7c3aed", "#db2777", "#d97706"][i % 6]
                        : mg < 0 ? EC.red : mg < 15 ? EC.amber : EC.green;
                      return { name: String(pr.name).slice(0, 40), value: Math.max(num(pr.venta), 1),
                               key: num(pr.key), margen: mg, itemStyle: { color } };
                    }),
                  }],
                }} />
            </Panel>
            <Panel title="Venta por categoría" hint="click = profundizar">
              <MiniTable head={["Categoría", "Venta", "m²"]} rows={cats.map((c, i) => ({
                key: i, a: String(c.name), b: money(c.venta), c: n1(c.m2),
                onClick: () => props.drill({ kind: "entity", entity: "category", value: num(c.key), label: String(c.name) }),
              }))} />
            </Panel>
            <Panel title="Estructura de la venta: categoría → producto" hint="toggle sunburst ⇄ treemap · ECharts 6" wide>
              <ProductosJerarquia data={d as Rec} />
            </Panel>
            <Panel title="Carrera de venta mensual por producto" hint="line race · top 6 productos del periodo" wide>
              <EChartBox height={340} deps={[d.product_monthly]} option={(() => {
                const pm = (d.product_monthly ?? {}) as Rec;
                return raceOpt(arr(pm.products), arr<string>(pm.months), "name");
              })()} />
            </Panel>
                        <Panel title="Red de la venta: categorías y sus productos" hint="fuerza dirigida · tamaño = venta · arrastra los nodos" wide>
              <EChartBox height={430} deps={[arr(d.categ_products)]} option={(() => {
                const catsN = arr(d.categ_products);
                if (!catsN.length) return { ...ecBase(), series: [] };
                const NET = ["#0b57d0", "#0ea5e9", "#059669", "#d97706", "#7c3aed", "#db2777"];
                const nodes: Rec[] = [];
                const links: Rec[] = [];
                const maxTot = Math.max(...catsN.map((c2) => num(c2.total)), 1);
                catsN.forEach((c2, i) => {
                  nodes.push({ name: String(c2.categ).slice(0, 24), value: num(c2.total),
                    category: i, symbolSize: 26 + Math.sqrt(num(c2.total) / maxTot) * 34,
                    label: { show: true, fontWeight: 800, fontSize: 11.5 },
                    itemStyle: { color: NET[i % 6], shadowBlur: 10, shadowColor: NET[i % 6] + "66" } });
                  arr(c2.products).forEach((pr) => {
                    nodes.push({ name: String(pr.name).slice(0, 26), value: num(pr.venta),
                      category: i, symbolSize: 10 + Math.sqrt(num(pr.venta) / maxTot) * 26,
                      label: { show: true, fontSize: 9.5 },
                      itemStyle: { color: NET[i % 6] + "cc" } });
                    links.push({ source: String(c2.categ).slice(0, 24), target: String(pr.name).slice(0, 26),
                      lineStyle: { width: 1 + (num(pr.venta) / maxTot) * 5, curveness: 0.18, opacity: 0.5,
                        color: NET[i % 6] } });
                  });
                });
                return {
                  ...ecBase(),
                  tooltip: { ...(ecBase().tooltip as object),
                    formatter: (pm: { data: Rec }) =>
                      pm.data?.name ? `${String(pm.data.name)}<br/><b>${money(pm.data.value)}</b>` : "" },
                  series: [{
                    type: "graph", layout: "force", roam: true, draggable: true,
                    data: nodes, links,
                    categories: catsN.map((c2, i) => ({ name: String(c2.categ).slice(0, 24) })),
                    force: { repulsion: 220, edgeLength: [40, 110], gravity: 0.12 },
                    labelLayout: { hideOverlap: true },
                    emphasis: { focus: "adjacency", label: { show: true } },
                  }],
                };
              })()} />
            </Panel>
                        <Panel title="Río de categorías: composición de la venta en el tiempo" hint="áreas apiladas · grosor = venta mensual de la categoría" wide>
              <EChartBox height={330} deps={[arr(d.categ_monthly)]} option={(() => {
                const cmr = arr(d.categ_monthly);
                if (!cmr.length) return { ...ecBase(), series: [] };
                const monthsR = [...new Set(cmr.map((r) => String(r.month)))].sort();
                const categsR = [...new Set(cmr.map((r) => String(r.categ)))];
                const val = new Map(cmr.map((r) => [`${r.categ}|${r.month}`, num(r.venta)]));
                const RIVER = ["#0b57d0", "#0ea5e9", "#059669", "#d97706", "#7c3aed", "#db2777"];
                return {
                  ...ecBase(),
                  legend: { top: 0, textStyle: { fontSize: 10.5 } },
                  tooltip: { ...(ecBase().tooltip as object), trigger: "axis" },
                  xAxis: ecAxis("cat", monthsR.map((m) => monthLabel(m).toUpperCase())),
                  yAxis: ecAxis("money"),
                  series: categsR.map((c2, i) => ({
                    name: c2.slice(0, 22), type: "line", stack: "rio", smooth: true,
                    symbol: "none", lineStyle: { width: 0 },
                    emphasis: { focus: "series" },
                    areaStyle: { opacity: 0.85,
                      color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1,
                        colorStops: [{ offset: 0, color: RIVER[i % 6] }, { offset: 1, color: RIVER[i % 6] + "88" }] } },
                    data: monthsR.map((m) => val.get(`${c2}|${m}`) ?? 0),
                  })),
                };
              })()} />
            </Panel>
          </div>
        </>
      );
    }}</SalesSub>
  );
}

function VentasPreciosView(props: { filters: Filters; drill: (n: DrillNode) => void }) {
  return (
    <SalesSub filters={props.filters}>{(k, d) => {
      const levels = arr(d.levels);
      const desc = num(k.descuento_mxn);
      const venta = num(k.venta_mxn);
      return (
        <>
          <InsightStrip insights={[
            venta > 0 && desc > 0 ? {
              metric_id: "descuento_mxn", severity: desc / venta > 0.08 ? "warn" : "info",
              text: `El descuento otorgado equivale a ${pct((desc / venta) * 100)} de la venta del periodo (${money(desc)}); ${n0(k.descuentos_con_auth)} pasaron por autorización.`,
            } as Insight : null as unknown as Insight,
          ].filter(Boolean) as Insight[]} />
          <div className="cc-score">
            <Kpi id="realizacion_pct" value={pct(k.realizacion_pct)} />
            <Kpi id="descuento_mxn" value={money(k.descuento_mxn)} sub={`${n0(k.descuentos_con_auth)} con autorización`} />
            <Kpi id="auth_delta_pct" value={pct(k.auth_delta_pct)} />
            <Kpi id="reincidencias_piso" value={n0(k.reincidencias_piso)} />
            {(d as Rec).perm_profit !== false && <Kpi id="margen_pct" value={pct(k.margen_pct)} />}
          </div>
          <div className="grid">
          <Panel title="Venta por nivel de precio" hint="click = profundizar">
            <EChartBox height={400} deps={[levels]}
              onClick={(pm) => { const l = levels[pm.dataIndex]; if (l) props.drill({ kind: "entity", entity: "level", value: String(l.key), label: String(l.name) }); }}
              option={{
                ...ecBase(),
                legend: { bottom: 0 },
                tooltip: { ...(ecBase().tooltip as object),
                  formatter: (pm: { name: string; value: number; percent: number }) =>
                    `${pm.name}<br/>${money(pm.value)} · ${n1(pm.percent)}%` },
                series: [{
                  type: "pie", roseType: "radius",
                  radius: ["18%", "72%"], center: ["50%", "46%"],
                  itemStyle: { borderRadius: 6, borderColor: "rgba(255,255,255,.4)", borderWidth: 2 },
                  label: { fontSize: 12, fontWeight: 600, formatter: (pm: { name: string }) => pm.name },
                  data: levels.map((l) => ({ name: String(l.name), value: Math.max(num(l.venta), 0) })),
                }],
              }} />
          </Panel>
          <Panel title="Niveles — cifras exactas" hint="la misma información, en tabla">
            <MiniTable head={["Nivel", "Venta", "m²"]} rows={levels.map((l, i) => ({
              key: i, a: String(l.name), b: money(l.venta), c: n1(l.m2),
              onClick: () => props.drill({ kind: "entity", entity: "level", value: String(l.key), label: String(l.name) }),
            }))} />
          </Panel>
          </div>
        </>
      );
    }}</SalesSub>
  );
}

function VentasAuthView(props: { filters: Filters }) {
  return (
    <SalesSub filters={props.filters}>{(k, d) => {
      const weekly = arr(d.auth_weekly);
      const pcts = (d.auth_percentiles ?? {}) as Rec;
      const fmtP = (v: unknown) => (v == null ? "—" : `${n1(v)} h`);
      return (
        <>
          <div className="cc-score">
            <Kpi id="bloqueadas_monto" value={money(k.bloqueadas_monto)} sub={`${n0(k.bloqueadas_precio)} órdenes detenidas`} />
            <Kpi id="auth_horas_resolucion" value={`${n1(k.auth_horas_resolucion)} h`}
                 sub={`mediana ${fmtP(pcts.p50)} · P75 ${fmtP(pcts.p75)} · P90 ${fmtP(pcts.p90)}`} />
            <Kpi id="auth_pendientes" value={n0(k.auth_pendientes)} sub={`${n0(k.auth_solicitudes)} solicitadas · ${n0(k.auth_aprobadas)} aprobadas`} />
            <Kpi id="auth_delta_pct" value={pct(k.auth_delta_pct)} />
          </div>
          <div className="grid">
            <Panel title="Flujo semanal: entradas vs resueltas" hint="backlog crece cuando entran más de las que se resuelven">
              <EChartBox height={380} deps={[weekly]} option={{
                ...ecBase(),
                tooltip: { ...(ecBase().tooltip as object), trigger: "axis" },
                legend: { top: 0 },
                xAxis: ecAxis("cat", weekly.map((w) => `S${String(w.week)}`)),
                yAxis: ecAxis("money"),
                series: [
                  { name: "Solicitadas", type: "bar", barMaxWidth: 24, data: weekly.map((w) => num(w.created)),
                    itemStyle: { color: EC.amber, borderRadius: [5, 5, 0, 0] } },
                  { name: "Resueltas", type: "bar", barMaxWidth: 24, data: weekly.map((w) => num(w.resolved)),
                    itemStyle: { color: EC.green, borderRadius: [5, 5, 0, 0] } },
                ],
              }} />
            </Panel>
            <Panel title="Solicitudes fiscales (IVA) — flujo separado" hint="no se mezcla con precio">
              <MiniTable head={["Concepto", "Cantidad", ""]} rows={[
                { key: "s", a: "Solicitadas", b: n0(k.iva_solicitadas), c: "" },
                { key: "a", a: "Aprobadas", b: n0(k.iva_aprobadas), c: "" },
              ]} />
            </Panel>
          </div>
        </>
      );
    }}</SalesSub>
  );
}

function VentasEquipoView(props: { filters: Filters; drill: (n: DrillNode) => void }) {
  return (
    <SalesSub filters={props.filters}>{(k, d) => {
      const sellers = arr(d.by_seller);
      const comm = arr(d.commissions);
      const canProfit = (d as Rec).perm_profit !== false;
      return (
        <>
          <div className="cc-score">
            <Kpi id="venta_mxn" value={money(k.venta_mxn)} />
            <Kpi id="comisiones_mxn" value={money(k.comisiones_mxn)}
                 sub={num(k.venta_mxn) > 0 ? `${pct((num(k.comisiones_mxn) / num(k.venta_mxn)) * 100)} de la venta` : undefined} />
            {canProfit && <Kpi id="margen_pct" value={pct(k.margen_pct)} />}
          </div>
          <div className="grid">
            {!canProfit && (
              <Panel title="Venta por vendedor" hint="click = profundizar" wide>
                <MiniTable head={["Vendedor", "Venta", "m²"]} rows={sellers.map((sr, i) => ({
                  key: i, a: String(sr.name), b: money(sr.venta), c: n1(sr.m2),
                  onClick: () => props.drill({ kind: "entity", entity: "seller", value: num(sr.key), label: String(sr.name) }),
                }))} />
              </Panel>
            )}
            {canProfit && (
            <Panel title="Venta × utilidad por vendedor" hint="click = profundizar" wide>
              <ChartBox height={420} deps={[sellers]} config={{
                type: "scatter",
                data: {
                  datasets: sellers.map((s, i) => ({
                    label: String(s.name), data: [{ x: num(s.venta), y: num(s.utilidad) }],
                    backgroundColor: PALETTE[i % PALETTE.length], pointRadius: 7, pointHoverRadius: 9,
                  })),
                },
                options: {
                  ...baseOptions(),
                  onClick: (_e: unknown, els: Array<{ datasetIndex: number }>) => {
                    if (!els.length) return;
                    const s = sellers[els[0].datasetIndex];
                    props.drill({ kind: "entity", entity: "seller", value: num(s.key), label: String(s.name) });
                  },
                  scales: { x: axisMoney(), y: axisMoney() },
                },
              }} />
            </Panel>
            )}
            {canProfit && sellers.length >= 3 && (
              <Panel title="Radar del equipo — top 6" hint="cada dimensión normalizada al máximo del grupo">
                <EChartBox height={360} deps={[sellers]} option={(() => {
                  const top = sellers.slice(0, 6);
                  const dims = [
                    { name: "Venta", key: "venta" },
                    { name: "Utilidad", key: "utilidad" },
                    { name: "m²", key: "m2" },
                  ];
                  const maxes = dims.map((dm) => Math.max(...top.map((t) => num((t as Rec)[dm.key])), 1));
                  return {
                    ...ecBase(),
                    legend: { bottom: 0, textStyle: { fontSize: 10.5 } },
                    radar: {
                      indicator: dims.map((dm, i) => ({ name: dm.name, max: maxes[i] })),
                      radius: "62%", splitNumber: 4,
                      axisName: { fontSize: 11.5, fontWeight: 700 },
                    },
                    series: [{
                      type: "radar", symbolSize: 4,
                      data: top.map((t, i) => ({
                        name: String(t.name).slice(0, 18),
                        value: dims.map((dm) => num((t as Rec)[dm.key])),
                        lineStyle: { width: 2 },
                        areaStyle: { opacity: 0.08 },
                      })),
                    }],
                  };
                })()} />
              </Panel>
            )}
            <Panel title="Carrera de venta mensual por vendedor" hint="line race · la etiqueta sigue a cada corredor · labels anti-encimado" wide>
              <EChartBox height={360} deps={[d.seller_monthly]} option={(() => {
                const sm = (d.seller_monthly ?? {}) as Rec;
                return raceOpt(arr(sm.sellers), arr<string>(sm.months), "name");
              })()} />
            </Panel>
                        <Panel title="Matrix ejecutiva del equipo" hint="tendencia mensual por vendedor · sparkline por celda" wide>
              <EChartBox height={Math.max(220, (arr((d.seller_monthly as Rec)?.sellers).length + 1) * 46)} deps={[d.seller_monthly]} option={(() => {
                const sm = (d.seller_monthly ?? {}) as Rec;
                const sellersM = arr(sm.sellers);
                const monthsM = arr<string>(sm.months);
                if (!sellersM.length) return { ...ecBase(), series: [] };
                const maxTotal = Math.max(...sellersM.map((sr) => num(sr.total)), 1);
                return {
                  ...ecBase(),
                  grid: { left: 6, right: 6, top: 6, bottom: 6 },
                  xAxis: { show: false, min: 0, max: 1, type: "value" },
                  yAxis: { show: false, min: 0, max: 1, type: "value", inverse: true },
                  tooltip: { ...(ecBase().tooltip as object),
                    formatter: (pm: { dataIndex: number }) => {
                      const sr = sellersM[pm.dataIndex];
                      if (!sr) return "";
                      const serie = arr<number>(sr.serie);
                      const lines = serie.map((v, i) => `${monthLabel(monthsM[i])}: <b>${money(v)}</b>`).join("<br/>");
                      return `<b>${String(sr.name)}</b><br/>${lines}`;
                    } },
                  series: [{
                    type: "custom",
                    data: sellersM.map((_sr, i) => i),
                    renderItem: (params: { dataIndex: number }, api: { coord: (v: [number, number]) => [number, number] }) => {
                      const i = params.dataIndex;
                      const sr = sellersM[i];
                      const nRows = sellersM.length;
                      const serie = arr<number>(sr.serie);
                      const yT = i / nRows, yB = (i + 1) / nRows;
                      const pL = api.coord([0, yT]);
                      const pR = api.coord([1, yT]);
                      const pB = api.coord([0, yB]);
                      const rowH = pB[1] - pL[1];
                      const width = pR[0] - pL[0];
                      const cy = pL[1] + rowH / 2;
                      // celdas: nombre (26%), total + barra (26%), sparkline (48%)
                      const sparkX0 = pL[0] + width * 0.54;
                      const sparkW = width * 0.44;
                      const maxSerie = Math.max(...serie, 1);
                      const pts = serie.map((v, j) => [
                        sparkX0 + (serie.length > 1 ? (j / (serie.length - 1)) * sparkW : sparkW / 2),
                        cy + rowH * 0.28 - (v / maxSerie) * rowH * 0.56,
                      ]);
                      const barW = width * 0.22 * (num(sr.total) / maxTotal);
                      const up = serie.length > 1 && serie[serie.length - 1] >= serie[serie.length - 2];
                      return {
                        type: "group",
                        children: [
                          { type: "rect",
                            shape: { x: pL[0], y: pL[1] + 3, width, height: rowH - 6, r: 10 },
                            style: { fill: i % 2 ? "rgba(100,116,139,.05)" : "rgba(100,116,139,.09)" } },
                          { type: "text",
                            style: { x: pL[0] + 14, y: cy, text: String(sr.name).slice(0, 20).toUpperCase(),
                              textVerticalAlign: "middle", fontSize: 11.5, fontWeight: 800, fontFamily: "Inter",
                              fill: ecInk().txt } },
                          { type: "rect",
                            shape: { x: pL[0] + width * 0.27, y: cy + 6, width: Math.max(barW, 2), height: 5, r: 2.5 },
                            style: { fill: "#93c5fd" } },
                          { type: "text",
                            style: { x: pL[0] + width * 0.27, y: cy - 6, text: money(sr.total),
                              textVerticalAlign: "middle", fontSize: 12, fontWeight: 750, fontFamily: "Inter",
                              fill: "#0b57d0" } },
                          { type: "polyline",
                            shape: { points: pts },
                            style: { stroke: up ? "#059669" : "#dc2626", lineWidth: 2.2, fill: "none",
                              shadowBlur: 4, shadowColor: up ? "rgba(5,150,105,.3)" : "rgba(220,38,38,.3)" } },
                          ...(pts.length ? [{ type: "circle",
                            shape: { cx: pts[pts.length - 1][0], cy: pts[pts.length - 1][1], r: 3.4 },
                            style: { fill: up ? "#059669" : "#dc2626", stroke: "#fff", lineWidth: 1.4 } }] : []),
                        ],
                      };
                    },
                  }],
                };
              })()} />
            </Panel>
            <Panel title="Comisiones del periodo" hint="commission.move · fecha plana">
              <MiniTable head={["Participante", "Comisión", ""]} rows={comm.map((c, i) => ({
                key: i, a: String(c.name), b: money(c.total ?? c.amount ?? c.venta), c: "",
              }))} />
            </Panel>
          </div>
        </>
      );
    }}</SalesSub>
  );
}

function VentasCanalesView(props: { filters: Filters }) {
  return (
    <SalesSub filters={props.filters}>{(k, d) => {
      const arch = arr(d.architects);
      return (
        <>
          <div className="cc-score">
            <Kpi id="pct_via_arquitecto" value={pct(k.pct_via_arquitecto)} sub="de las órdenes del periodo" />
            <Kpi id="venta_mxn" value={money(k.venta_mxn)} />
          </div>
          <Panel title="Top embajadores / especificadores" hint="órdenes originadas" wide>
            <MiniTable head={["Embajador", "Venta", "Órdenes"]} rows={arch.map((a, i) => ({
              key: i, a: String(a.name), b: money(a.venta), c: n0(a.count ?? a.ordenes),
            }))} />
          </Panel>
        </>
      );
    }}</SalesSub>
  );
}

function VentasFxView(props: { filters: Filters }) {
  const ex = useData(["exec"], fetchExec, { refetchInterval: 60_000 });
  const tc = ex.data ? num((ex.data as Rec).tc_banorte) : 0;
  return (
    <SalesSub filters={props.filters}>{(k) => (
      <>
        <div className="cc-score">
          <Kpi id="exposicion_usd" value={`$${n1(k.exposicion_usd)} USD`} sub={`${n0(k.exposicion_ordenes)} órdenes · ${money(k.exposicion_mxn)}`} />
          <Kpi id="fx_realizado_mxn" value={money(k.fx_realizado_mxn)} sub={`${n0(k.fx_ordenes)} órdenes cobradas`} />
          <Kpi id="tc_banorte" value={n1(tc)} />
        </div>
        <div className="grid">
          <Panel title="Sensibilidad al TC (paramétrica, no contable)" hint="efecto sobre la exposición por Δ del tipo de cambio">
            <EChartBox height={320} deps={[k.exposicion_usd]} option={(() => {
              const deltas = [-1, -0.5, -0.25, 0.25, 0.5, 1];
              return {
                ...ecBase(),
                xAxis: ecAxis("cat", deltas.map((dd) => `${dd > 0 ? "+" : ""}${dd.toFixed(2)}`)),
                yAxis: ecAxis("money"),
                series: [{
                  type: "bar", barMaxWidth: 44,
                  label: { show: true, position: "top", fontSize: 11,
                           formatter: (pm: { value: number }) => money(pm.value) },
                  data: deltas.map((dd) => ({
                    value: Math.round(num(k.exposicion_usd) * dd),
                    itemStyle: { color: dd < 0 ? EC.red : EC.green, borderRadius: dd < 0 ? [0, 0, 6, 6] : [6, 6, 0, 0] },
                  })),
                }],
              };
            })()} />
          </Panel>
          <Panel title="Escenarios — cifras exactas" hint="la misma información, en tabla">
            <MiniTable head={["Escenario", "Efecto sobre exposición", ""]} rows={[-1, -0.5, -0.25, 0.25, 0.5, 1].map((delta) => ({
              key: String(delta),
              a: `TC ${delta > 0 ? "+" : ""}${delta.toFixed(2)} MXN`,
              b: money(num(k.exposicion_usd) * delta),
              c: "",
            }))} />
          </Panel>
        </div>
      </>
    )}</SalesSub>
  );
}

function App() {
  const boot = useMemo<Rec>(() => {
    try {
      return JSON.parse(document.getElementById("som-boot")?.textContent ?? "{}");
    } catch {
      return {};
    }
  }, []);

  const initial = useMemo(readHash, []);
  // RESUMEN es exclusivo de escritorio (tablero TV): en móvil se arranca
  // en Ventas y el chip de Resumen se oculta por CSS.
  const isMobileScreen = typeof window !== "undefined" &&
    window.matchMedia && window.matchMedia("(max-width: 760px)").matches;
  // REGLA MÓVIL (definida por dirección): en teléfono NO existe Inicio
  // (ni Resumen TV ni Command Center) — toda la información arranca en
  // VENTAS; el dominio Inicio se oculta del menú por CSS.
  const [view, setView] = useState<ViewKey>(
    isMobileScreen && (initial.view === "resumen" || initial.view === "inicio")
      ? "ventas" : initial.view
  );

  // ── Navegación anidada (Fase 1 del rediseño) ──────────────────────────
  // Escape hatch: localStorage som_nav_legacy = '1' regresa a la lista
  // plana anterior sin redeploy (mecanismo de rollback de la fase).
  const navLegacy = typeof localStorage !== "undefined" && localStorage.getItem("som_nav_legacy") === "1";
  const [openDomain, setOpenDomain] = useState<string>(() => {
    const stored = typeof localStorage !== "undefined" ? localStorage.getItem("som_nav_open") : null;
    return domainOf(initial.view)?.id || stored || "inicio";
  });
  useEffect(() => {
    // La ruta activa abre automáticamente a su padre y se recuerda la
    // preferencia (un solo dominio expandido a la vez).
    const d = domainOf(view);
    if (d) setOpenDomain(d.id);
  }, [view]);
  useEffect(() => {
    try { localStorage.setItem("som_nav_open", openDomain); } catch { /* privado */ }
  }, [openDomain]);
  const crumbDomain = domainOf(view);
  const crumbPage = pageOf(view);

  // Drawer móvil del menú: con tantas páginas, los chips horizontales ya
  // no escalan — hamburguesa + panel lateral; navegar lo cierra.
  const [navOpen, setNavOpen] = useState(false);
  const goView = useCallback((v: ViewKey) => { setView(v); setNavOpen(false); }, []);
  const [filters, setFilters] = useState<Filters>({ ...defaultRange(), ...initial.filters });
  const [drillStack, setDrillStack] = useState<DrillNode[]>([]);
  const [preset, setPreset] = useState("mes");
  const [theme, setTheme] = useState<Theme>(initTheme);

  useEffect(() => writeHash(view, filters), [view, filters]);

  // GUARDIÁN móvil: Resumen es exclusivo de escritorio. Si en cualquier
  // momento (arranque tardío del webview, rotación, resize) la pantalla es
  // móvil y la vista activa es Resumen, se salta a Ventas. Cubre los casos
  // donde el chequeo inicial corre antes de que el viewport reporte su
  // ancho real.
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(max-width: 760px)");
    const enforce = () => {
      if (mq.matches) setView((v) => (v === "resumen" || v === "inicio" ? "ventas" : v));
    };
    enforce();
    mq.addEventListener?.("change", enforce);
    return () => mq.removeEventListener?.("change", enforce);
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme((t) => {
      const next: Theme = t === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      localStorage.setItem("som_theme", next);
      window.dispatchEvent(new CustomEvent("som-theme"));
      return next;
    });
  }, []);

  const applyPreset = useCallback((p: string) => {
    const to = new Date();
    const from = new Date(to);
    if (p === "hoy") { /* from = to: solo el día de hoy */ }
    else if (p === "sem") from.setDate(from.getDate() - ((from.getDay() + 6) % 7)); // lunes de esta semana
    else if (p === "mes") from.setDate(1);
    else if (p === "trim") from.setDate(from.getDate() - 90);
    else from.setFullYear(from.getFullYear() - 1);
    setPreset(p);
    setFilters((f) => ({ ...f, month: undefined, date_from: localYMD(from), date_to: localYMD(to) }));
  }, []);

  const setDate = useCallback((k: "date_from" | "date_to", v: string) => {
    if (!v) return;
    setPreset("custom");
    setFilters((f) => ({ ...f, month: undefined, [k]: v }));
  }, []);

  const drill = useCallback((n: DrillNode) => setDrillStack((s) => [...s, n]), []);
  const filtersKey = JSON.stringify(filters);

  return (
    <div className="app">
      <header className="topbar">
        <button className="nav-burger" aria-label="Abrir menú" aria-expanded={navOpen}
                onClick={() => setNavOpen((o) => !o)}>
          <span/><span/><span/>
        </button>
        <div className="brand">
          <span className="logo" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinejoin="round" strokeLinecap="round">
              <path d="M12 2.5 20.5 7v10L12 21.5 3.5 17V7Z" />
              <path d="M12 2.5V12m0 0 8.5-5M12 12l-8.5-5M12 12v9.5" opacity=".55" />
            </svg>
          </span>
          <div>
            <b>SOM Analytics</b>
            <small>{String(boot.company ?? "")} · utilidad all-in · TC Banorte real</small>
          </div>
        </div>
        <div className="presets" role="tablist" aria-label="Periodo">
          {PRESETS.map(([k, l]) => (
            <button key={k} role="tab" aria-selected={preset === k} className={preset === k ? "on" : ""} onClick={() => applyPreset(k)}>{l}</button>
          ))}
        </div>
        <div className="granul" role="tablist" aria-label="Granularidad de las series">
          {([["day", "Día"], ["week", "Semana"], ["month", "Mes"]] as Array<[string, string]>).map(([g, l]) => (
            <button key={g} role="tab"
                    aria-selected={(filters.granularity ?? "month") === g}
                    className={(filters.granularity ?? "month") === g ? "on" : ""}
                    onClick={() => setFilters((f2) => ({ ...f2, granularity: g }))}>{l}</button>
          ))}
        </div>
        <div className="dates" aria-label="Rango personalizado">
          <input type="date" value={filters.date_from ?? ""} max={filters.date_to} onChange={(e) => setDate("date_from", e.target.value)} aria-label="Desde" />
          <span>→</span>
          <input type="date" value={filters.date_to ?? ""} min={filters.date_from} onChange={(e) => setDate("date_to", e.target.value)} aria-label="Hasta" />
        </div>
        <button className="theme-btn" onClick={toggleTheme} title={theme === "dark" ? "Cambiar a tema claro" : "Cambiar a tema oscuro"} aria-label="Cambiar tema">
          {theme === "dark" ? (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round"><circle cx="12" cy="12" r="4.5" /><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.6 4.6l1.8 1.8M17.6 17.6l1.8 1.8M19.4 4.6l-1.8 1.8M6.4 17.6l-1.8 1.8" /></svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><path d="M20.5 14.5A8.5 8.5 0 0 1 9.5 3.5a8.5 8.5 0 1 0 11 11Z" /></svg>
          )}
        </button>
        <a className="back" href="/odoo" title="Volver a operaciones">
          <span className="back-l">← Volver a operaciones</span>
          <span className="back-s">←</span>
        </a>
      </header>

      <div className="body">
        {navOpen && <div className="nav-backdrop" onClick={() => setNavOpen(false)} />}
        <nav className={`sidenav${navOpen ? " open" : ""}`} aria-label="Vistas">
          {navLegacy ? (
            VIEWS.map((v) => (
              <button key={v.key} className={`nav-${v.key}${view === v.key ? " on" : ""}`} onClick={() => goView(v.key)}>{v.label}</button>
            ))
          ) : (
            NAV.map((d) => {
              const isOpen = openDomain === d.id;
              const hasActive = d.pages.some((p) => p.key === view);
              // En móvil los hijos viven siempre en el DOM (el CSS los
              // muestra como chips bajo su etiqueta de sección).
              const showChildren = isOpen || hasActive || isMobileScreen;
              return (
                <div key={d.id} className={`nav-domain nav-dom-${d.id}${hasActive ? " has-active" : ""}`}>
                  <button
                    className="nav-domain-head"
                    aria-expanded={isOpen}
                    onClick={() => setOpenDomain(d.id)}
                  >
                    <span className="nav-domain-label">{d.label}</span>
                    <span className="nav-chevron" aria-hidden="true">{isOpen ? "▾" : "▸"}</span>
                  </button>
                  {showChildren && (
                    <div className="nav-children">
                      {d.pages.map((p) => (
                        <button
                          key={p.key}
                          className={`nav-${p.key}${view === p.key ? " on" : ""}`}
                          aria-current={view === p.key ? "page" : undefined}
                          title={p.question}
                          onClick={() => { goView(p.key as ViewKey); setOpenDomain(d.id); }}
                        >
                          {p.label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              );
            })
          )}
          <div className="navfoot">{String(boot.user ?? "")}</div>
        </nav>

        <main className="content" key={view + filtersKey}>
          {!navLegacy && crumbDomain && crumbPage && (
            <div className="crumbs" aria-label="Ruta">
              <span>Analytics</span>
              <span className="crumb-sep">/</span>
              <span>{crumbDomain.label}</span>
              <span className="crumb-sep">/</span>
              <strong>{crumbPage.label}</strong>
              <span className="crumb-q">{crumbPage.question}</span>
            </div>
          )}
          {!navLegacy && view !== "resumen" && crumbDomain && (
            <VitalBar domainId={crumbDomain.id} goHome={() => setView("inicio")} />
          )}
          {view === "inicio" && <CommandCenterView filters={filters} drill={drill} go={setView} />}
          {view === "ventas_conversion" && <VentasConversionView filters={filters} go={setView} />}
          {view === "ventas_clientes" && <VentasClientesView filters={filters} drill={drill} />}
          {view === "ventas_productos" && <VentasProductosView filters={filters} drill={drill} />}
          {view === "ventas_precios" && <VentasPreciosView filters={filters} drill={drill} />}
          {view === "ventas_auth" && <VentasAuthView filters={filters} />}
          {view === "ventas_equipo" && <VentasEquipoView filters={filters} drill={drill} />}
          {view === "ventas_canales" && <VentasCanalesView filters={filters} />}
          {view === "ventas_fx" && <VentasFxView filters={filters} />}
          {view === "resumen" && <ResumenView filters={filters} paused={drillStack.length > 0} />}
          {view === "ventas" && <VentasView filters={filters} drill={drill} />}
          {view === "materiales" && <MaterialesView filters={filters} drill={drill} />}
          {view === "inventario" && <InventarioView filters={filters} drill={drill} />}
          {view === "compras" && <ComprasView filters={filters} />}
          {view === "transito" && <TransitoView />}
          {view === "recepciones" && <RecepcionesView filters={filters} />}
          {view === "taller" && <TallerView filters={filters} />}
          {view === "entregas" && <EntregasView filters={filters} />}
          {view === "finanzas" && <FinanzasView filters={filters} drill={drill} />}
          {view === "pronosticos" && <PronosticosView />}
          {view === "control" && <ControlView filters={filters} />}
        </main>
      </div>

      {drillStack.length > 0 && (
        <DrillPanel
          stack={drillStack}
          filters={filters}
          push={drill}
          popTo={(i) => setDrillStack((s) => s.slice(0, i + 1))}
          close={() => setDrillStack([])}
        />
      )}
    </div>
  );
}

const queryClient = new QueryClient();

const rootEl = document.getElementById("som-root");
if (rootEl) {
  createRoot(rootEl).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </StrictMode>,
  );
}
