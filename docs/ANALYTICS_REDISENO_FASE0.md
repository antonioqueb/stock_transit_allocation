# SOM Analytics — Rediseño: Informe de Fase 0 y plan de fases

> 2026-08-16 · Auditoría previa a implementación, según el prompt maestro.
> Inventario completo de KPIs con fórmula/fuente/por qué: ver
> `../../SOM_ANALYTICS_KPIS.md` (raíz de Módulos). Este documento cubre la
> arquitectura, el mapa de migración, brechas y el plan.

## 1. Arquitectura actual (verificada en código)

- **Backend**: `models/som_analytics.py` — AbstractModel `som.analytics`.
  RPCs: `get_dashboard(domain, filters)` con `_dom_*` por pestaña,
  `get_exec_summary` (ticker 60 s), `get_bank_balances`, `get_time_to_sell`,
  `get_drill(entity, value, label, filters)`, `set_product_cost`.
  SQL parametrizado vía `_sq`; TZ Monterrey en `_bounds`; costo all-in
  company-dependent (`_cid`); acceso restringido en `_check_access`
  (Autorizadores de Precios / grupo dashboard).
- **Frontend**: React 18 **real** (no Owl) + `@tanstack/react-query` + zod
  (contratos en `api.ts`) + Chart.js **vendorizado por Odoo** (`window.Chart`
  vía `ChartBox`). Bundle propio con esbuild (`dashboard_src/build.mjs` →
  `static/dashboard/som_dashboard.js`). Routing por **hash**
  (`#view=…&date_from=…`), ya profundo y compartible.
- **Navegación previa**: lista plana de 12 vistas (`VIEWS`) en un sidenav;
  ticker TV en Resumen; móvil oculta Resumen y fuerza Ventas (guardián
  matchMedia ≤760px).
- **Deuda observada**: vistas monolíticas en `main.tsx` (~2,200 líneas, sin
  code splitting real), fórmulas solo en backend (bien) pero sin registro
  semántico consultable, tablas sin virtualización, sin estados de permiso
  diferenciados en UI (el backend ya restringe), doble fuente de verdad del
  breakpoint móvil (CSS y JS, ya unificados en 760px).

## 2. Mapa de migración (paridad — nada huérfano)

| Vista actual | Destino Fase 1 (implementado) | Destinos futuros |
|---|---|---|
| Resumen (TV) | Inicio / **Command Center** (solo escritorio) | Command Center móvil (F2) |
| Ventas | Ventas / Visión comercial | 9 subpáginas de Ventas (F2) |
| Materiales | Inventario / Rotación y tiempo para vender | Materiales y desempeño (F3) |
| Inventario | Inventario / Visión de inventario | Disponibilidad, aging, valoración (F3) |
| Compras | Abastecimiento / Compras y proveedores | Costos logísticos (F3) |
| Tránsito | Abastecimiento / Tránsito y ETA | Portal de proveedores (F3) |
| Recepciones | Abastecimiento / Recepciones y discrepancias | Pedimentos (F3) |
| Taller | Operaciones / Taller y WIP | Merma y reclasificaciones (F4) |
| Entregas | Operaciones / Entregas en curso | Cumplimiento, rutas, devoluciones (F4) |
| Finanzas | Finanzas / Visión financiera | Facturación, AR, AP, bancos, flujo (F4) |
| Pronósticos | Inteligencia / Pronósticos y cobertura | Escenarios, anomalías (F5) |
| Control | Control / Bandeja de control | Calidad, SLA, metodología (F5) |
| Ticker | Se conserva en Resumen | Barra vital contextual ≤4 señales (F2) |

**Decisión móvil documentada**: el Resumen actual es un tablero TV no apto
para móvil; se mantiene desktop-only y el guardián Ventas-en-móvil sigue
activo. El **Command Center de Fase 2** (nuevo, responsivo) sustituirá esa
regla y será portada universal — reconcilia el prompt maestro con la
instrucción previa del negocio.

## 3. Brechas (clasificación A/B/C)

- **A (solo re-presentación)**: todo el inventario de KPIs actual (ver doc
  de KPIs); Pareto clientes/productos; aging; small multiples.
- **B (derivable hoy)**: ticket promedio/mediano; PVM (precio-volumen-mix);
  cohortes de conversión y de recurrencia (con historia sale.order);
  concentración Top-N/HHI; percentiles de time-to-sell; funnel cotización
  (draft→sent→sale + price_authorization); sell-through 30/60/90/180;
  aging de previas; DPO básico.
- **C (requiere instrumentación — NO activar con ceros falsos)**:
  snapshots de inventario (GMROI, variación de valoración, waterfall de
  inventario envejecido); **metas por vendedor** (modelo de objetivos);
  **fecha/cantidad prometida** para OTIF y on-time delivery; probabilidad
  de cierre del pipeline; geometría real de bodega (mapa); saldos iniciales
  para CEI; genealogía completa first-pass-yield de taller; eventos de
  negocio anotables (cambios de escalera/póliza) como registro formal.

## 4. Plan de archivos y fases

- **F1 (este commit)**: `dashboard_src/src/nav.ts` (navegación declarativa
  dominio→página), sidebar anidado en `main.tsx` (un dominio expandido,
  preferencia persistida, aria-expanded/aria-current, breadcrumb con
  pregunta de negocio), estilos `.nav-domain/.nav-children/.crumbs`,
  escape hatch `localStorage.som_nav_legacy='1'` → nav plana anterior.
  Cero cambios en vistas ni en backend ⇒ paridad numérica por construcción.
- **F2**: Command Center responsivo (page nueva) + barra vital contextual +
  subpáginas de Ventas + registro semántico de métricas (`metrics.ts` +
  espejo backend) + contrato de datos normalizado.
- **F3–F5**: según sitemap del prompt (dominios Inventario/Abastecimiento,
  Operaciones/Finanzas, Inteligencia/Control), cada página con la plantilla
  narrativa (pregunta → KPIs → visual principal → diagnósticos → tabla →
  estados) y drill-through heredando filtros.
- **F6**: hardening (rendimiento medido, WCAG 2.2 AA en rutas críticas,
  E2E, telemetría, retiro de legacy tras adopción).

## 5. Riesgos y rollback

- **Rollback F1**: `som_nav_legacy=1` en localStorage (sin deploy) o revert
  del commit; el hash routing no cambió, los deep links viejos siguen
  funcionando (mismas `view=` keys).
- **Riesgos F2+**: volumen de `main.tsx` (conviene dividir por página antes
  de F2), costo de queries nuevas (medir con EXPLAIN en QA, nunca en prod),
  privacidad de utilidad al normalizar contratos (enmascarar en backend),
  y las brechas C que tientan a mostrar ceros — prohibido.
- **Línea base numérica**: los fixtures de regresión deben capturarse en QA
  (`qa.recubrimientos.app`) con periodos cerrados conocidos antes de F2;
  desde este repositorio no hay acceso a la base para generarlos.

---

## 6. Bitácora de fases

- **F1 — completada** (v19.0.47.0.0): sidebar anidado declarativo, breadcrumbs,
  rollback `som_nav_legacy`.
- **F2 — completada** (v19.0.48.0.0):
  - `metrics.ts`: registro semántico (unidad, dirección favorable, fórmula,
    fuentes, frescura) + tooltip "explicar métrica" + tono por dirección
    (nunca verde solo por subir).
  - **Command Center** responsivo (`inicio`): portada universal (también
    móvil — sustituye la regla Ventas-en-móvil); scorecard de 8 señales +
    "Ver todos", hallazgos determinísticos (InsightStrip con drill), serie
    venta/utilidad, capital comprometido (liquidez vs no líquido separados)
    y "Atención hoy" desde la bandeja de control. El Resumen TV se conserva
    como página de escritorio.
  - **Barra vital contextual**: ≤4 señales según dominio activo + "Ver
    resumen"; comparte caché del exec (una sola consulta, 60 s, pausada con
    pestaña oculta).
  - **8 subpáginas de Ventas** (todas capacidad A, reutilizan el pack
    'comercial' con la MISMA query key ⇒ cero consultas duplicadas):
    conversión, clientes y concentración (insight Top-5), productos y mix
    (m² y piezas separados), precios/descuentos/margen (insight presión de
    descuento), autorizaciones (precio separado de IVA), vendedores y
    comisiones (scatter venta×utilidad + % costo comercial), embajadores y
    canales, exposición cambiaria (sensibilidad paramétrica no contable).
  - Brechas B declaradas EN la UI sin cifras simuladas: funnel/cohortes de
    conversión y series semanales de autorizaciones (requieren nuevas
    series del backend — candidatas a F2.1).
- **Pendiente próximo**: F2.1 (series backend para funnel/cohortes/control
  charts), fixtures de línea base en QA, luego F3.
- **F2.1 — completada** (v19.0.49.0.0):
  - **SEGURIDAD (payload)**: `_scrub_profit` en el modelo — para usuarios
    SIN permiso de Autorizador de Precios, utilidad/margen/costos y el
    valor de inventario (reconstruye costo) viajan como null desde el
    SERVIDOR en todos los RPCs (dashboard, exec, drill y atajos,
    time_to_sell, order_lines); `set_product_cost` ya estaba blindado. El
    front recibe `perm_profit` y NO pinta esos elementos (sin ceros
    falsos) en: ticker TV, Command Center (tarjeta y serie), subpáginas
    de Ventas (margen, top-productos cambia a venta, scatter del equipo
    se sustituye por tabla de venta) y drill de orden (columnas de costo
    ocultas). LIMITACIÓN documentada: vistas legacy (Ventas visión,
    Materiales, paneles TV) pueden mostrar $0 en campos enmascarados para
    visores nivel 1 — la seguridad es de servidor; la limpieza cosmética
    completa va en el sweep de F6.
  - **Series nuevas clase B en `_dom_comercial`**: funnel de cotizaciones
    creadas (borrador→enviada→confirmada + canceladas aparte), aging del
    backlog abierto (0-7/8-14/15-30/>30), top cotizaciones estancadas,
    flujo semanal de autorizaciones (entradas vs resueltas por write_date
    de estados terminales) y percentiles P50/P75/P90 de resolución
    (percentile_cont — el promedio esconde extremos).
  - Conversión y Autorizaciones estrenan sus visuales (funnel, aging,
    estancadas con insight, flujo semanal, percentiles en el KPI) — las
    dos brechas B declaradas en F2 quedan cerradas.
- **F2.2 — completada** (v19.0.50.0.0): **Apache ECharts 6.1 vendorizado**
  (tree-shaken vía echarts/core, componente ARIA habilitado, tema
  claro/oscuro de la casa, resize observer). Catálogo registrado: bar,
  line, pie/rosa, scatter, funnel, treemap, radar, gauge, sankey,
  heatmap, boxplot, sunburst (los 4 últimos listos para F3). Visuales
  modernizados: funnel real de conversión, gauge de tasa de conversión,
  pareto clientes con % acumulado, treemap de productos coloreado por
  margen (con permiso), rosa de Nightingale de niveles de precio, radar
  comparativo del top-6 de vendedores, combos barra+línea con área.
  Chart.js sigue para vistas legacy. Cards con drill a un nivel más.
- **F2.3 — completada** (v19.0.52.0.0): catálogo ECharts 6 integrado con
  datos reales — funnel CUSTOM de trapecios continuos (degradado, sombra,
  métricas internas, % del total y tasa real de cobro), sankey gradiente
  del dinero cotizado (creadas→estado→cobrado/por cobrar), calendar
  heatmap de pedidos diarios en Command Center (serie daily_sales nueva),
  chord clientes↔categorías + beeswarm de órdenes con drill en Clientes
  (series client_categ y orders), jerarquía categoría→producto con toggle
  sunburst⇄treemap en Productos (serie categ_products, derivada de rows
  sin SQL extra), y gantt custom ETD→ETA de embarques en Tránsito (rojo =
  ETA vencida). Registrados además ThemeRiver/PictorialBar/Matrix/
  DataZoom/Brush para siguientes páginas. PENDIENTES por datos (brecha B/C
  documentada): Matrix+sparklines y matriz de correlación (requieren
  series mensuales por entidad), jitter nativo de eje (beeswarm manual
  determinístico en su lugar), fisheye y axis-breaks (se adoptarán donde
  la serie lo amerite en F3).
