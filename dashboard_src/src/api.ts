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
