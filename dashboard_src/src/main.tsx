// SOM Dashboard Ejecutivo — bundle React standalone (patrón portal proveedores).
// Tres niveles de lectura: titular (Resumen/pantalla de dirección) → contexto
// (vistas por dominio) → explicación (drill con breadcrumbs).
import { StrictMode, useCallback, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import {
  arr, num, money, n0, n1, pct, monthLabel, marginTone, Rec, rpc,
  fetchExec, fetchBanks, fetchOrderLines, fetchTimeToSell,
  fetchDashboard, fetchDrill,
} from "./api";
import { ChartBox, baseOptions, axisMoney, axisPlain, C, PALETTE } from "./charts";
import { NAV, domainOf, pageOf } from "./nav";
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

type Filters = { date_from?: string; date_to?: string; month?: string; categ_id?: number; user_id?: number; partner_id?: number; product_id?: number };

function readHash(): { view: ViewKey; filters: Filters } {
  const p = new URLSearchParams(window.location.hash.slice(1));
  const view = (p.get("view") as ViewKey) || "inicio";
  const filters: Filters = {};
  for (const k of ["date_from", "date_to", "month"] as const) {
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
      <TvStat label="TC Banorte" value={n1(d.tc_banorte)} sub={`Autorizaciones pendientes: ${n0(d.auth_pendientes)}`} />
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
            <ChartBox height={360} deps={[slow]} config={(() => {
              const base = baseOptions((i) => {
                const r = slow[i];
                props.drill({ kind: "entity", entity: "product", value: num(r.tmpl_id), label: String(r.name) });
              });
              return {
                type: "bar",
                data: {
                  labels: slow.map((r) => String(r.name).slice(0, 42)),
                  datasets: [{
                    label: "Tiempo en patio",
                    data: slow.map((r) => num(r.edad_stock)),
                    backgroundColor: slow.map((r) =>
                      num(r.edad_stock) > 730 ? "rgba(220,38,38,.85)"
                      : num(r.edad_stock) > 365 ? "rgba(234,88,12,.85)"
                      : "rgba(217,119,6,.7)"),
                    borderRadius: 5, maxBarThickness: 20,
                  }],
                },
                options: {
                  ...base,
                  indexAxis: "y",
                  plugins: {
                    ...base.plugins,
                    legend: { display: false },
                    tooltip: {
                      ...base.plugins.tooltip,
                      callbacks: {
                        label: (ctx: any) => {
                          const r = slow[ctx.dataIndex];
                          return ` ${fmtAge(num(r.edad_stock))} en patio · ${n1(num(r.m2_stock))} m² detenidos · ${n0(num(r.lots_stock))} lotes`;
                        },
                      },
                    },
                  },
                  scales: {
                    // Rejilla por AÑOS (365 d) con etiquetas "1 a", "2 a"…
                    x: { ...axisMoney(), ticks: { ...axisMoney().ticks, stepSize: 365, callback: (v: number) => fmtAge(Number(v)) } },
                    y: axisPlain(10.5),
                  },
                },
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
          <ChartBox height={280} deps={[st]} config={{
            type: "bar",
            data: {
              labels: st.map((r) => String(r.label)),
              datasets: [
                { label: "m²", data: st.map((r) => num(r.m2)), backgroundColor: "rgba(2,132,199,.8)", borderRadius: 6, maxBarThickness: 44 },
                { type: "line", label: "Contenedores", data: st.map((r) => num(r.count)), borderColor: C.amber, borderWidth: 2.5, pointRadius: 4, tension: 0.3, yAxisID: "y1" },
              ],
            },
            options: { ...baseOptions(), interaction: { mode: "index", intersect: false }, scales: { y: axisMoney(), y1: { beginAtZero: true, position: "right", grid: { display: false }, border: { display: false }, ticks: { color: C.amber, font: { size: 11 } } }, x: axisPlain(10.5) } },
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
          <ChartBox height={280} deps={[arr(d.eta_months)]} config={{
            type: "bar",
            data: {
              labels: arr(d.eta_months).map((r) => monthLabel(r.key)),
              datasets: [
                { label: "m²", data: arr(d.eta_months).map((r) => num(r.m2)), backgroundColor: "rgba(2,132,199,.8)", borderRadius: 6, maxBarThickness: 40 },
                { type: "line", label: "Contenedores", data: arr(d.eta_months).map((r) => num(r.count)), borderColor: C.amber, borderWidth: 2.5, pointRadius: 4, tension: 0.3, yAxisID: "y1" },
              ],
            },
            options: { ...baseOptions(), interaction: { mode: "index", intersect: false }, scales: { y: axisMoney(), y1: { beginAtZero: true, position: "right", grid: { display: false }, border: { display: false }, ticks: { color: C.amber, font: { size: 11 } } }, x: axisPlain(11) } },
          }} />
        </Panel>
        <Panel title="Lo que ya llegó: m² recibidos por mes (12 meses)" wide>
          <ChartBox height={260} deps={[arr(d.arrived_monthly)]} config={{
            type: "bar",
            data: { labels: arr(d.arrived_monthly).map((r) => monthLabel(r.key)), datasets: [{ label: "m²", data: arr(d.arrived_monthly).map((r) => num(r.m2)), backgroundColor: "rgba(5,150,105,.75)", borderRadius: 6, maxBarThickness: 36 }] },
            options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(11) } },
          }} />
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

      <div className="grid" style={{ marginTop: 12 }}>
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

        <Panel title="Flujo por vencimiento: qué entra vs qué sale" hint="cobros y pagos según cuándo vencen" wide>
          <ChartBox height={300} deps={[due]} config={{
            type: "bar",
            data: {
              labels: due.map((r) => String(r.bucket)),
              datasets: [
                { label: "Entra (por cobrar)", data: due.map((r) => num(r.entra)), backgroundColor: "rgba(5,150,105,.8)", borderRadius: 6, maxBarThickness: 46, isMoney: true },
                { label: "Sale (por pagar)", data: due.map((r) => num(r.sale)), backgroundColor: "rgba(220,38,38,.75)", borderRadius: 6, maxBarThickness: 46, isMoney: true },
              ],
            },
            options: { ...baseOptions(), interaction: { mode: "index", intersect: false }, scales: { y: axisMoney(), x: axisPlain(12) } },
          }} />
        </Panel>

        <Panel title="Por cobrar por antigüedad" hint="edad de lo que me deben">
          <ChartBox height={260} deps={[arb]} config={{
            type: "bar",
            data: { labels: arb.map((r) => String(r.bucket)), datasets: [{ label: "MXN", data: arb.map((r) => num(r.monto)), backgroundColor: [C.green, "#84cc16", C.amber, "#f97316", C.red], borderRadius: 6, isMoney: true }] },
            options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(11) } },
          }} />
        </Panel>
        <Panel title="Por pagar por antigüedad" hint="edad de lo que debo">
          <ChartBox height={260} deps={[apb]} config={{
            type: "bar",
            data: { labels: apb.map((r) => String(r.bucket)), datasets: [{ label: "MXN", data: apb.map((r) => num(r.monto)), backgroundColor: [C.sky, "#818cf8", C.violet, "#f472b6", C.red], borderRadius: 6, isMoney: true }] },
            options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(11) } },
          }} />
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

// ═════════ COMMAND CENTER (portada universal, responsivo) ═════════
function CommandCenterView(props: { filters: Filters; drill: (n: DrillNode) => void; go: (v: ViewKey) => void }) {
  const ex = useData(["exec"], fetchExec, { refetchInterval: 60_000 });
  const rz = useData(["dashboard", "resumen", props.filters], () => fetchDashboard("resumen", props.filters as Rec), { refetchInterval: TV_REFRESH_MS });
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
    { id: "tc_banorte", value: n1(d.tc_banorte) },
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
          <Panel title="Venta y utilidad por mes" hint="pack Resumen · 5 min" wide>
            <ChartBox height={340} deps={[months]} config={{
              type: "bar",
              data: {
                labels: months.map((r) => monthLabel(String(r.key))),
                datasets: [
                  { label: "Venta", data: months.map((r) => num(r.venta)), backgroundColor: "rgba(11,87,208,.85)", borderRadius: 5, maxBarThickness: 34, isMoney: true },
                  ...(canProfit ? [{ label: "Utilidad", data: months.map((r) => num(r.utilidad)), backgroundColor: "rgba(5,150,105,.8)", borderRadius: 5, maxBarThickness: 34, isMoney: true }] : []),
                ],
              },
              options: { ...baseOptions(), scales: { y: axisMoney(), x: axisPlain(12) } },
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
            <Kpi id="venta_mxn" value={money(k.venta_mxn)} sub={`${n0(k.ordenes)} órdenes confirmadas`} />
          </div>
          <div className="grid">
            <Panel title="Funnel de cotizaciones creadas en el periodo" hint={cancel ? `+ ${n0(cancel.count)} canceladas (${money(cancel.amount)})` : "solo etapas registradas"} wide>
              <ChartBox height={380} deps={[funnel]} config={{
                type: "bar",
                data: {
                  labels: funnel.map((s) => `${s.stage} (${n0(s.count)})`),
                  datasets: [{ label: "Monto", data: funnel.map((s) => num(s.amount)), backgroundColor: ["#94a3b8", "#0ea5e9", "#0b57d0"], borderRadius: 6, maxBarThickness: 46, isMoney: true }],
                },
                options: { ...baseOptions(), indexAxis: "y", plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { x: axisMoney(), y: axisPlain(12) } },
              }} />
            </Panel>
            <Panel title="Antigüedad del backlog abierto" hint="cotizaciones vivas hoy">
              <ChartBox height={380} deps={[aging]} config={{
                type: "bar",
                data: {
                  labels: aging.map((b) => String(b.bucket)),
                  datasets: [{ label: "Monto abierto", data: aging.map((b) => num(b.amount)), backgroundColor: ["#059669", "#0ea5e9", "#d97706", "#dc2626"], borderRadius: 6, maxBarThickness: 40, isMoney: true }],
                },
                options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(11) } },
              }} />
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
            <Kpi id="venta_mxn" value={money(k.venta_mxn)} />
            {(d as Rec).perm_profit !== false && <Kpi id="margen_pct" value={pct(k.margen_pct)} />}
          </div>
          <Panel title="Pareto de clientes del periodo" hint="click = profundizar" wide>
            <ChartBox height={460} deps={[cust]} config={{
              type: "bar",
              data: {
                labels: cust.map((c) => String(c.name).slice(0, 34)),
                datasets: [{ label: "Venta", data: cust.map((c) => num(c.venta)), backgroundColor: "rgba(11,87,208,.85)", borderRadius: 5, maxBarThickness: 20, isMoney: true }],
              },
              options: {
                ...baseOptions((i) => { const c = cust[i]; props.drill({ kind: "entity", entity: "customer", value: num(c.key), label: String(c.name) }); }),
                indexAxis: "y", plugins: { ...baseOptions().plugins, legend: { display: false } },
                scales: { x: axisMoney(), y: axisPlain(10.5) },
              },
            }} />
          </Panel>
        </>
      );
    }}</SalesSub>
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
            <Panel title={canProfit ? "Top productos por utilidad" : "Top productos por venta"} hint="click = profundizar" wide>
              <ChartBox height={420} deps={[prods, canProfit]} config={{
                type: "bar",
                data: {
                  labels: prods.map((p) => String(p.name).slice(0, 34)),
                  datasets: [canProfit
                    ? { label: "Utilidad", data: prods.map((p) => num(p.utilidad)), backgroundColor: "rgba(5,150,105,.8)", borderRadius: 5, maxBarThickness: 18, isMoney: true }
                    : { label: "Venta", data: prods.map((p) => num(p.venta)), backgroundColor: "rgba(11,87,208,.85)", borderRadius: 5, maxBarThickness: 18, isMoney: true }],
                },
                options: {
                  ...baseOptions((i) => { const p = prods[i]; props.drill({ kind: "entity", entity: "product", value: num(p.key), label: String(p.name) }); }),
                  indexAxis: "y", plugins: { ...baseOptions().plugins, legend: { display: false } },
                  scales: { x: axisMoney(), y: axisPlain(10.5) },
                },
              }} />
            </Panel>
            <Panel title="Venta por categoría" hint="click = profundizar">
              <MiniTable head={["Categoría", "Venta", "m²"]} rows={cats.map((c, i) => ({
                key: i, a: String(c.name), b: money(c.venta), c: n1(c.m2),
                onClick: () => props.drill({ kind: "entity", entity: "category", value: num(c.key), label: String(c.name) }),
              }))} />
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
          <Panel title="Venta por nivel de precio" hint="click = profundizar" wide>
            <ChartBox height={400} deps={[levels]} config={{
              type: "bar",
              data: {
                labels: levels.map((l) => String(l.name)),
                datasets: [{ label: "Venta", data: levels.map((l) => num(l.venta)), backgroundColor: PALETTE, borderRadius: 6, maxBarThickness: 46, isMoney: true }],
              },
              options: {
                ...baseOptions((i) => { const l = levels[i]; props.drill({ kind: "entity", entity: "level", value: String(l.key), label: String(l.name) }); }),
                plugins: { ...baseOptions().plugins, legend: { display: false } },
                scales: { y: axisMoney(), x: axisPlain(12) },
              },
            }} />
          </Panel>
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
            <Panel title="Flujo semanal: entradas vs resueltas" hint="backlog crece cuando entran más de las que se resuelven" wide>
              <ChartBox height={380} deps={[weekly]} config={{
                type: "bar",
                data: {
                  labels: weekly.map((w) => `S${String(w.week)}`),
                  datasets: [
                    { label: "Solicitadas", data: weekly.map((w) => num(w.created)), backgroundColor: "rgba(217,119,6,.8)", borderRadius: 5, maxBarThickness: 26 },
                    { label: "Resueltas", data: weekly.map((w) => num(w.resolved)), backgroundColor: "rgba(5,150,105,.8)", borderRadius: 5, maxBarThickness: 26 },
                  ],
                },
                options: { ...baseOptions(), interaction: { mode: "index", intersect: false }, scales: { y: axisMoney(), x: axisPlain(11) } },
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
        <Panel title="Sensibilidad simple (parámetros visibles, no contable)" hint="exposición × Δ TC">
          <MiniTable head={["Escenario", "Efecto sobre exposición", ""]} rows={[-1, -0.5, 0.5, 1].map((delta) => ({
            key: String(delta),
            a: `TC ${delta > 0 ? "+" : ""}${delta.toFixed(2)} MXN`,
            b: money(num(k.exposicion_usd) * delta),
            c: "",
          }))} />
        </Panel>
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
  // El Resumen TV es exclusivo de escritorio; en móvil su equivalente es
  // el Command Center responsivo (portada universal desde Fase 2).
  const [view, setView] = useState<ViewKey>(
    isMobileScreen && initial.view === "resumen" ? "inicio" : initial.view
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
      if (mq.matches) setView((v) => (v === "resumen" ? "inicio" : v));
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
        <nav className="sidenav" aria-label="Vistas">
          {navLegacy ? (
            VIEWS.map((v) => (
              <button key={v.key} className={`nav-${v.key}${view === v.key ? " on" : ""}`} onClick={() => setView(v.key)}>{v.label}</button>
            ))
          ) : (
            NAV.map((d) => {
              const isOpen = openDomain === d.id;
              const hasActive = d.pages.some((p) => p.key === view);
              // En móvil los hijos viven siempre en el DOM (el CSS los
              // muestra como chips bajo su etiqueta de sección).
              const showChildren = isOpen || hasActive || isMobileScreen;
              return (
                <div key={d.id} className={`nav-domain${hasActive ? " has-active" : ""}`}>
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
                          onClick={() => { setView(p.key as ViewKey); setOpenDomain(d.id); }}
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
