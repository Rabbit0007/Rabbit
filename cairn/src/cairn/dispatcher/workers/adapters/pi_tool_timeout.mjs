// Explicitly loaded by Cairn; unrelated user/project extensions stay disabled.
// This bounds foreground bash calls, NOT OS permissions or detached processes.
export default function registerBashTimeout(pi) {
  // Thirty seconds was too short for bounded scanners and network commands:
  // the tool was killed repeatedly while the outer Step still had several
  // minutes available. Keep an independent per-call ceiling, but give a
  // progressing command enough time to finish.
  const defaultMaximum = 90;
  const configured = Number(process.env.CAIRN_BASH_TIMEOUT_SECONDS ?? defaultMaximum);
  const maximum = Number.isFinite(configured) && configured > 0 ? configured : defaultMaximum;
  pi.on("tool_call", async (event) => {
    if (event.toolName !== "bash") return;
    const requested = event.input.timeout;
    event.input.timeout = typeof requested === "number" && Number.isFinite(requested) && requested > 0
      ? Math.min(requested, maximum) : maximum;
  });
}
