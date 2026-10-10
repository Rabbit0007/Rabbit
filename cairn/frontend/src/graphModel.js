function wrapLabel(text, width, fontSize) {
  const lines = [];
  let line = "";
  let used = 0;
  for (const char of Array.from(text)) {
    const size = /[\u1100-\u115f\u2e80-\ua4cf\uac00-\ud7a3\uf900-\ufaff\ufe10-\uffef]/.test(char)
      ? fontSize : /\s/.test(char) ? fontSize * 0.35 : fontSize * 0.62;
    if (line && used + size > width) {
      lines.push(line);
      line = "";
      used = 0;
    }
    line += char;
    used += size;
  }
  if (line) lines.push(line);
  return lines.length ? lines : [" "];
}

function factNode(item, nodeType) {
  const title = item.id === "origin" ? "Origin" : String(item.title || item.description || item.id).replace(/\s+/g, " ").trim();
  const chars = Array.from(title);
  const summary = chars.length > 24 ? `${chars.slice(0, 24).join("")}...` : title;
  const fontSize = nodeType === "fact" ? 10 : 11;
  const textWidth = nodeType === "origin" ? 60 : 116;
  const lines = wrapLabel(summary, textWidth, fontSize);
  return { data: {
    id: item.id, label: lines.join("\n"), description: item.description,
    nodeType, status: item.status,
    width: nodeType === "origin" ? 64 : 136,
    height: Math.max(38, Math.ceil(lines.length * fontSize * 1.4 + 20)),
    sourceIds: item.from || [],
  } };
}

// This is a read-only view projection; protocol objects are never rewritten.
export function buildFactGraph(detail) {
  const nodes = (detail.facts || []).map(fact => factNode(fact, fact.id === "origin" ? "origin" : "fact"));
  const edges = [];
  for (const goal of detail.goals || []) {
    nodes.push(factNode(goal, "goal"));
    if (goal.parent_goal_id) {
      edges.push({ data: { id: `goal:${goal.parent_goal_id}:${goal.id}`, source: goal.parent_goal_id, target: goal.id, status: "goal_child", label: "子目标" } });
    } else {
      edges.push({ data: { id: `goal-origin:${goal.id}`, source: "origin", target: goal.id, status: "goal_evidence", label: "目标" } });
    }
    for (const factId of goal.from || []) {
      edges.push({ data: { id: `goal-evidence:${factId}:${goal.id}`, source: factId, target: goal.id, status: "goal_evidence", label: "完成依据" } });
    }
  }
  for (const step of detail.steps || []) {
    if (!step.to) {
      nodes.push({ data: {
        id: step.id, stepId: step.id, label: step.priority > 0 ? `P${step.priority}` : "?",
        description: step.description, sourceIds: step.from || [],
        nodeType: step.abandoned ? "step_abandoned" : step.worker ? "in_progress" : "unclaimed",
        width: 24, height: 24,
      } });
    }
    for (const source of step.from || []) {
      edges.push({ data: {
        id: `step:${step.id}:${source}`, source, target: step.to || step.id,
        stepId: step.id, label: step.description,
        status: step.to ? "concluded" : step.abandoned ? "unclaimed" : step.worker ? "in_progress" : "unclaimed",
      } });
    }
    if (step.goal_id) {
      edges.push({ data: {
        id: `scope:${step.id}:${step.goal_id}`, source: step.to || step.id, target: step.goal_id,
        stepId: step.id, status: "step_scope", label: "推进目标",
      } });
    }
  }
  const ids = new Set(nodes.map(node => node.data.id));
  return [...nodes, ...edges.filter(edge => ids.has(edge.data.source) && ids.has(edge.data.target))];
}

export function fitFactGraph(cy) {
  if (!cy || !cy.elements().length || !cy.width() || !cy.height()) return;
  cy.fit(cy.elements(), 40);
  if (cy.zoom() > 1) {
    cy.zoom(1);
    cy.center(cy.elements());
  }
}
