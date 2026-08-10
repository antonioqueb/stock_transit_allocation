// ─────────────────────────────────────────────────────────────────────────────
// Navegación declarativa de SOM Analytics (Fase 1 del rediseño).
//
// Arquitectura de información: dominio → página (máx. 2 niveles visibles).
// PARIDAD TOTAL: cada página apunta a una vista YA existente — ninguna vista
// se reescribe en esta fase y ningún contenido queda huérfano. Las páginas
// futuras del sitemap (ver docs/ANALYTICS_REDISENO_FASE0.md) se agregarán
// aquí cuando existan; no se listan entradas vacías.
// ─────────────────────────────────────────────────────────────────────────────

export type NavPage = {
  key: string;          // ViewKey existente (ruta en el hash)
  label: string;
  question: string;     // pregunta de negocio que responde la página
  desktopOnly?: boolean;
};

export type NavDomain = {
  id: string;
  label: string;
  pages: NavPage[];
};

export const NAV: NavDomain[] = [
  {
    id: "inicio",
    label: "Inicio",
    pages: [
      // El Resumen actual (formato TV) es la portada de escritorio; el
      // Command Center móvil llega en Fase 2 y será portada universal.
      { key: "resumen", label: "Command Center", question: "¿Cuál es el estado integral del negocio hoy?", desktopOnly: true },
    ],
  },
  {
    id: "ventas",
    label: "Ventas",
    pages: [
      { key: "ventas", label: "Visión comercial", question: "¿Estamos vendiendo más, mejor y de forma rentable?" },
    ],
  },
  {
    id: "inventario",
    label: "Inventario y materiales",
    pages: [
      { key: "inventario", label: "Visión de inventario", question: "¿Cuánto capital está disponible, apartado o envejecido?" },
      { key: "materiales", label: "Rotación y tiempo para vender", question: "¿Qué tan rápido se vuelve venta el inventario?" },
    ],
  },
  {
    id: "abastecimiento",
    label: "Abastecimiento y tránsito",
    pages: [
      { key: "compras", label: "Compras y proveedores", question: "¿Compramos al ritmo correcto y qué proveedor cumple?" },
      { key: "transito", label: "Tránsito y ETA", question: "¿Qué viene, cuándo llega y qué está retrasado?" },
      { key: "recepciones", label: "Recepciones y discrepancias", question: "¿Qué entró, con qué exactitud y qué faltó?" },
    ],
  },
  {
    id: "operaciones",
    label: "Operaciones",
    pages: [
      { key: "taller", label: "Taller y WIP", question: "¿Cuánto trabajo hay en proceso y con cuánta merma?" },
      { key: "entregas", label: "Entregas en curso", question: "¿Qué está en ruta, firmado y cobrado al entregar?" },
    ],
  },
  {
    id: "finanzas",
    label: "Finanzas",
    pages: [
      { key: "finanzas", label: "Visión financiera", question: "¿La venta se está convirtiendo en efectivo?" },
    ],
  },
  {
    id: "inteligencia",
    label: "Inteligencia y planeación",
    pages: [
      { key: "pronosticos", label: "Pronósticos y cobertura", question: "¿Qué viene y alcanza el inventario y la caja?" },
    ],
  },
  {
    id: "control",
    label: "Control y gobierno",
    pages: [
      { key: "control", label: "Bandeja de control", question: "¿Qué pendientes y deudas de datos requieren atención?" },
    ],
  },
];

export function domainOf(viewKey: string): NavDomain | undefined {
  return NAV.find((d) => d.pages.some((p) => p.key === viewKey));
}

export function pageOf(viewKey: string): NavPage | undefined {
  for (const d of NAV) {
    const p = d.pages.find((x) => x.key === viewKey);
    if (p) return p;
  }
  return undefined;
}
