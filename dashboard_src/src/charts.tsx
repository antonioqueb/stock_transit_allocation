// Envoltura fina sobre el Chart.js vendorizado de Odoo (window.Chart).
// Tematizable: los colores de ejes/leyendas se resuelven al crear la
// gráfica y ChartBox se recrea al escuchar el evento global "som-theme".
import { useEffect, useRef, useState } from "react";
import { money, n1 } from "./api";

declare global {
  interface Window {
    Chart: any; // Chart.js UMD vendorizado por Odoo — sin tipos empaquetados.
  }
}

export const C = {
  blue: "#0b57d0",
  sky: "#0ea5e9",
  green: "#059669",
  amber: "#d97706",
  red: "#dc2626",
  violet: "#7c3aed",
  mut: "#64748b",
};
export const PALETTE = [C.blue, C.sky, C.green, C.amber, C.violet, C.red, "#0e7490", "#db2777"];

const FONT = "'Inter','SF Pro Display',-apple-system,'Segoe UI',sans-serif";

function isDark(): boolean {
  return document.documentElement.dataset.theme === "dark";
}

function themeInk() {
  return isDark()
    ? { tick: "#94a3b8", grid: "rgba(148,163,184,.12)", legend: "#94a3b8" }
    : { tick: "#64748b", grid: "rgba(15,23,42,.08)", legend: "#475569" };
}

export function baseOptions(onClick?: (index: number) => void) {
  const ink = themeInk();
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
          font: { size: 12, family: FONT, weight: "600" }, color: ink.legend,
        },
      },
      tooltip: {
        backgroundColor: "rgba(10,16,30,.97)", titleColor: "#f8fafc", bodyColor: "#cbd5e1",
        titleFont: { size: 13, weight: "700", family: FONT },
        bodyFont: { size: 12.5, family: FONT },
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
  const ink = themeInk();
  return {
    beginAtZero: true,
    border: { display: false },
    grid: { color: ink.grid },
    ticks: {
      font: { size: 11.5, family: FONT }, color: ink.tick,
      callback: (v: number) => new Intl.NumberFormat("en-US").format(v),
    },
  };
}

export function axisPlain(size = 11.5) {
  const ink = themeInk();
  return {
    border: { display: false }, grid: { display: false },
    ticks: { font: { size, family: FONT, weight: "600" }, color: ink.tick },
  };
}

export function ChartBox(props: { config: unknown | (() => unknown); height?: number; deps: unknown[] }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const chartRef = useRef<any>(null);
  const [themeBump, setThemeBump] = useState(0);

  useEffect(() => {
    const onTheme = () => setThemeBump((b) => b + 1);
    window.addEventListener("som-theme", onTheme);
    return () => window.removeEventListener("som-theme", onTheme);
  }, []);

  useEffect(() => {
    if (!ref.current) return;
    if (!window.Chart) {
      ref.current.parentElement?.classList.add("nochart");
      return;
    }
    chartRef.current?.destroy();
    const cfg = typeof props.config === "function" ? (props.config as () => unknown)() : props.config;
    chartRef.current = new window.Chart(ref.current.getContext("2d"), cfg);
    return () => {
      chartRef.current?.destroy();
      chartRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...props.deps, themeBump]);

  return (
    <div className="chartbox" style={{ height: props.height ?? 280 }}>
      <canvas ref={ref} />
    </div>
  );
}
