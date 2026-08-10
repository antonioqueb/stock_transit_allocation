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
      // Command Center (F2): portada universal, responsivo. El Resumen TV
      // se conserva como modo pantalla-completa de escritorio.
      { key: "inicio", label: "Command Center", question: "¿Cuál es el estado integral del negocio y qué necesita atención hoy?" },
      { key: "resumen", label: "Resumen TV", question: "Modo pantalla completa para sala/TV (solo escritorio).", desktopOnly: true },
    ],
  },
  {
    id: "ventas",
    label: "Ventas",
    pages: [
      { key: "ventas", label: "Visión", question: "¿Estamos vendiendo más, mejor y de forma rentable?" },
      { key: "ventas_conversion", label: "Conversión", question: "¿Dónde se pierden oportunidades y cuánto dinero está detenido?" },
      { key: "ventas_clientes", label: "Clientes", question: "¿Qué clientes crecen, concentran riesgo o dejan de comprar?" },
      { key: "ventas_productos", label: "Productos", question: "¿Qué productos explican el crecimiento y la rentabilidad?" },
      { key: "ventas_precios", label: "Precios y margen", question: "¿Dónde se erosiona el precio y qué excepciones destruyen margen?" },
      { key: "ventas_auth", label: "Autorizaciones", question: "¿Cuánto negocio está bloqueado y dónde se incumple el SLA?" },
      { key: "ventas_equipo", label: "Vendedores", question: "¿El costo comercial está alineado con el valor generado?" },
      { key: "ventas_canales", label: "Embajadores", question: "¿Qué canal origina negocio de calidad?" },
      { key: "ventas_fx", label: "Cambiario (FX)", question: "¿Qué venta USD no cobrada está expuesta y cuál fue el FX realizado?" },
    ],
  },
  {
    id: "inventario",
    label: "Inventario",
    pages: [
      { key: "inventario", label: "Visión", question: "¿Cuánto capital está disponible, apartado o envejecido?" },
      { key: "materiales", label: "Rotación", question: "¿Qué tan rápido se vuelve venta el inventario?" },
    ],
  },
  {
    id: "abastecimiento",
    label: "Abastecimiento",
    pages: [
      { key: "compras", label: "Compras", question: "¿Compramos al ritmo correcto y qué proveedor cumple?" },
      { key: "transito", label: "Tránsito", question: "¿Qué viene, cuándo llega y qué está retrasado?" },
      { key: "recepciones", label: "Recepciones", question: "¿Qué entró, con qué exactitud y qué faltó?" },
    ],
  },
  {
    id: "operaciones",
    label: "Operaciones",
    pages: [
      { key: "taller", label: "Taller", question: "¿Cuánto trabajo hay en proceso y con cuánta merma?" },
      { key: "entregas", label: "Entregas", question: "¿Qué está en ruta, firmado y cobrado al entregar?" },
    ],
  },
  {
    id: "finanzas",
    label: "Finanzas",
    pages: [
      { key: "finanzas", label: "Visión", question: "¿La venta se está convirtiendo en efectivo?" },
    ],
  },
  {
    id: "inteligencia",
    label: "Inteligencia",
    pages: [
      { key: "pronosticos", label: "Pronósticos", question: "¿Qué viene y alcanza el inventario y la caja?" },
    ],
  },
  {
    id: "control",
    label: "Control",
    pages: [
      { key: "control", label: "Bandeja", question: "¿Qué pendientes y deudas de datos requieren atención?" },
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
