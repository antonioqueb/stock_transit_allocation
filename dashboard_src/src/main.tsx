// SOM Dashboard Ejecutivo — bundle React standalone (patrón portal proveedores).
// Tres niveles de lectura: titular (ticker) → contexto (vistas) → explicación
// (drill con breadcrumbs: venta → líneas por material → material → historia).
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
// Estado de navegación serializable en el hash (#view=ventas&month=2026-07)
// ─────────────────────────────────────────────────────────────────────────────
type ViewKey = "resumen" | "ventas" | "materiales" | "inventario" | "transito" | "finanzas";

const VIEWS: Array<{ key: ViewKey; label: string }> = [
  { key: "resumen", label: "Resumen" },
  { key: "ventas", label: "Ventas" },
  { key: "materiales", label: "Materiales" },
  { key: "inventario", label: "Inventario" },
  { key: "transito", label: "Tránsito" },
  { key: "finanzas", label: "Finanzas" },
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
  | { kind: "entity"; entity: "month" | "seller" | "customer" | "product" | "category" | "level"; value: string | number; label: string; data?: Rec }
  | { kind: "order"; orderId: number; label: string; data?: Rec };

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

function Panel(props: { title: string; hint?: string; right?: React.ReactNode; children: React.ReactNode; wide?: boolean }) {
  return (
    <section className={"panel" + (props.wide ? " wide" : "")}>
      <header className="panel-h">
        <h3>{props.title}</h3>
        {props.hint && <span className="hint">{props.hint}</span>}
        {props.right}
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
// Volver atrás en el drill o entre vistas es instantáneo (staleTime 60s).
function useData<T>(key: unknown[], fn: () => Promise<T>): { data: T | null; loading: boolean; error: string; retry: () => void } {
  const q = useQuery({ queryKey: key, queryFn: fn, staleTime: 60_000, retry: 1, refetchOnWindowFocus: false });
  return {
    data: q.data ?? null,
    loading: q.isPending,
    error: q.error ? (q.error as Error).message : "",
    retry: () => void q.refetch(),
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Ticker ejecutivo: se refresca solo, rota el foco
// ─────────────────────────────────────────────────────────────────────────────
function ExecTicker() {
  const [data, setData] = useState<Rec | null>(null);
  const [focus, setFocus] = useState(0);

  useEffect(() => {
    let alive = true;
    const load = () => fetchExec().then((d) => alive && setData(d as unknown as Rec)).catch(() => undefined);
    load();
    const poll = setInterval(load, 60_000);
    const rotate = setInterval(() => setFocus((f) => f + 1), 7_000);
    return () => {
      alive = false;
      clearInterval(poll);
      clearInterval(rotate);
    };
  }, []);

  const items = useMemo(() => {
    if (!data) return [];
    return [
      { l: "Venta hoy", v: money(data.venta_hoy) },
      {
        l: "Venta del mes",
        v: money(data.venta_mes),
        d: `${num(data.venta_mom_pct) >= 0 ? "▲" : "▼"} ${pct(Math.abs(num(data.venta_mom_pct)))} vs mes anterior`,
        t: num(data.venta_mom_pct) >= 0 ? ("good" as const) : ("bad" as const),
      },
      { l: "Utilidad del mes", v: money(data.utilidad_mes), t: marginTone(num(data.margen_mes)) },
      { l: "Margen", v: pct(data.margen_mes), t: marginTone(num(data.margen_mes)) },
      { l: "m² del mes", v: n1(data.m2_mes) },
      { l: "Bancos", v: money(data.bancos_mxn) },
      { l: "Me deben", v: money(data.por_cobrar), t: "good" as const },
      { l: "Debo", v: money(data.por_pagar), t: "bad" as const },
      { l: "Contenedores en el agua", v: n0(data.contenedores_agua) },
      { l: "m² en el agua", v: n1(data.m2_agua) },
      { l: "Inventario m²", v: n1(data.inv_m2) },
      { l: "Holds activos", v: n0(data.holds_activos) },
      { l: "TC Banorte", v: n1(data.tc_banorte) },
    ];
  }, [data]);

  if (!items.length) return <div className="ticker sk-line" />;
  const f = items[focus % items.length];
  return (
    <div className="ticker" role="status" aria-live="polite">
      <div className="ticker-focus" key={focus}>
        <span className="tf-l">{f.l}</span>
        <span className={"tf-v " + ((f as { t?: string }).t ?? "")}>{f.v}</span>
        {(f as { d?: string }).d && <span className={"tf-d " + ((f as { t?: string }).t ?? "")}>{(f as { d?: string }).d}</span>}
      </div>
      <div className="ticker-rest">
        {items.map((it, i) => (
          <button key={it.l} className={"tk-item" + (i === focus % items.length ? " on" : "")} onClick={() => setFocus(i)}>
            <span>{it.l}</span>
            <b className={(it as { t?: string }).t ?? ""}>{it.v}</b>
          </button>
        ))}
      </div>
    </div>
  );
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
            <tr key={String(o.id)} onClick={() => props.onOrder(num(o.id), String(o.name))} tabIndex={0}
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
// Vistas
// ─────────────────────────────────────────────────────────────────────────────
function ResumenView(props: { filters: Filters; drill: (n: DrillNode) => void }) {
  const q = useData(["dashboard", "resumen", props.filters], () => fetchDashboard("resumen", props.filters as Rec));
  if (q.loading) return <div className="grid"><Skeleton h={90} /><Skeleton /><Skeleton /><Skeleton /></div>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const d = q.data!;
  const k = (d.kpis ?? {}) as Rec;
  const months = arr(d.by_month);
  const aging = arr(d.aging);
  const transit = arr(d.transit_status);
  return (
    <>
      <div className="stats">
        <Stat label="Venta (TC real)" value={money(k.venta_mxn)} sub={`${n0(k.ordenes)} órdenes`} />
        <Stat label="Utilidad all-in" value={money(k.utilidad_mxn)} sub={`Margen ${pct(k.margen_pct)}`} tone={marginTone(num(k.margen_pct))} />
        <Stat label="m² vendidos" value={n1(k.m2_vendidos)} sub={`${n0(k.piezas_vendidas)} piezas`} />
        <Stat label="Inventario disponible" value={`${n1(k.inv_disponible_m2)} m²`} sub={`Valor ${money(k.inv_valor_mxn)}`} />
        <Stat label="En el agua" value={`${n1(k.transit_m2)} m²`} />
        <Stat label="Me deben" value={money(k.por_cobrar)} tone="good" />
        <Stat label="Debo" value={money(k.por_pagar)} tone="bad" />
      </div>
      <div className="grid">
        <Panel title="Venta y utilidad por mes" hint="click en un mes = profundizar" wide>
          <ChartBox height={300} deps={[months]} config={{
            type: "bar",
            data: {
              labels: months.map((r) => monthLabel(r.key)),
              datasets: [
                { label: "Venta", data: months.map((r) => num(r.venta)), backgroundColor: "rgba(11,87,208,.85)", borderRadius: 6, maxBarThickness: 34, isMoney: true },
                { label: "Utilidad", data: months.map((r) => num(r.utilidad)), backgroundColor: "rgba(16,185,129,.8)", borderRadius: 6, maxBarThickness: 34, isMoney: true },
                { type: "line", label: "m²", data: months.map((r) => num(r.m2)), borderColor: C.sky, borderWidth: 2.5, pointRadius: 3, tension: 0.4, yAxisID: "y1" },
              ],
            },
            options: {
              ...baseOptions((i) => {
                const r = months[i];
                props.drill({ kind: "entity", entity: "month", value: String(r.key), label: `Mes ${monthLabel(r.key)}` });
              }),
              interaction: { mode: "index", intersect: false },
              scales: { y: axisMoney(), y1: { beginAtZero: true, position: "right", grid: { display: false }, border: { display: false }, ticks: { color: "#7dd3fc", font: { size: 10 } } }, x: axisPlain() },
            },
          }} />
        </Panel>
        <Panel title="Antigüedad del inventario">
          <ChartBox height={300} deps={[aging]} config={{
            type: "bar",
            data: { labels: aging.map((r) => String(r.bucket)), datasets: [{ label: "m²", data: aging.map((r) => num(r.m2)), backgroundColor: [C.green, C.sky, C.amber, C.red, "#64748b"], borderRadius: 6 }] },
            options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(9.5) } },
          }} />
        </Panel>
        <Panel title="Material en el agua por estatus" wide>
          <ChartBox height={230} deps={[transit]} config={{
            type: "bar",
            data: { labels: transit.map((r) => String(r.label)), datasets: [{ label: "m²", data: transit.map((r) => num(r.m2)), backgroundColor: "rgba(56,189,248,.8)", borderRadius: 6 }] },
            options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(9.5) } },
          }} />
        </Panel>
        <Panel title="Órdenes recientes" hint="click = por qué de su utilidad">
          <OrdersTable orders={arr(d.orders).slice(0, 8)} onOrder={(id, name) => props.drill({ kind: "order", orderId: id, label: name })} />
        </Panel>
      </div>
    </>
  );
}

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
        <Panel title="Venta por categoría de producto" hint="click = profundizar" wide>
          <ChartBox height={280} deps={[cats]} config={{
            type: "bar",
            data: {
              labels: cats.map((r) => String(r.name)),
              datasets: [
                { label: "Venta", data: cats.map((r) => num(r.venta)), backgroundColor: "rgba(11,87,208,.85)", borderRadius: 6, maxBarThickness: 20, isMoney: true },
                { label: "Utilidad", data: cats.map((r) => num(r.utilidad)), backgroundColor: "rgba(16,185,129,.8)", borderRadius: 6, maxBarThickness: 20, isMoney: true },
              ],
            },
            options: {
              ...baseOptions((i) => {
                const r = cats[i];
                props.drill({ kind: "entity", entity: "category", value: num(r.key), label: `Categoría ${r.name}` });
              }),
              indexAxis: "y",
              scales: { x: axisMoney(), y: axisPlain(10) },
            },
          }} />
        </Panel>
        <Panel title="Vendedores" hint="click = profundizar">
          <ChartBox height={280} deps={[sellers]} config={{
            type: "bar",
            data: {
              labels: sellers.map((r) => String(r.name).split(" ")[0]),
              datasets: [
                { label: "Venta", data: sellers.map((r) => num(r.venta)), backgroundColor: "rgba(11,87,208,.85)", borderRadius: 6, isMoney: true },
                { label: "Utilidad", data: sellers.map((r) => num(r.utilidad)), backgroundColor: "rgba(16,185,129,.8)", borderRadius: 6, isMoney: true },
              ],
            },
            options: {
              ...baseOptions((i) => {
                const r = sellers[i];
                props.drill({ kind: "entity", entity: "seller", value: num(r.key), label: String(r.name) });
              }),
              interaction: { mode: "index", intersect: false },
              scales: { y: axisMoney(), x: axisPlain(10) },
            },
          }} />
        </Panel>
        <Panel title="Top materiales por utilidad" hint="click = profundizar" wide>
          <ChartBox height={300} deps={[products]} config={{
            type: "bar",
            data: {
              labels: products.map((r) => String(r.name).slice(0, 38)),
              datasets: [{ label: "Utilidad", data: products.map((r) => num(r.utilidad)), backgroundColor: products.map((r) => (num(r.utilidad) >= 0 ? "rgba(16,185,129,.85)" : "rgba(239,68,68,.85)")), borderRadius: 5, maxBarThickness: 16, isMoney: true }],
            },
            options: {
              ...baseOptions((i) => {
                const r = products[i];
                props.drill({ kind: "entity", entity: "product", value: num(r.key), label: String(r.name) });
              }),
              indexAxis: "y",
              plugins: { ...baseOptions().plugins, legend: { display: false } },
              scales: { x: axisMoney(), y: axisPlain(9.5) },
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

function MaterialesView(props: { drill: (n: DrillNode) => void }) {
  const q = useData(["time_to_sell"], fetchTimeToSell);
  if (q.loading) return <div className="grid"><Skeleton h={480} /></div>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const rows = arr(q.data);
  const slow = rows.filter((r) => r.edad_stock != null).slice(0, 12);
  return (
    <>
      <div className="stats">
        <Stat label="Materiales analizados" value={n0(rows.length)} sub="ventas 12 meses + stock actual" />
        <Stat label="Más lento en patio" value={slow[0] ? `${n1(slow[0].edad_stock)} días` : "—"} sub={slow[0] ? String(slow[0].name).slice(0, 40) : ""} tone="bad" />
      </div>
      <div className="grid">
        <Panel title="Capital estancado: edad del stock por material" hint="click = profundizar" wide>
          <ChartBox height={320} deps={[slow]} config={{
            type: "bar",
            data: {
              labels: slow.map((r) => String(r.name).slice(0, 36)),
              datasets: [{ label: "Días en patio (stock actual)", data: slow.map((r) => num(r.edad_stock)), backgroundColor: "rgba(245,158,11,.85)", borderRadius: 5, maxBarThickness: 18 }],
            },
            options: {
              ...baseOptions((i) => {
                const r = slow[i];
                props.drill({ kind: "entity", entity: "product", value: num(r.tmpl_id), label: String(r.name) });
              }),
              indexAxis: "y",
              plugins: { ...baseOptions().plugins, legend: { display: false } },
              scales: { x: axisMoney(), y: axisPlain(9.5) },
            },
          }} />
        </Panel>
        <Panel title="Tiempo de venta por material" hint="click en fila = profundizar" wide>
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
                  <tr key={String(r.tmpl_id)} onClick={() => props.drill({ kind: "entity", entity: "product", value: num(r.tmpl_id), label: String(r.name) })}>
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
      </div>
    </>
  );
}

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
        <Stat label="Disponible" value={`${n1(k.disponible_m2)} m²`} />
        <Stat label="En hold" value={`${n1(k.hold_m2)} m²`} sub={`${n0(k.holds_activos)} apartados · ${n1(k.holds_edad_dias)} días prom.`} />
        <Stat label="Valor all-in inmovilizado" value={money(k.valor_mxn)} />
        <Stat label="Rotación 12m" value={`${n1(k.rotacion)}x`} sub={`${n1(k.meses_inventario)} meses de inventario`} tone={num(k.rotacion) < 2 ? "bad" : "good"} />
        <Stat label="Committed en OV" value={`${n1(k.committed_m2)} m²`} />
        <Stat label="Bloques con foto" value={pct(k.bloques_con_foto_pct)} sub={`${n1(k.m2_sin_foto)} m² invisibles`} tone={num(k.bloques_con_foto_pct) < 70 ? "bad" : "good"} />
      </div>
      <div className="grid">
        <Panel title="Top materiales en stock" hint="click = profundizar" wide>
          <ChartBox height={320} deps={[top]} config={{
            type: "bar",
            data: { labels: top.map((r) => String(r.name).slice(0, 36)), datasets: [{ label: "m²", data: top.map((r) => num(r.m2)), backgroundColor: "rgba(11,87,208,.85)", borderRadius: 5, maxBarThickness: 16 }] },
            options: {
              ...baseOptions((i) => {
                const r = top[i];
                props.drill({ kind: "entity", entity: "product", value: num(r.key), label: String(r.name) });
              }),
              indexAxis: "y",
              plugins: { ...baseOptions().plugins, legend: { display: false } },
              scales: { x: axisMoney(), y: axisPlain(9.5) },
            },
          }} />
        </Panel>
        <Panel title="Antigüedad (regla Stone Profit)">
          <ChartBox height={320} deps={[aging]} config={{
            type: "bar",
            data: { labels: aging.map((r) => String(r.bucket)), datasets: [{ label: "m²", data: aging.map((r) => num(r.m2)), backgroundColor: [C.green, C.sky, C.amber, C.red, "#64748b"], borderRadius: 6 }] },
            options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(9) } },
          }} />
        </Panel>
        <Panel title="Merma dimensional por proveedor (PL vs medidas reales)" wide>
          <MiniTable head={["Proveedor", "m² PL / reales", "Merma"]} rows={merma.map((m, i) => ({
            key: i, a: String(m.name), b: `${n1(m.teorico)} / ${n1(m.real)}`,
            c: <Pill tone={num(m.merma_pct) > 2 ? "bad" : num(m.merma_pct) > 0.5 ? "mid" : "good"}>{pct(m.merma_pct)}</Pill>,
          }))} />
        </Panel>
      </div>
    </>
  );
}

function TransitoView() {
  const q = useData(["dashboard", "transito"], () => fetchDashboard("transito", {}));
  if (q.loading) return <div className="grid"><Skeleton h={90} /><Skeleton /></div>;
  if (q.error) return <ErrorBox msg={q.error} retry={q.retry} />;
  const d = q.data!;
  const k = (d.kpis ?? {}) as Rec;
  const st = arr(d.by_status);
  const voyages = arr(d.voyages);
  return (
    <>
      <div className="stats">
        <Stat label="m² en el agua" value={n1(k.total_m2)} sub={`${n0(k.embarques)} embarques`} />
        <Stat label="Pre-vendido" value={pct(k.prevendido_pct)} tone={num(k.prevendido_pct) > 50 ? "good" : ""} />
        <Stat label="Pendientes de publicar" value={n0(k.pendientes_publicar)} sub="meta: 0" tone={num(k.pendientes_publicar) > 0 ? "bad" : "good"} />
        <Stat label="Desviación de ETA" value={`${n1(k.eta_desviacion_dias)} días`} />
        <Stat label="Días a publicar" value={n1(k.dias_a_publicar)} />
      </div>
      <div className="grid">
        <Panel title="m² por estatus del viaje" wide>
          <ChartBox height={260} deps={[st]} config={{
            type: "bar",
            data: { labels: st.map((r) => String(r.label)), datasets: [{ label: "m²", data: st.map((r) => num(r.m2)), backgroundColor: "rgba(56,189,248,.85)", borderRadius: 6 }] },
            options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(9) } },
          }} />
        </Panel>
        <Panel title="Embarques activos">
          <MiniTable head={["Embarque", "m²", "% vendido"]} rows={voyages.map((v) => ({
            key: String(v.id), a: `${v.name} · ${v.supplier}`, b: n1(v.m2),
            c: <Pill tone={num(v.alloc_pct) >= 50 ? "good" : "mid"}>{pct(v.alloc_pct)}</Pill>,
          }))} />
        </Panel>
      </div>
    </>
  );
}

function FinanzasView() {
  const banks = useData(["banks"], fetchBanks);
  const fin = useData(["dashboard", "financiero"], () => fetchDashboard("financiero", {}));
  return (
    <>
      {banks.loading ? <Skeleton h={90} /> : banks.error ? <ErrorBox msg={banks.error} retry={banks.retry} /> : (
        <div className="stats">
          <Stat label="Dinero en bancos y cajas" value={money(banks.data!.total)} tone="good" />
          {banks.data!.journals.slice(0, 5).map((j) => (
            <Stat key={String(j.id)} label={String(j.name)} value={money(j.balance)} sub={j.type === "cash" ? "caja" : "banco"} />
          ))}
        </div>
      )}
      {fin.loading ? <div className="grid"><Skeleton /><Skeleton /></div> : fin.error ? <ErrorBox msg={fin.error} retry={fin.retry} /> : (() => {
        const d = fin.data!;
        const k = (d.kpis ?? {}) as Rec;
        const arb = arr(d.ar_buckets);
        const apb = arr(d.ap_buckets);
        const bm = arr(d.by_month);
        return (
          <div className="grid">
            <div className="stats wide">
              <Stat label="ME DEBEN" value={money(k.por_cobrar)} tone="good" />
              <Stat label="DEBO" value={money(k.por_pagar)} tone="bad" />
              <Stat label="Posición neta" value={money(k.neto)} tone={num(k.neto) >= 0 ? "good" : "bad"} />
              <Stat label="Efectivo sin aplicar" value={money(k.efectivo_sin_aplicar)} sub={`${n0(k.recibos_sin_aplicar)} recibos`} tone={num(k.efectivo_sin_aplicar) > 0 ? "mid" : ""} />
            </div>
            <Panel title="Por cobrar por antigüedad">
              <ChartBox height={230} deps={[arb]} config={{
                type: "bar",
                data: { labels: arb.map((r) => String(r.bucket)), datasets: [{ label: "MXN", data: arb.map((r) => num(r.monto)), backgroundColor: ["#10b981", "#a3e635", "#f59e0b", "#fb923c", "#ef4444"], borderRadius: 6, isMoney: true }] },
                options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(9.5) } },
              }} />
            </Panel>
            <Panel title="Por pagar por antigüedad">
              <ChartBox height={230} deps={[apb]} config={{
                type: "bar",
                data: { labels: apb.map((r) => String(r.bucket)), datasets: [{ label: "MXN", data: apb.map((r) => num(r.monto)), backgroundColor: ["#38bdf8", "#818cf8", "#a78bfa", "#f472b6", "#ef4444"], borderRadius: 6, isMoney: true }] },
                options: { ...baseOptions(), plugins: { ...baseOptions().plugins, legend: { display: false } }, scales: { y: axisMoney(), x: axisPlain(9.5) } },
              }} />
            </Panel>
            <Panel title="Facturado vs comprado (12 meses)" wide>
              <ChartBox height={260} deps={[bm]} config={{
                type: "line",
                data: {
                  labels: bm.map((r) => monthLabel(r.key)),
                  datasets: [
                    { label: "Facturado", data: bm.map((r) => num(r.facturado)), borderColor: C.green, backgroundColor: "rgba(16,185,129,.08)", borderWidth: 2.5, tension: 0.4, fill: true, isMoney: true },
                    { label: "Comprado", data: bm.map((r) => num(r.comprado)), borderColor: C.red, backgroundColor: "rgba(239,68,68,.06)", borderWidth: 2.5, tension: 0.4, fill: true, isMoney: true },
                  ],
                },
                options: { ...baseOptions(), interaction: { mode: "index", intersect: false }, scales: { y: axisMoney(), x: axisPlain() } },
              }} />
            </Panel>
            <Panel title="Quién me debe">
              <MiniTable head={["Cliente", "Saldo", "Facturas"]} rows={arr(d.ar_top).map((c) => ({ key: String(c.key), a: String(c.name), b: money(c.monto), c: n0(c.facturas) }))} />
            </Panel>
            <Panel title="A quién le debo">
              <MiniTable head={["Proveedor", "Saldo", "Facturas"]} rows={arr(d.ap_top).map((p) => ({ key: String(p.key), a: String(p.name), b: money(p.monto), c: n0(p.facturas) }))} />
            </Panel>
          </div>
        );
      })()}
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
                        <tr key={i} onClick={() => props.push({ kind: "entity", entity: "product", value: num(l.tmpl_id), label: String(l.product) })}>
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
                      { label: "Utilidad", data: bm.map((r) => num(r.utilidad)), borderColor: C.green, backgroundColor: "rgba(16,185,129,.07)", borderWidth: 2.5, tension: 0.4, fill: true, isMoney: true },
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
  const [preset, setPreset] = useState("12m");

  useEffect(() => writeHash(view, filters), [view, filters]);

  const applyPreset = useCallback((p: string) => {
    const to = new Date();
    const from = new Date(to);
    if (p === "mes") from.setDate(1);
    else if (p === "90d") from.setDate(from.getDate() - 90);
    else from.setFullYear(from.getFullYear() - 1);
    setPreset(p);
    setFilters((f) => ({ ...f, month: undefined, date_from: from.toISOString().slice(0, 10), date_to: to.toISOString().slice(0, 10) }));
  }, []);

  const drill = useCallback((n: DrillNode) => setDrillStack((s) => [...s, n]), []);
  const filtersKey = JSON.stringify(filters);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></svg>
          </span>
          <div>
            <b>SOM Analytics</b>
            <small>{String(boot.company ?? "")} · utilidad all-in · TC Banorte real</small>
          </div>
        </div>
        <div className="presets" role="tablist" aria-label="Periodo">
          {[["mes", "Mes"], ["90d", "Trimestre"], ["12m", "12 meses"]].map(([k, l]) => (
            <button key={k} role="tab" aria-selected={preset === k} className={preset === k ? "on" : ""} onClick={() => applyPreset(k)}>{l}</button>
          ))}
        </div>
        <a className="back" href="/odoo">← Volver a operaciones</a>
      </header>

      <ExecTicker />

      <div className="body">
        <nav className="sidenav" aria-label="Vistas">
          {VIEWS.map((v) => (
            <button key={v.key} className={view === v.key ? "on" : ""} onClick={() => setView(v.key)}>{v.label}</button>
          ))}
          <div className="navfoot">{String(boot.user ?? "")}</div>
        </nav>

        <main className="content" key={view + filtersKey}>
          {view === "resumen" && <ResumenView filters={filters} drill={drill} />}
          {view === "ventas" && <VentasView filters={filters} drill={drill} />}
          {view === "materiales" && <MaterialesView drill={drill} />}
          {view === "inventario" && <InventarioView filters={filters} drill={drill} />}
          {view === "transito" && <TransitoView />}
          {view === "finanzas" && <FinanzasView />}
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
