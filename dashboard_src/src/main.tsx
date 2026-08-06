// SOM Dashboard Ejecutivo — bundle React standalone (patrón portal proveedores).
// Tres niveles de lectura: titular (Resumen/pantalla de dirección) → contexto
// (vistas por dominio) → explicación (drill con breadcrumbs).
import { StrictMode, useCallback, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import {
  arr, num, money, n0, n1, pct, monthLabel, marginTone, Rec,
  fetchExec, fetchBanks, fetchOrderLines, fetchTimeToSell,
  fetchDashboard, fetchDrill,
} from "./api";
import { ChartBox, baseOptions, axisMoney, axisPlain, C, PALETTE } from "./charts";
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
  | "resumen" | "ventas" | "materiales" | "inventario" | "compras"
  | "transito" | "recepciones" | "taller" | "entregas" | "finanzas" | "control";

const VIEWS: Array<{ key: ViewKey; label: string }> = [
  { key: "resumen", label: "Resumen" },
  { key: "ventas", label: "Ventas" },
  { key: "materiales", label: "Materiales" },
  { key: "inventario", label: "Inventario" },
  { key: "compras", label: "Compras" },
  { key: "transito", label: "Tránsito" },
  { key: "recepciones", label: "Recepciones" },
  { key: "taller", label: "Taller" },
  { key: "entregas", label: "Entregas" },
  { key: "finanzas", label: "Finanzas" },
  { key: "control", label: "Control" },
];

type Filters = { date_from?: string; date_to?: string; month?: string; categ_id?: number; user_id?: number; partner_id?: number; product_id?: number };

function readHash(): { view: ViewKey; filters: Filters } {
  const p = new URLSearchParams(window.location.hash.slice(1));
  const view = (p.get("view") as ViewKey) || "resumen";
  const filters: Filters = {};
  for (const k of ["date_from", "date_to", "month"] as const) {
    const v = p.get(k);
    if (v) filters[k] = v;
  }
  for (const k of ["categ_id", "user_id", "partner_id", "product_id"] as const) {
    const v = p.get(k);
    if (v) filters[k] = parseInt(v, 10);
  }
  return { view: VIEWS.some((x) => x.key === view) ? view : "resumen", filters };
}

function writeHash(view: ViewKey, filters: Filters) {
  const p = new URLSearchParams();
  p.set("view", view);
  Object.entries(filters).forEach(([k, v]) => v != null && p.set(k, String(v)));
  history.replaceState(null, "", "#" + p.toString());
}

function defaultRange(): Filters {
  const to = new Date();
  const from = new Date(to);
  from.setFullYear(from.getFullYear() - 1);
  return { date_from: from.toISOString().slice(0, 10), date_to: to.toISOString().slice(0, 10) };
}

// ─────────────────────────────────────────────────────────────────────────────
// Drill: pila navegable con breadcrumbs
// ─────────────────────────────────────────────────────────────────────────────
type DrillNode =
  | { kind: "entity"; entity: "month" | "seller" | "customer" | "product" | "category" | "level"; value: string | number; label: string }
  | { kind: "order"; orderId: number; label: string };

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
const TV_ROTATE_MS = 90_000;
const TV_REFRESH_MS = 300_000;

function TvStat(props: { label: string; value: string; sub?: string; tone?: "good" | "bad" | "mid" | "" }) {
  return (
    <div className={"tv-stat " + (props.tone ?? "")}>
      <div className="l">{props.label}</div>
      <div className="v">{props.value}</div>
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
      <TvStat label="Venta de hoy" value={money(d.venta_hoy)} />
      <TvStat label="Venta del mes" value={money(d.venta_mes)}
        sub={`${mom >= 0 ? "▲" : "▼"} ${pct(Math.abs(mom))} vs mes anterior`} tone={mom >= 0 ? "good" : "bad"} />
      <TvStat label="Utilidad del mes" value={money(d.utilidad_mes)} sub={`Margen ${pct(d.margen_mes)}`} tone={marginTone(d.margen_mes)} />
      <TvStat label="m² vendidos del mes" value={n1(d.m2_mes)} />
      <TvStat label="Dinero en bancos" value={money(d.bancos_mxn)} tone="good" />
      <TvStat label="Me deben" value={money(d.por_cobrar)} />
      <TvStat label="Debo" value={money(d.por_pagar)} tone="bad" />
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
  const aging = arr(d.aging);
  return (
    <>
      <div className="tv-stats">
        <TvStat label="Disponible" value={`${n1(k.disponible_m2)} m²`} sub={`${n0(k.lotes)} lotes`} />
        <TvStat label="En hold" value={`${n1(k.hold_m2)} m²`} sub={`${n0(k.holds_activos)} apartados`} />
        <TvStat label="Valor inmovilizado" value={money(k.valor_mxn)} />
        <TvStat label="Rotación 12 meses" value={`${n1(k.rotacion)}x`} sub={`${n1(k.meses_inventario)} meses de inventario`} tone={num(k.rotacion) < 2 ? "bad" : "good"} />
      </div>
      <Panel title="Antigüedad del inventario (regla Stone Profit)" wide>
        <ChartBox height={340} deps={[aging]} config={{
          type: "bar",
          data: { labels: aging.map((r) => String(r.bucket)), datasets: [{ label: "m²", data: aging.map((r) => num(r.m2)), backgroundColor: [C.green, C.sky, C.amber, C.red, "#64748b"], borderRadius: 8 }] },
          options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(13) } },
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
  const q = useData(["dashboard", "comercial", props.filters], () => fetchDashboard("comercial", props.filters as Rec));
  if (q.loading) return <div className="grid"><Skeleton h={90} /><Skeleton /><Skeleton /><Skeleton /></div>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const d = q.data!;
  const k = (d.kpis ?? {}) as Rec;
  const cats = arr(d.by_category);
  const sellers = arr(d.by_seller);
  const products = arr(d.top_products);
  const customers = arr(d.top_customers);
  return (
    <>
      <div className="stats">
        <Stat label="Venta" value={money(k.venta_mxn)} sub={`${n0(k.ordenes)} órdenes`} />
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
function MaterialesView(props: { filters: Filters; drill: (n: DrillNode) => void }) {
  const q = useData(["time_to_sell"], fetchTimeToSell);
  const inv = useData(["dashboard", "inventario", props.filters], () => fetchDashboard("inventario", props.filters as Rec));
  if (q.loading) return <div className="grid"><Skeleton h={90} /><Skeleton h={480} /></div>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const rows = arr(q.data);
  const slow = rows.filter((r) => r.edad_stock != null).slice(0, 14);
  const topStock = inv.data ? arr(inv.data.top_stock) : [];
  return (
    <>
      <div className="stats">
        <Stat label="Materiales analizados" value={n0(rows.length)} sub="ventas 12 meses + stock actual" />
        <Stat label="Más lento en patio" value={slow[0] ? `${n1(slow[0].edad_stock)} días` : "—"} sub={slow[0] ? String(slow[0].name).slice(0, 40) : ""} tone="bad" />
        <Stat label="m² en stock (analizados)" value={n1(rows.reduce((s, r) => s + num(r.m2_stock), 0))} />
        <Stat label="m² vendidos 12 meses" value={n1(rows.reduce((s, r) => s + num(r.m2_vendidos), 0))} />
      </div>
      {!rows.length && (
        <Empty msg="Sin datos de rotación: no hay lotes de material (m²) con movimientos a cliente en los últimos 12 meses ni stock actual con lote. Verifica que los productos de placa usen unidad de medida de área." />
      )}
      <div className="grid">
        {slow.length > 0 && (
          <Panel title="Capital estancado: edad del stock por material" hint="click = profundizar" wide>
            <ChartBox height={360} deps={[slow]} config={{
              type: "bar",
              data: {
                labels: slow.map((r) => String(r.name).slice(0, 42)),
                datasets: [{ label: "Días en patio (stock actual)", data: slow.map((r) => num(r.edad_stock)), backgroundColor: slow.map((r) => (num(r.edad_stock) > 365 ? "rgba(220,38,38,.8)" : "rgba(217,119,6,.8)")), borderRadius: 5, maxBarThickness: 18 }],
              },
              options: {
                ...baseOptions((i) => {
                  const r = slow[i];
                  props.drill({ kind: "entity", entity: "product", value: num(r.tmpl_id), label: String(r.name) });
                }),
                indexAxis: "y",
                plugins: { ...baseOptions().plugins, legend: { display: false } },
                scales: { x: axisMoney(), y: axisPlain(10.5) },
              },
            }} />
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
                    <th className="r">Edad stock (días)</th><th className="r">m² en stock</th><th className="r">Lotes stock</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={String(r.tmpl_id)} className="click" onClick={() => props.drill({ kind: "entity", entity: "product", value: num(r.tmpl_id), label: String(r.name) })}>
                      <td className="ell">{String(r.name)}</td>
                      <td className="r">{r.dias_venta == null ? "—" : n1(r.dias_venta)}</td>
                      <td className="r">{n1(r.m2_vendidos)}</td>
                      <td className={"r " + (num(r.edad_stock) > 365 ? "neg" : "")}>{r.edad_stock == null ? "—" : n1(r.edad_stock)}</td>
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
        <Stat label="En hold" value={`${n1(k.hold_m2)} m²`} sub={`${n0(k.holds_activos)} apartados · ${n1(k.holds_edad_dias)} días prom.`} />
        <Stat label="Valor all-in inmovilizado" value={money(k.valor_mxn)} />
        <Stat label="Rotación 12m" value={`${n1(k.rotacion)}x`} sub={`${n1(k.meses_inventario)} meses de inventario`} tone={num(k.rotacion) < 2 ? "bad" : "good"} />
        <Stat label="Committed en OV" value={`${n1(k.committed_m2)} m²`} />
        <Stat label="Conversión de holds" value={pct(k.holds_conversion_pct)} sub={`${n0(k.reservas_desplazadas)} reservas desplazadas`} />
        <Stat label="Bloques rotos" value={n0(k.bloques_rotos)} sub={`${n1(k.bloques_rotos_m2)} m² afectados de ${n0(k.bloques_activos)} bloques`} tone={num(k.bloques_rotos) > 0 ? "mid" : "good"} />
        <Stat label="Bloques con foto" value={pct(k.bloques_con_foto_pct)} sub={`${n1(k.m2_sin_foto)} m² invisibles`} tone={num(k.bloques_con_foto_pct) < 70 ? "bad" : "good"} />
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
        <Panel title="Antigüedad (regla Stone Profit)">
          <ChartBox height={340} deps={[aging]} config={{
            type: "bar",
            data: { labels: aging.map((r) => String(r.bucket)), datasets: [{ label: "m²", data: aging.map((r) => num(r.m2)), backgroundColor: [C.green, C.sky, C.amber, C.red, "#64748b"], borderRadius: 6 }] },
            options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(10) } },
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
function FinanzasView(props: { filters: Filters }) {
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
  return (
    <>
      <div className="stats">
        <Stat label="ME DEBEN" value={money(k.por_cobrar)} sub={`${n0(k.clientes_deudores)} clientes con saldo`} tone="good" />
        <Stat label="DEBO" value={money(k.por_pagar)} tone="bad" />
        <Stat label="Posición neta" value={money(k.neto)} tone={num(k.neto) >= 0 ? "good" : "bad"} />
        <Stat label="Dinero en bancos y cajas" value={banks.data ? money(banks.data.total) : "…"} tone="good" />
        <Stat label="Efectivo sin aplicar" value={money(k.efectivo_sin_aplicar)} sub={`${n0(k.recibos_sin_aplicar)} recibos entregados`} tone={num(k.efectivo_sin_aplicar) > 0 ? "mid" : ""} />
        <Stat label="Efectivo aplicado" value={money(k.efectivo_aplicado)} />
        <Stat label="Comprobantes por validar" value={n0(k.comprobantes_pendientes)} sub={money(k.comprobantes_monto)} tone={num(k.comprobantes_pendientes) > 0 ? "mid" : "good"} />
      </div>
      {!banks.loading && !banks.error && banks.data && banks.data.journals.length > 0 && (
        <div className="stats">
          {banks.data.journals.map((j) => (
            <Stat key={String(j.id)} label={String(j.name)} value={money(j.balance)} sub={j.type === "cash" ? "caja" : "banco"} />
          ))}
        </div>
      )}
      <div className="grid">
        <Panel title="Por cobrar por antigüedad" hint="lo que me deben, por edad de la deuda">
          <ChartBox height={260} deps={[arb]} config={{
            type: "bar",
            data: { labels: arb.map((r) => String(r.bucket)), datasets: [{ label: "MXN", data: arb.map((r) => num(r.monto)), backgroundColor: [C.green, "#84cc16", C.amber, "#f97316", C.red], borderRadius: 6, isMoney: true }] },
            options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(11) } },
          }} />
        </Panel>
        <Panel title="Por pagar por antigüedad" hint="lo que yo debo, por edad">
          <ChartBox height={260} deps={[apb]} config={{
            type: "bar",
            data: { labels: apb.map((r) => String(r.bucket)), datasets: [{ label: "MXN", data: apb.map((r) => num(r.monto)), backgroundColor: [C.sky, "#818cf8", C.violet, "#f472b6", C.red], borderRadius: 6, isMoney: true }] },
            options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(11) } },
          }} />
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
        <Panel title="Quién me debe" hint="saldo vivo por cliente">
          {!arr(d.ar_top).length ? <Empty msg="Nadie me debe" /> : (
            <div className="tablewrap">
              <table>
                <thead><tr><th>Cliente</th><th className="r">Saldo</th><th className="r">Facturas</th><th>Vence desde</th></tr></thead>
                <tbody>
                  {arr(d.ar_top).map((c) => (
                    <tr key={String(c.key)}>
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
        <Panel title="A quién le debo" hint="saldo vivo por proveedor">
          {!arr(d.ap_top).length ? <Empty msg="No debo nada" /> : (
            <div className="tablewrap">
              <table>
                <thead><tr><th>Proveedor</th><th className="r">Saldo</th><th className="r">Facturas</th><th>Vence desde</th></tr></thead>
                <tbody>
                  {arr(d.ap_top).map((p) => (
                    <tr key={String(p.key)}>
                      <td className="ell">{String(p.name)}</td>
                      <td className="r strong">{money(p.monto)}</td>
                      <td className="r mut">{n0(p.facturas)}</td>
                      <td className="mut">{String(p.oldest ?? "")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        {pago.length > 0 && (
          <Panel title="Días de pago después de la entrega (los más lentos)" wide>
            <MiniTable head={["Cliente", "Entregas", "Días promedio"]} rows={pago.map((p, i) => ({
              key: i, a: String(p.name), b: n0(p.entregas), c: `${n1(p.dias)} días`,
            }))} />
          </Panel>
        )}
      </div>
    </>
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
        <Stat label="Lotes en stock" value={n0(k.lotes_en_stock)} />
        <Stat label="Órdenes sin proyecto" value={pct(k.sin_proyecto_pct)} tone={num(k.sin_proyecto_pct) > 30 ? "mid" : ""} />
        <Stat label="Sin referencia del cliente" value={pct(k.sin_referencia_pct)} tone={num(k.sin_referencia_pct) > 30 ? "mid" : ""} />
      </div>
      <div className="grid">
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
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Drill panel: pila con breadcrumbs; venta → líneas → material → historia
// ─────────────────────────────────────────────────────────────────────────────
function DrillPanel(props: { stack: DrillNode[]; filters: Filters; push: (n: DrillNode) => void; popTo: (i: number) => void; close: () => void }) {
  const node = props.stack[props.stack.length - 1];
  const key = node.kind === "order" ? ["order_lines", node.orderId] : ["drill", node.entity, node.value, props.filters];
  const q = useData<Rec>(key, () =>
    node.kind === "order"
      ? (fetchOrderLines(node.orderId) as unknown as Promise<Rec>)
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
          const venta = lines.reduce((s, l) => s + num(l.venta), 0);
          const util = lines.reduce((s, l) => s + num(l.utilidad), 0);
          return (
            <>
              <div className="stats drill-stats">
                <Stat label="Cliente" value={String(o.partner)} sub={`${o.date} · ${o.seller} · ${o.currency}`} />
                <Stat label="Venta MXN" value={money(venta)} />
                <Stat label="Utilidad all-in" value={money(util)} tone={util < 0 ? "bad" : "good"} />
                <Stat label="Margen" value={venta ? pct((util / venta) * 100) : "—"} tone={marginTone(venta ? (util / venta) * 100 : 0)} />
              </div>
              <Panel title="Utilidad por material de esta venta" hint="click en material = seguir profundizando">
                <div className="tablewrap tall">
                  <table>
                    <thead>
                      <tr><th>Material</th><th>Categoría</th><th>Nivel</th><th className="r">Cant.</th><th className="r">Venta</th><th className="r">Costo all-in</th><th className="r">Utilidad</th><th className="r">Margen</th></tr>
                    </thead>
                    <tbody>
                      {lines.map((l, i) => (
                        <tr key={i} className="click" onClick={() => props.push({ kind: "entity", entity: "product", value: num(l.tmpl_id), label: String(l.product) })}>
                          <td className="ell">{String(l.product)}</td>
                          <td className="ell mut">{String(l.categ)}</td>
                          <td>{String(l.level)}</td>
                          <td className="r">{n1(l.qty)}{l.is_area ? " m²" : " pz"}</td>
                          <td className="r strong">{money(l.venta)}</td>
                          <td className="r mut">{money(l.costo)}</td>
                          <td className={"r " + (num(l.utilidad) < 0 ? "neg" : "")}>{money(l.utilidad)}</td>
                          <td className="r"><Pill tone={marginTone(num(l.margen))}>{pct(l.margen)}</Pill></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
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
const PRESETS: Array<[string, string]> = [["mes", "Mes"], ["trim", "Trimestre"], ["anio", "Año"]];

function App() {
  const boot = useMemo<Rec>(() => {
    try {
      return JSON.parse(document.getElementById("som-boot")?.textContent ?? "{}");
    } catch {
      return {};
    }
  }, []);

  const initial = useMemo(readHash, []);
  const [view, setView] = useState<ViewKey>(initial.view);
  const [filters, setFilters] = useState<Filters>({ ...defaultRange(), ...initial.filters });
  const [drillStack, setDrillStack] = useState<DrillNode[]>([]);
  const [preset, setPreset] = useState("anio");
  const [theme, setTheme] = useState<Theme>(initTheme);

  useEffect(() => writeHash(view, filters), [view, filters]);

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
    if (p === "mes") from.setDate(1);
    else if (p === "trim") from.setDate(from.getDate() - 90);
    else from.setFullYear(from.getFullYear() - 1);
    setPreset(p);
    setFilters((f) => ({ ...f, month: undefined, date_from: from.toISOString().slice(0, 10), date_to: to.toISOString().slice(0, 10) }));
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
        <a className="back" href="/odoo">← Volver a operaciones</a>
      </header>

      <div className="body">
        <nav className="sidenav" aria-label="Vistas">
          {VIEWS.map((v) => (
            <button key={v.key} className={view === v.key ? "on" : ""} onClick={() => setView(v.key)}>{v.label}</button>
          ))}
          <div className="navfoot">{String(boot.user ?? "")}</div>
        </nav>

        <main className="content" key={view + filtersKey}>
          {view === "resumen" && <ResumenView filters={filters} paused={drillStack.length > 0} />}
          {view === "ventas" && <VentasView filters={filters} drill={drill} />}
          {view === "materiales" && <MaterialesView filters={filters} drill={drill} />}
          {view === "inventario" && <InventarioView filters={filters} drill={drill} />}
          {view === "compras" && <ComprasView filters={filters} />}
          {view === "transito" && <TransitoView />}
          {view === "recepciones" && <RecepcionesView filters={filters} />}
          {view === "taller" && <TallerView filters={filters} />}
          {view === "entregas" && <EntregasView filters={filters} />}
          {view === "finanzas" && <FinanzasView filters={filters} />}
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
