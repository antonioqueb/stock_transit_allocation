// ─────────────────────────────────────────────────────────────────────────────
// Apache ECharts vendorizado (tree-shaken: SOLO los tipos que usamos) con
// tema de la casa y accesibilidad ARIA habilitada explícitamente.
// TODO el dashboard renderiza con ECharts: los visuales del rediseño usan
// este wrapper directo y las gráficas legacy pasan por el adaptador de
// charts.tsx (cero Chart.js en runtime).
// ─────────────────────────────────────────────────────────────────────────────
import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts/core";
import {
  BarChart, LineChart, PieChart, ScatterChart, FunnelChart, TreemapChart,
  RadarChart, GaugeChart, SankeyChart, HeatmapChart, BoxplotChart, SunburstChart,
  CustomChart, ChordChart, ThemeRiverChart, PictorialBarChart,
} from "echarts/charts";
import {
  GridComponent, TooltipComponent, LegendComponent, TitleComponent,
  DatasetComponent, VisualMapComponent, MarkLineComponent, AriaComponent,
  CalendarComponent, MatrixComponent, DataZoomInsideComponent, BrushComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  // Catálogo AMPLIO: los tipos sin uso aún (sankey, heatmap, boxplot,
  // sunburst) quedan registrados para las páginas de F3 — la variedad
  // visual es requisito del negocio, la semántica la decide cada página.
  BarChart, LineChart, PieChart, ScatterChart, FunnelChart, TreemapChart,
  RadarChart, GaugeChart, SankeyChart, HeatmapChart, BoxplotChart, SunburstChart,
  CustomChart, ChordChart, ThemeRiverChart, PictorialBarChart,
  GridComponent, TooltipComponent, LegendComponent, TitleComponent,
  DatasetComponent, VisualMapComponent, MarkLineComponent, AriaComponent,
  CalendarComponent, MatrixComponent, DataZoomInsideComponent, BrushComponent,
  CanvasRenderer,
]);

export const EC = {
  blue: "#0b57d0", sky: "#0ea5e9", green: "#059669", amber: "#d97706",
  red: "#dc2626", violet: "#7c3aed", teal: "#0e7490", pink: "#db2777",
};
export const EC_PALETTE = [EC.blue, EC.sky, EC.green, EC.amber, EC.violet, EC.red, EC.teal, EC.pink];

const FONT = "'Inter','SF Pro Display',-apple-system,'Segoe UI',sans-serif";

function isDark(): boolean {
  return document.documentElement.dataset.theme === "dark";
}

export function ecInk() {
  return isDark()
    ? { tick: "#94a3b8", grid: "rgba(148,163,184,.14)", txt: "#e2e8f0", panel: "#101725" }
    : { tick: "#64748b", grid: "rgba(15,23,42,.08)", txt: "#17223b", panel: "#ffffff" };
}

// Base común: tooltip de la casa, tipografía, ARIA y sin animaciones largas.
export function ecBase(): Record<string, unknown> {
  const ink = ecInk();
  return {
    color: EC_PALETTE,
    textStyle: { fontFamily: FONT, color: ink.txt },
    animationDuration: 350,
    aria: { enabled: true },
    tooltip: {
      trigger: "item",
      backgroundColor: "rgba(10,16,30,.97)",
      borderWidth: 0,
      textStyle: { color: "#e2e8f0", fontFamily: FONT, fontSize: 12.5 },
      padding: [10, 14],
    },
    grid: { left: 8, right: 16, top: 28, bottom: 8, containLabel: true },
  };
}

export function ecAxis(kind: "cat" | "money", data?: unknown[]): Record<string, unknown> {
  const ink = ecInk();
  if (kind === "cat") {
    return {
      type: "category", data,
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: ink.tick, fontFamily: FONT, fontSize: 11.5 },
    };
  }
  return {
    type: "value",
    splitLine: { lineStyle: { color: ink.grid } },
    axisLabel: {
      color: ink.tick, fontFamily: FONT, fontSize: 11,
      formatter: (v: number) => new Intl.NumberFormat("en-US").format(v),
    },
  };
}

export function EChartBox(props: {
  option: Record<string, unknown> | (() => Record<string, unknown>);
  height?: number;
  deps: unknown[];
  onClick?: (params: { name: string; dataIndex: number; seriesIndex: number; data: unknown }) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const [themeBump, setThemeBump] = useState(0);

  useEffect(() => {
    const onTheme = () => setThemeBump((b) => b + 1);
    window.addEventListener("som-theme", onTheme);
    return () => window.removeEventListener("som-theme", onTheme);
  }, []);

  useEffect(() => {
    if (!ref.current) return;
    chartRef.current?.dispose();
    const chart = echarts.init(ref.current);
    chartRef.current = chart;
    const opt = typeof props.option === "function" ? props.option() : props.option;
    chart.setOption(opt as never);
    if (props.onClick) {
      chart.on("click", (p) => props.onClick!({
        name: String(p.name ?? ""), dataIndex: p.dataIndex ?? 0,
        seriesIndex: p.seriesIndex ?? 0, data: p.data,
      }));
    }
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...props.deps, themeBump]);

  return <div ref={ref} style={{ height: props.height ?? 320, width: "100%" }} />;
}
