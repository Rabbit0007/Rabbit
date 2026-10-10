import test from "node:test";
import assert from "node:assert/strict";
import { buildFactGraph, fitFactGraph } from "./graphModel.js";

const fixture = () => ({
  facts: [{ id: "origin", description: "起点" }, { id: "f001", description: "s001 初始信息收集完成：目标与应用版本已经确认" }],
  goals: [{ id: "g001", description: "验证目标并记录完整结果", status: "completed", from: ["f001"] }],
  steps: [
    { id: "s001", description: "对目标进行信息收集", from: ["origin"], to: "f001", goal_id: "g001" },
    { id: "s002", description: "对目标主机进行全面测试这是一段没有空格的很长中文说明", from: ["f001"], goal_id: "g001" },
  ],
  findings: [{ id: "finding001", fact_id: "f001" }],
});

test("completed steps are fact-to-fact edges with their own selectable identity", () => {
  const elements = buildFactGraph(fixture());
  assert.ok(!elements.some(el => el.data.id === "s001"));
  assert.deepEqual(elements.find(el => el.data.id === "step:s001:origin").data, {
    id: "step:s001:origin", source: "origin", target: "f001", stepId: "s001", label: "对目标进行信息收集", status: "concluded",
  });
  assert.ok(!elements.some(el => el.data.id === "finding001"));
});

test("open steps remain selectable compact placeholders, not overflowing text boxes", () => {
  const data = fixture();
  for (const [fields, type] of [[{}, "unclaimed"], [{ worker: "test" }, "in_progress"], [{ abandoned: true }, "step_abandoned"]]) {
    Object.assign(data.steps[1], { worker: null, abandoned: false }, fields);
    const node = buildFactGraph(data).find(el => el.data.id === "s002");
    assert.equal(node.data.nodeType, type);
    assert.equal(node.data.label, "?");
    assert.equal(node.data.description, data.steps[1].description);
    assert.equal(node.data.width, 24);
  }
});

test("Chinese labels use explicit short lines and keep goal completion status", () => {
  const elements = buildFactGraph(fixture());
  const fact = elements.find(el => el.data.id === "f001");
  assert.ok(fact.data.label.includes("\n"));
  assert.ok(fact.data.label.split("\n").every(line => Array.from(line).length <= 20));
  assert.equal(elements.find(el => el.data.id === "g001").data.status, "completed");
});

test("the projection never mutates facts, steps, goals or findings", () => {
  const data = fixture();
  const before = structuredClone(data);
  buildFactGraph(data);
  assert.deepEqual(data, before);
});

test("missing references cannot produce dangling graph edges", () => {
  const data = fixture();
  data.steps[0].from.push("missing");
  data.goals[0].from.push("missing");
  const elements = buildFactGraph(data);
  const ids = new Set(elements.filter(el => !el.data.source).map(el => el.data.id));
  assert.ok(elements.filter(el => el.data.source).every(el => ids.has(el.data.source) && ids.has(el.data.target)));
});

test("automatic fitting caps enlargement but keeps a large graph zoomed out", () => {
  for (const initialZoom of [0.4, 2.7]) {
    let zoom = initialZoom;
    let centered = false;
    const cy = { elements: () => [1], width: () => 900, height: () => 600, fit: () => {}, zoom: value => value === undefined ? zoom : (zoom = value), center: () => { centered = true; } };
    fitFactGraph(cy);
    assert.equal(zoom, Math.min(1, initialZoom));
    assert.equal(centered, initialZoom > 1);
  }
});
