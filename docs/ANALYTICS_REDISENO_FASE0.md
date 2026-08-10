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
