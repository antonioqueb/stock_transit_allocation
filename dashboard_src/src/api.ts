// Pasarela JSON-RPC al backend (/som/analytics/rpc, sesión Odoo).
// Guard mínimo en el borde: todo payload pasa por asRecord antes de usarse.

export type Rec = Record<string, unknown>;

let rpcId = 0;

export async function rpc<T = Rec>(method: string, args: unknown[] = []): Promise<T> {
  const res = await fetch("/som/analytics/rpc", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: ++rpcId,
      method: "call",
      params: { method, args },
    }),
  });
  if (res.status === 401 || res.status === 403 || res.redirected) {
    window.location.href = "/web/login?redirect=/som/analytics";
    throw new Error("session expired");
  }
  const body = (await res.json()) as { result?: T; error?: { data?: { message?: string }; message?: string } };
  if (body.error) {
    throw new Error(body.error.data?.message ?? body.error.message ?? "RPC error");
  }
  const result = body.result as T & { error?: string };
  if (result && typeof result === "object" && "error" in result && (result as Rec).error) {
    throw new Error(String((result as Rec).error));
  }
  return result;
}

export function num(v: unknown): number {
  const n = typeof v === "number" ? v : parseFloat(String(v ?? 0));
  return Number.isFinite(n) ? n : 0;
}

export function arr<T = Rec>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : [];
}

// Formato anglosajón crudo: coma miles/millones, punto decimal. Sin k/M.
const nfMoney = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 1 });
const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export const money = (v: unknown) => "$" + nfMoney.format(num(v));
export const n1 = (v: unknown) => nf1.format(num(v));
export const n0 = (v: unknown) => nf0.format(num(v));
export const pct = (v: unknown) => nf1.format(num(v)) + "%";

export const MONTHS_ES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];
export function monthLabel(m: unknown): string {
  const s = String(m ?? "");
  if (s.length < 7) return s;
  return `${MONTHS_ES[parseInt(s.slice(5, 7), 10) - 1]} ${s.slice(2, 4)}`;
}

export function marginTone(m: number): "good" | "mid" | "bad" {
  return m < 0 ? "bad" : m < 15 ? "mid" : "good";
}

// ── Contratos Zod (zod/mini: tree-shakeable) en el borde de datos ──
import * as z from "zod/mini";

const zn = z.catch(z.coerce.number(), 0);
const zs = z.catch(z.coerce.string(), "");
const znn = z.catch(z.nullable(z.number()), null);
const zb = z.catch(z.boolean(), false);

export const ExecSummarySchema = z.object({
  venta_hoy: zn, venta_mes: zn, venta_mes_prev: zn, venta_mom_pct: zn,
  utilidad_mes: zn, margen_mes: zn, m2_mes: zn, bancos_mxn: zn,
  por_cobrar: zn, por_pagar: zn, contenedores_agua: zn, m2_agua: zn,
  inv_m2: zn, holds_activos: zn, auth_pendientes: zn, tc_banorte: zn,
});
export type ExecSummary = z.infer<typeof ExecSummarySchema>;

export const BanksSchema = z.object({
  journals: z.catch(z.array(z.object({ id: zn, name: zs, type: zs, balance: zn })), []),
  total: zn,
});
export type Banks = z.infer<typeof BanksSchema>;

export const OrderLinesSchema = z.object({
  order: z.object({
    id: zn, name: zs, partner: zs, date: zs, seller: zs,
    currency: zs, amount_total: zn,
  }),
  lines: z.catch(z.array(z.object({
    product: zs, categ: zs, qty: zn, is_area: zb,
    level: zs, price_unit: zn, venta: zn, costo: zn, utilidad: zn,
    margen: zn, tmpl_id: zn,
  })), []),
});
export type OrderLines = z.infer<typeof OrderLinesSchema>;

export const TimeToSellSchema = z.catch(z.array(z.object({
  tmpl_id: zn, name: zs, dias_venta: znn, m2_vendidos: zn,
  lots_vendidos: zn, edad_stock: znn, m2_stock: zn, lots_stock: zn,
})), []);
export type TimeToSellRow = z.infer<typeof TimeToSellSchema>[number];

// Packs de dashboard/drill: heterogéneos por dominio → contrato laxo
// (record) + acceso defensivo con arr()/num() en los componentes.
const LoosePack = z.record(z.string(), z.unknown());

export const fetchExec = () => rpc<Rec>("exec").then((r) => ExecSummarySchema.parse(r));
export const fetchBanks = () => rpc<Rec>("banks").then((r) => BanksSchema.parse(r));
export const fetchOrderLines = (orderId: number) =>
  rpc<Rec>("order_lines", [orderId]).then((r) => OrderLinesSchema.parse(r));
export const fetchTimeToSell = () =>
  rpc<Rec[]>("time_to_sell", [{}]).then((r) => TimeToSellSchema.parse(r));
export const fetchDashboard = (domain: string, filters: Rec) =>
  rpc<Rec>("dashboard", [domain, filters]).then((r) => LoosePack.parse(r));
export const fetchDrill = (entity: string, value: string | number, label: string, filters: Rec) =>
  rpc<Rec>("drill", [entity, value, label, filters]).then((r) => LoosePack.parse(r));
