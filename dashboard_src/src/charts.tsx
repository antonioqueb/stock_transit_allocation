// Envoltura fina sobre el Chart.js vendorizado de Odoo (window.Chart).
import { useEffect, useRef } from "react";
import { money, n1 } from "./api";

declare global {
  interface Window {
    Chart: any; // Chart.js UMD vendorizado por Odoo — sin tipos empaquetados.
  }
}

export const C = {
  blue: "#0b57d0",
  sky: "#38bdf8",
  green: "#10b981",
  amber: "#f59e0b",
  red: "#ef4444",
  violet: "#8b5cf6",
  ink: "#0f172a",
  mut: "#64748b",
};
export const PALETTE = [C.blue, C.sky, C.green, C.amber, C.violet, C.red, "#0e7490", "#ec4899"];

const FONT = "'Inter','SF Pro Display',-apple-system,'Segoe UI',sans-serif";

export function baseOptions(onClick?: (index: number) => void) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 350, easing: "easeOutQuart" },
    onClick: onClick
      ? (_e: unknown, els: Array<{ index: number }>) => els.length && onClick(els[0].index)
      : undefined,
    onHover: (e: any, els: unknown[]) => {
      if (e?.native?.target) e.native.target.style.cursor = els.length && onClick ? "pointer" : "default";
    },
    plugins: {
      legend: {
        labels: {
          boxWidth: 9, boxHeight: 9, usePointStyle: true, pointStyle: "circle",
          font: { size: 11, family: FONT, weight: "600" }, color: "#94a3b8",
        },
      },
      tooltip: {
        backgroundColor: "rgba(10,16,30,.97)", titleColor: "#f8fafc", bodyColor: "#cbd5e1",
        titleFont: { size: 12, weight: "700", family: FONT },
        bodyFont: { size: 11.5, family: FONT },
        padding: 12, cornerRadius: 10, boxWidth: 8, boxHeight: 8, usePointStyle: true,
        callbacks: {
          label: (ctx: any) => {
            const v = ctx.parsed.y ?? ctx.parsed.x ?? ctx.parsed;
            return ` ${ctx.dataset.label ?? ""}: ${ctx.dataset.isMoney ? money(v) : n1(v)}`;
          },
        },
      },
    },
  };
}

export function axisMoney() {
  return {
    beginAtZero: true,
    border: { display: false },
    grid: { color: "rgba(148,163,184,.10)" },
    ticks: {
      font: { size: 10.5, family: FONT }, color: "#64748b",
      callback: (v: number) => new Intl.NumberFormat("en-US").format(v),
    },
  };
}

export function axisPlain(size = 10.5) {
  return {
    border: { display: false }, grid: { display: false },
    ticks: { font: { size, family: FONT, weight: "600" }, color: "#94a3b8" },
  };
}

export function ChartBox(props: { config: unknown; height?: number; deps: unknown[] }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const chartRef = useRef<any>(null);

  useEffect(() => {
    if (!ref.current || !window.Chart) return;
    chartRef.current?.destroy();
    chartRef.current = new window.Chart(ref.current.getContext("2d"), props.config);
    return () => {
      chartRef.current?.destroy();
      chartRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, props.deps);

  return (
    <div className="chartbox" style={{ height: props.height ?? 260 }}>
      <canvas ref={ref} />
    </div>
  );
}
