# dashboard_src — FUENTE OBSOLETA, NO RECONSTRUIR

El bundle desplegado `static/dashboard/som_dashboard.js` está **adelante**
de este `src/`. Se editó directamente sobre el bundle (igual que el portal
React) y desde entonces `src/main.tsx` quedó atrás.

Diferencias conocidas (lo que este `src/` **NO** tiene y el bundle **SÍ**):

- Filtro global de ORIGEN en la barra de filtros: `Odoo | SPS | Mixto`
  (`filters.source`). Aquí todavía existe el switch viejo, local y de dos
  posiciones, dentro de `VentasView`.
- Centro de Comando y el wrapper de datos comerciales ya no fuerzan
  `source: "odoo"`.
- El ticker ejecutivo (`exec`) recibe los filtros.

**Correr `npm run build` aquí PISA el bundle y regresa el dashboard varias
versiones atrás.** Para cambiar el dashboard: editar
`static/dashboard/som_dashboard.js`, validar con `node --check` y subir la
versión en `__manifest__.py` (cache-bust: la URL del bundle lleva la
versión instalada del módulo).
