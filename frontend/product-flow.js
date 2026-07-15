import { escapeHtml } from "./helpers.js";

const STEP_LABELS = {
  concurso_actual: "Concurso",
  estado_de_datos: "Datos",
  recomendacion: "Recomendación",
  boleto: "Boleto",
  seguimiento: "Seguimiento",
  postmortem: "Postmortem",
};

function toneForStatus(status) {
  if (["ready", "jugar", "play_balanced", "play_minimum_conservative"].includes(status)) return "ok";
  if (["blocked", "do_not_play", "no_jugar"].includes(status)) return "danger";
  return "warn";
}

function stat(label, value) {
  return `<div class="product-flow-stat"><strong>${escapeHtml(label)}</strong><span>${escapeHtml(value ?? "—")}</span></div>`;
}

function renderSteps(steps = []) {
  return `
    <div class="product-flow-steps">
      ${steps
        .map((step) => {
          const tone = toneForStatus(step.status);
          return `<div class="product-flow-step tone-${escapeHtml(tone)}"><strong>${escapeHtml(STEP_LABELS[step.step] || step.step)}</strong><span>${escapeHtml(step.status)}</span></div>`;
        })
        .join("")}
    </div>`;
}

function renderCurrent(current) {
  if (!current) {
    return `<div class="empty-state">No hay concurso activo para el flujo de producto.</div>`;
  }
  const slate = current.slate || {};
  const quality = current.data_quality || {};
  const reco = current.recommendation || {};
  const policy = current.betting_policy || {};
  const drift = current.drift_audit || {};
  const gate = current.publication_gate || null;
  const explanation = reco.explanation || {};
  return `
    <div class="product-flow-grid">
      <section class="product-flow-block">
        <h3>${escapeHtml(slate.draw_code || "Concurso")}</h3>
        ${stat("Tipo", slate.week_type)}
        ${stat("Partidos", slate.match_count)}
        ${stat("Lineage", slate.classification)}
      </section>
      <section class="product-flow-block">
        <h3>Calidad de datos</h3>
        ${stat("Score", `${quality.score ?? "—"} · ${quality.level || "—"}`)}
        ${stat("Predicción", quality.prediction_status)}
        ${stat("Resumen", quality.summary)}
      </section>
      <section class="product-flow-block">
        <h3>Recomendación</h3>
        ${stat("Final", reco.final_recommendation)}
        ${stat("Boleto", reco.recommended_ticket || "—")}
        ${stat("Motivo", explanation.primary_reason || explanation.why_not_play || "—")}
      </section>
      <section class="product-flow-block">
        <h3>Política de apuesta</h3>
        ${stat("Acción", policy.action)}
        ${stat("No jugar", policy.hard_no_play ? "sí" : "no")}
        ${stat("Combinaciones máx.", policy.max_combinations)}
      </section>
      ${renderPublicationGate(gate)}
      <section class="product-flow-block">
        <h3>Drift</h3>
        ${stat("Estado", drift.status)}
        ${stat("Señales", (drift.signals || []).join(", ") || "sin señales")}
      </section>
      <section class="product-flow-block">
        <h3>Slate activa</h3>
        ${stat("Estricto", current.active_slate_contract?.strict ? "sí" : "no")}
        ${stat("Activas", current.active_slate_contract?.active_count)}
        ${stat("Violaciones", (current.active_slate_contract?.violations || []).join(", ") || "ninguna")}
      </section>
    </div>`;
}

function renderPublicationGate(gate) {
  if (!gate) return "";
  const debt = gate.data_debt || {};
  return `
      <section class="product-flow-block">
        <h3>Gate de publicación</h3>
        ${stat("Estado", gate.status)}
        ${stat("Publicar", gate.publish_allowed ? "sí" : "no")}
        ${stat("Placeholders", debt.placeholder_count ?? 0)}
        ${stat("Bloqueados", debt.blocked_count ?? 0)}
        ${stat("ML", gate.ml_activation_gate?.activation_allowed ? "activable" : "bloqueado")}
      </section>`;
}

function renderPostmortem(postmortem) {
  if (!postmortem) return "";
  const latest = postmortem.latest_validation || {};
  const score = postmortem.latest_score?.score || {};
  return `
    <section class="product-flow-postmortem">
      <h3>Postmortem y aprendizaje</h3>
      <div class="product-flow-grid compact">
        ${stat("Slates cerradas", postmortem.completed_slate_count)}
        ${stat("Listas para aplicar", postmortem.ready_to_apply_count)}
        ${stat("Última", latest.draw_code || "—")}
        ${stat("Hits", score.total ? `${score.hits}/${score.total}` : "—")}
        ${stat("Siguiente", postmortem.next_step)}
      </div>
    </section>`;
}

export function renderProductFlowPanel(flow) {
  if (!flow || flow.mode !== "product_flow") {
    return `<div class="empty-state">Sin flujo de producto disponible.</div>`;
  }
  const actions = Array.isArray(flow.next_actions) ? flow.next_actions : [];
  return `
    <div class="product-flow-panel">
      ${renderSteps(flow.workflow_steps || [])}
      ${renderCurrent(flow.current_slate)}
      ${renderPostmortem(flow.postmortem)}
      <section class="product-flow-actions">
        <h3>Siguientes acciones</h3>
        <ul>${actions.map((action) => `<li>${escapeHtml(action)}</li>`).join("")}</ul>
      </section>
      <p class="meta-copy">Flujo agregado de solo lectura: no escribe predicciones, tickets, resultados ni entrenamiento.</p>
    </div>`;
}
