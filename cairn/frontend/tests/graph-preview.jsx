import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { Maximize2, Plus, CheckCircle2, Sparkles, ShieldAlert } from "lucide-react";
import { GraphCanvas } from "../src/App.jsx";
import "../src/styles.css";

const facts = [{ id: "origin", description: "测试起点" }];
const steps = [];
for (let i = 1; i <= 16; i += 1) {
  facts.push({ id: `f${i}`, description: `s${i.toString().padStart(3, "0")} 测试执行完成：这是一段用于检查中文与English混排的事实说明` });
  steps.push({ id: `s${i}`, description: `测试执行步骤 ${i}：检查接口、验证结果并保存证据`, from: [i === 1 ? "origin" : `f${Math.max(1, Math.floor(i / 2))}`], to: `f${i}`, goal_id: "g001" });
}
const completed = { facts, steps, goals: [{ id: "g001", description: "完成测试并记录完整的验证结果", status: "completed", from: ["f15", "f16"] }], findings: [] };
const pending = {
  facts: [facts[0]],
  steps: [1, 2, 3].map(i => ({ id: `s${i}`, description: "对目标应用开展完整验证这是一段没有空格的中文长说明并包含EnglishLongIdentifier需要检查不会溢出", from: ["origin"], goal_id: "g001", worker: i === 2 ? "test-worker" : null })),
  goals: [{ id: "g001", description: "获取测试结果并返回完整验证记录", status: "active", from: [] }], findings: [],
};

function Preview() {
  const [mode, setMode] = useState("pending");
  const [layout, setLayout] = useState("dagre_tb");
  const [selected, setSelected] = useState(null);
  return <main style={{ height: "100dvh", display: "flex", flexDirection: "column" }}>
    <header style={{ padding: 8, display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" }}>
      <label>测试数据 <select aria-label="测试数据" value={mode} onChange={event => { setMode(event.target.value); setSelected(null); }}><option value="pending">未完成项目</option><option value="completed">17 个事实项目</option></select></label>
      <output aria-label="选中对象">{selected ? `${selected.type}:${selected.id}` : "未选择"}</output>
    </header>
    <section className="graph-panel" style={{ flex: 1 }}>
      <div className="graph-controls">
        <div className="graph-toolbar">
          <button className="ghost-button compact" title="适配图谱" aria-label="适配图谱" onClick={() => window.dispatchEvent(new Event("rabbit:fit-graph"))}><Maximize2 size={16} /></button>
          <select aria-label="图布局" value={layout} onChange={event => setLayout(event.target.value)}>
            <option value="dagre_tb">Dagre ↓</option><option value="dagre_lr">Dagre →</option>
            <option value="klay_tb">Klay ↓</option><option value="klay_lr">Klay →</option>
            <option value="elk_tb">ELK ↓</option><option value="elk_lr">ELK →</option>
          </select>
        </div>
        <div className="graph-actions">
          <button className="primary-outline compact"><Plus size={16} />步骤</button>
          <button className="primary-outline compact success" disabled><CheckCircle2 size={16} />完成</button>
          <button className="primary-outline compact warning"><Sparkles size={16} />提示</button>
          <button className="primary-outline compact"><ShieldAlert size={16} />总结</button>
        </div>
      </div>
      <GraphCanvas detail={mode === "pending" ? pending : completed} selected={selected} onSelect={setSelected} layout={layout} />
    </section>
  </main>;
}

createRoot(document.getElementById("root")).render(<Preview />);
