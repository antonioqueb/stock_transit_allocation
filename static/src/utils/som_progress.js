/** @odoo-module **/
/**
 * SOM Progress — barra de progreso de operaciones largas (branding SOM).
 *
 * Overlay a pantalla completa con pasos nombrados y barra animada. Las RPC de
 * Odoo no reportan avance real, así que la barra "gotea" hacia el tope del
 * paso activo y solo llega a 100% cuando el caller confirma el final. El
 * objetivo es que el usuario SIEMPRE sepa que el proceso vive (cronómetro,
 * paso activo, aviso si tarda de más) o que se rompió (estado de error).
 *
 * Uso:
 *   const prog = somProgress({
 *       title: "Guardando asignación",
 *       steps: ["Validando selección", "Asignando material", "Actualizando tablero"],
 *   });
 *   prog.step();                    // avanza al siguiente paso
 *   prog.done("Listo");            // 100% + éxito + se desvanece
 *   prog.fail("mensaje de error"); // estado de error + se desvanece
 *
 * Vanilla DOM a propósito: se usa igual desde componentes OWL que desde los
 * popups renderizados a mano (innerHTML) de los hubs.
 */

let _activeRoot = null;

function _destroyActive() {
    if (_activeRoot) {
        clearInterval(_activeRoot._somTicker);
        _activeRoot.remove();
        _activeRoot = null;
    }
}

function _esc(str) {
    return String(str ?? "").replace(/[&<>"']/g, (c) => (
        { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
}

export function somProgress({ title = "Procesando", subtitle = "", steps = [] } = {}) {
    _destroyActive();

    const stepList = steps.length ? steps : ["Procesando"];
    const root = document.createElement("div");
    root.className = "som-progress-root";
    document.body.appendChild(root);
    _activeRoot = root;

    root.innerHTML = `
        <div class="som-progress-overlay">
            <div class="som-progress-card" role="status" aria-live="polite">
                <div class="som-progress-head">
                    <div class="som-progress-logo">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                             stroke-linecap="round" stroke-linejoin="round">
                            <path d="M12 2v4M12 18v4M2 12h4M18 12h4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8"/>
                        </svg>
                    </div>
                    <div class="som-progress-titles">
                        <div class="som-progress-title">${_esc(title)}</div>
                        <div class="som-progress-subtitle">${_esc(subtitle)}</div>
                    </div>
                    <div class="som-progress-elapsed" data-elapsed>0 s</div>
                </div>

                <div class="som-progress-track">
                    <div class="som-progress-fill" data-fill style="width: 2%">
                        <div class="som-progress-shimmer"></div>
                    </div>
                </div>
                <div class="som-progress-pct" data-pct>0%</div>

                <ul class="som-progress-steps">
                    ${stepList.map((s, i) => `
                        <li class="som-progress-step ${i === 0 ? "is-active" : ""}" data-step="${i}">
                            <span class="som-progress-step-dot">
                                <svg class="som-progress-step-check" viewBox="0 0 24 24" fill="none"
                                     stroke="currentColor" stroke-width="3" stroke-linecap="round"
                                     stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                            </span>
                            <span class="som-progress-step-label">${_esc(s)}</span>
                        </li>`).join("")}
                </ul>

                <div class="som-progress-hint" data-hint></div>
            </div>
        </div>`;

    const fillEl = root.querySelector("[data-fill]");
    const pctEl = root.querySelector("[data-pct]");
    const elapsedEl = root.querySelector("[data-elapsed]");
    const hintEl = root.querySelector("[data-hint]");
    const cardEl = root.querySelector(".som-progress-card");

    const startedAt = performance.now();
    let stepStartedAt = startedAt;
    let currentStep = 0;
    let pct = 2;
    let finished = false;

    // Tope de la barra por paso: reparte 0→92% entre los pasos; el 100% solo
    // lo entrega done(). Goteo asintótico: avanza rápido al inicio del paso y
    // se frena cerca del tope (nunca se ve congelada, nunca "miente" el 100%).
    const capFor = (i) => ((i + 1) / stepList.length) * 92;

    const paint = () => {
        fillEl.style.width = pct.toFixed(1) + "%";
        pctEl.textContent = Math.round(pct) + "%";
    };

    root._somTicker = setInterval(() => {
        if (finished) return;
        const cap = capFor(currentStep);
        pct = Math.min(cap, pct + Math.max(0.08, (cap - pct) * 0.045));
        paint();

        const totalSec = Math.round((performance.now() - startedAt) / 1000);
        elapsedEl.textContent = totalSec + " s";

        const stepSec = (performance.now() - stepStartedAt) / 1000;
        if (stepSec > 20) {
            hintEl.textContent =
                "Está tardando más de lo normal, pero el proceso sigue vivo. No cierres la ventana.";
            hintEl.classList.add("is-warn");
        }
    }, 120);

    const markStep = (i, state) => {
        const el = root.querySelector(`[data-step="${i}"]`);
        if (!el) return;
        el.classList.remove("is-active", "is-done", "is-error");
        if (state) el.classList.add(state);
    };

    const finish = (ok, message) => {
        if (finished || _activeRoot !== root) return;
        finished = true;
        clearInterval(root._somTicker);
        stepList.forEach((_, i) => markStep(i, ok ? "is-done" : (i < currentStep ? "is-done" : (i === currentStep ? "is-error" : ""))));
        pct = ok ? 100 : pct;
        paint();
        cardEl.classList.add(ok ? "is-success" : "is-failure");
        hintEl.classList.toggle("is-warn", false);
        hintEl.classList.toggle("is-error", !ok);
        hintEl.textContent = message || (ok ? "Completado" : "El proceso se detuvo con un error.");
        setTimeout(() => {
            root.classList.add("som-progress-fade");
            setTimeout(() => { if (_activeRoot === root) _destroyActive(); }, 450);
        }, ok ? 650 : 2600);
    };

    return {
        /** Avanza al siguiente paso (o al índice dado). */
        step(index) {
            if (finished || _activeRoot !== root) return;
            const next = index !== undefined ? index : currentStep + 1;
            if (next <= currentStep || next >= stepList.length) return;
            for (let i = currentStep; i < next; i++) markStep(i, "is-done");
            currentStep = next;
            stepStartedAt = performance.now();
            hintEl.textContent = "";
            hintEl.classList.remove("is-warn");
            markStep(currentStep, "is-active");
            // Salto visible al entrar al paso: que se NOTE el avance.
            pct = Math.max(pct, capFor(currentStep - 1));
            paint();
        },
        done(message) { finish(true, message); },
        fail(message) { finish(false, message); },
        /** Cierre inmediato sin animación (p. ej. cancelación del usuario). */
        destroy() { if (_activeRoot === root) _destroyActive(); },
    };
}
