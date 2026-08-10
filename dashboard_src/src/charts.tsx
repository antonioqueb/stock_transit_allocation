// ─────────────────────────────────────────────────────────────────────────────
// ChartBox: ADAPTADOR universal a Apache ECharts.
//
// Históricamente este componente pintaba con el Chart.js vendorizado de
// Odoo; hoy TODO el dashboard renderiza con ECharts (regla del negocio:
// cero Chart.js). Las ~35 gráficas legacy siguen declarando su config en
// formato Chart.js (type/data/datasets/options) y aquí se traduce a una
// option ECharts con el tema de la casa — así la migración fue total sin
// reescribir página por página.
// ─────────────────────────────────────────────────────────────────────────────
import { money, n1 } from "./api";
import { EChartBox, ecInk, EC_PALETTE } from "./echarts";

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

// Compatibilidad: las páginas legacy construyen su config con estos
// helpers. El adaptador solo LEE de ellos onClick/stacked/legend.
export function baseOptions(onClick?: (index: number) => void) {
  return {
    onClick: onClick
      ? (_e: unknown, els: Array<{ index: number }>) => els.length && onClick(els[0].index)
      : undefined,
    plugins: { legend: {}, tooltip: {} },
  } as Record<string, unknown>;
}

export function axisMoney() {
  return { kind: "money" } as Record<string, unknown>;
}

export function axisPlain(size = 11.5) {
  return { kind: "plain", size } as Record<string, unknown>;
}

type Ds = {
  label?: string;
  data: unknown[];
  type?: string;
  backgroundColor?: string | string[];
  borderColor?: string;
  borderWidth?: number;
  fill?: boolean;
  isMoney?: boolean;
  yAxisID?: string;
  stack?: string;
};

type CjsConfig = {
  type: string;
  data: { labels?: unknown[]; datasets?: Ds[] };
  options?: Record<string, unknown>;
};

function up(v: unknown): string {
  return String(v ?? "").toUpperCase();
}

function adapt(cfg: CjsConfig): Record<string, unknown> {
  const ink = ecInk();
  const labels = (cfg.data?.labels ?? []).map(up);
  const ds = cfg.data?.datasets ?? [];
  const opts = (cfg.options ?? {}) as Record<string, never>;
  const horizontal = (opts as Record<string, unknown>).indexAxis === "y";
  const scales = ((opts as Record<string, unknown>).scales ?? {}) as Record<string, Record<string, unknown>>;
  const stacked = Boolean(scales.y?.stacked || scales.x?.stacked);
  const hasY1 = ds.some((d) => d.yAxisID === "y1");
  const legendCfg = (((opts as Record<string, unknown>).plugins ?? {}) as Record<string, Record<string, unknown>>).legend ?? {};
  const legendShow = legendCfg.display !== false && ds.length > 1;
  const moneyBySeries = ds.map((d) => Boolean(d.isMoney));
  const radius = (i: number): [number, number, number, number] =>
    horizontal ? [0, 6, 6, 0] : [6, 6, 0, 0];

  const isScatter = cfg.type === "scatter";
  const series = ds.map((d, si) => {
    const kind = d.type ?? cfg.type;
    const name = up(d.label ?? `Serie ${si + 1}`);
    const yIdx = d.yAxisID === "y1" ? 1 : 0;

    if (kind === "scatter") {
      // Chart.js: puntos {x,y} por dataset → ECharts: pares [x,y]
      const color = (Array.isArray(d.backgroundColor) ? d.backgroundColor[0] : d.backgroundColor)
        ?? d.borderColor ?? EC_PALETTE[si % EC_PALETTE.length];
      return {
        name, type: "scatter",
        data: (d.data as Array<{ x: number; y: number } | [number, number]>).map((pt) =>
          Array.isArray(pt) ? pt : [Number(pt?.x ?? 0), Number(pt?.y ?? 0)]),
        symbolSize: 13,
        itemStyle: { color, opacity: 0.88, shadowBlur: 5, shadowColor: "rgba(15,23,42,.25)" },
        emphasis: { scale: 1.4 },
      };
    }

    if (kind === "line") {
      const color = d.borderColor ?? EC_PALETTE[si % EC_PALETTE.length];
      return {
        name, type: "line", smooth: true, symbolSize: 5,
        yAxisIndex: hasY1 ? yIdx : 0,
        data: d.data,
        lineStyle: { width: d.borderWidth ?? 2.5, color },
        itemStyle: { color },
        areaStyle: d.fill ? { opacity: 0.12, color } : undefined,
      };
    }

    // Barras (vertical/horizontal, apiladas, color plano o por punto)
    const bg = d.backgroundColor;
    const perPoint = Array.isArray(bg);
    return {
      name, type: "bar",
      yAxisIndex: hasY1 ? yIdx : 0,
      stack: stacked ? "total" : undefined,
      barMaxWidth: 38,
      data: perPoint
        ? d.data.map((v, i) => ({
            value: v,
            itemStyle: { color: (bg as string[])[i % (bg as string[]).length], borderRadius: radius(i) },
          }))
        : d.data,
      itemStyle: perPoint
        ? undefined
        : { color: (bg as string) ?? EC_PALETTE[si % EC_PALETTE.length], borderRadius: radius(si) },
    };
  });

  const catAxis = {
    type: "category", data: labels,
    inverse: horizontal,
    axisLine: { show: false }, axisTick: { show: false },
    axisLabel: {
      color: ink.tick, fontFamily: FONT, fontSize: 11,
      hideOverlap: true,
      ...(horizontal ? { width: 150, overflow: "truncate" } : {}),
    },
  };
  const tickCb = ((scales.x?.ticks as Record<string, unknown>)?.callback
    ?? (scales.y?.ticks as Record<string, unknown>)?.callback) as ((v: number) => string) | undefined;
  const valAxis = {
    type: "value",
    splitLine: { lineStyle: { color: ink.grid } },
    axisLabel: {
      color: ink.tick, fontFamily: FONT, fontSize: 10.5,
      formatter: tickCb
        ? (v: number) => String(tickCb(v))
        : (v: number) => new Intl.NumberFormat("en-US", { notation: Math.abs(v) >= 100000 ? "compact" : "standard" }).format(v),
    },
  };
  const y1Axis = {
    type: "value", position: "right",
    splitLine: { show: false },
    axisLabel: { color: ink.tick, fontFamily: FONT, fontSize: 10.5 },
  };

  return {
    color: EC_PALETTE,
    textStyle: { fontFamily: FONT, color: ink.txt },
    animationDuration: 320,
    aria: { enabled: true },
    legend: legendShow ? { top: 0, textStyle: { color: ink.tick, fontSize: 11 } } : { show: false },
    tooltip: {
      trigger: isScatter ? "item" : "axis",
      backgroundColor: "rgba(10,16,30,.97)", borderWidth: 0,
      textStyle: { color: "#e2e8f0", fontFamily: FONT, fontSize: 12.5 },
      padding: [10, 14],
      formatter: (params: Array<{ marker: string; seriesName: string; seriesIndex: number; dataIndex: number; value: unknown; name: string; data: unknown }>) => {
        const list = Array.isArray(params) ? params : [params];
        if (isScatter) {
          return list.map((pp) => {
            const pair = Array.isArray(pp.value) ? pp.value as [number, number] : [0, 0];
            // El único scatter legacy es venta×utilidad: ambos ejes en MXN.
            return `${pp.marker} <b>${pp.seriesName}</b><br/>Venta ${money(pair[0])} · Utilidad ${money(pair[1])}`;
          }).join("<br/>");
        }
        const head = list[0]?.name ? `${list[0].name}<br/>` : "";
        const tipCb = ((((opts as Record<string, unknown>).plugins ?? {}) as Record<string, Record<string, unknown>>)
          .tooltip?.callbacks as Record<string, unknown> | undefined)?.label as
          ((ctx: Record<string, unknown>) => string) | undefined;
        return head + list.map((pp) => {
          const raw = (pp.data && typeof pp.data === "object" && pp.data !== null && "value" in (pp.data as Record<string, unknown>))
            ? (pp.data as Record<string, unknown>).value : pp.value;
          const v = typeof raw === "number" ? raw : Number(raw ?? 0);
          if (tipCb) {
            try {
              return `${pp.marker} ${String(tipCb({ dataIndex: pp.dataIndex, parsed: { x: v, y: v }, dataset: { label: pp.seriesName } }))}`;
            } catch { /* cae al formato estándar */ }
          }
          const txt = moneyBySeries[pp.seriesIndex] ? money(v) : n1(v);
          return `${pp.marker} ${pp.seriesName}: <b>${txt}</b>`;
        }).join("<br/>");
      },
    },
    grid: { left: 8, right: hasY1 ? 40 : 16, top: legendShow ? 30 : 14, bottom: 8, containLabel: true },
    xAxis: isScatter ? { ...valAxis } : (horizontal ? valAxis : catAxis),
    yAxis: isScatter ? { ...valAxis } : (horizontal ? catAxis : (hasY1 ? [valAxis, y1Axis] : valAxis)),
    series,
  };
}

export function ChartBox(props: { config: unknown | (() => unknown); height?: number; deps: unknown[] }) {
  const cfg = (typeof props.config === "function" ? (props.config as () => unknown)() : props.config) as CjsConfig;
  const option = adapt(cfg);
  const cjsClick = (cfg.options as Record<string, unknown> | undefined)?.onClick as
    | ((e: unknown, els: Array<{ index: number; datasetIndex: number }>) => void)
    | undefined;
  return (
    <EChartBox
      height={props.height ?? 280}
      deps={props.deps}
      option={option}
      onClick={cjsClick
        ? (p) => cjsClick(null, [{ index: p.dataIndex, datasetIndex: p.seriesIndex }])
        : undefined}
    />
  );
}
