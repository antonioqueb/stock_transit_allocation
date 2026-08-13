/** @odoo-module **/

// FORMATO DE FECHA ÚNICO DEL SISTEMA: "13 ago 2026".
// Día en número, mes abreviado en palabra (español) y año en número.
// Nada de dd/mm/yyyy ni ISO en pantalla: en una tabla densa el mes en
// palabra elimina la ambigüedad día/mes de un vistazo.
//
// El parseo es MANUAL a propósito. `new Date("2026-08-13")` interpreta la
// cadena como UTC y en México (UTC-6) devuelve el día ANTERIOR: un ETA se
// vería corrido un día. Aquí se parten los componentes de texto y jamás se
// construye un Date, así que no hay corrimiento posible.

const MESES_ES = ["ene", "feb", "mar", "abr", "may", "jun",
                  "jul", "ago", "sep", "oct", "nov", "dic"];

/**
 * @param {string|Date|false} value  Fecha ISO ("2026-08-13" o
 *        "2026-08-13 14:30:00"), un Date nativo, o vacío.
 * @param {Object} [options]
 * @param {string} [options.empty="—"]   Qué devolver si no hay fecha.
 * @param {boolean} [options.withTime=false]  Agrega " 14:30" al final.
 * @returns {string} "13 ago 2026" (o "13 ago 2026 14:30" con withTime).
 */
export function somFormatDate(value, options) {
    const opts = options || {};
    const empty = opts.empty !== undefined ? opts.empty : "—";
    if (!value) return empty;

    let raw = "";
    if (typeof value === "string") {
        raw = value.trim();
    } else if (value instanceof Date && !isNaN(value)) {
        // Se compone desde los getters LOCALES: toISOString() pasaría por
        // UTC y reintroduciría el corrimiento de día que evitamos arriba.
        const p2 = (n) => String(n).padStart(2, "0");
        raw = `${value.getFullYear()}-${p2(value.getMonth() + 1)}-${p2(value.getDate())}` +
              ` ${p2(value.getHours())}:${p2(value.getMinutes())}`;
    }
    if (!raw) return empty;

    const [datePart, timePart] = raw.split(/[ T]/);
    const parts = datePart.split("-");
    if (parts.length !== 3) return raw;

    const year = parseInt(parts[0], 10);
    const month = parseInt(parts[1], 10);
    const day = parseInt(parts[2], 10);
    if (!year || !month || !day || month < 1 || month > 12) return raw;

    // Día con dos dígitos: mantiene alineadas las columnas de fecha en las
    // tablas densas (cronograma, torre de control).
    let out = `${String(day).padStart(2, "0")} ${MESES_ES[month - 1]} ${year}`;
    if (opts.withTime && timePart) out += ` ${timePart.slice(0, 5)}`;
    return out;
}

/** Atajo con hora: "13 ago 2026 14:30". */
export function somFormatDateTime(value, options) {
    return somFormatDate(value, Object.assign({}, options, { withTime: true }));
}
