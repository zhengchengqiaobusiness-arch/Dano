/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

const els = {
  targetUrl: document.getElementById("targetUrl"),
  goal: document.getElementById("goal"),
  startBtn: document.getElementById("startBtn"),
  stopBtn: document.getElementById("stopBtn"),
  cancelBtn: document.getElementById("cancelBtn"),
  browserStatus: document.getElementById("browserStatus"),
  piStatus: document.getElementById("piStatus"),
  evidenceCount: document.getElementById("evidenceCount"),
  processStatus: document.getElementById("processStatus"),
  outcome: document.getElementById("outcome"),
  capabilityCount: document.getElementById("capabilityCount"),
  resultLink: document.getElementById("resultLink"),
  resultView: document.getElementById("resultView"),
};

let recordingId = "";
let source = null;

function renderSession(session) {
  if (!session) return;
  els.browserStatus.textContent = session.browserStatus || "idle";
  els.piStatus.textContent = session.piStatus || "idle";
  els.evidenceCount.textContent = String(session.evidenceCount ?? 0);
  els.processStatus.textContent = session.publicMessage || session.status || "未开始";
  if (session.status === "succeeded" && session.hasFinalResult) {
    els.outcome.textContent = "成功：已收到 PI 最终提交";
    els.outcome.className = "outcome ok";
  } else if (session.status === "failed") {
    els.outcome.textContent = session.publicMessage || "PI 未完成，本次录制失败，没有产出能力";
    els.outcome.className = "outcome bad";
    els.capabilityCount.textContent = "能力数量：0";
  } else {
    els.outcome.textContent = session.publicMessage || session.status || "进行中";
    els.outcome.className = "outcome";
  }
}

async function loadResult() {
  if (!recordingId) return;
  const response = await fetch(`/api/recordings/${recordingId}/result`);
  const payload = await response.json();
  const count = payload.capabilityCount;
  els.capabilityCount.textContent = `能力数量：${count == null ? "以 PI 结果为准" : count}`;
  if (payload.result) {
    els.resultLink.classList.remove("hidden");
    els.resultLink.href = `/api/recordings/${recordingId}/result`;
    els.resultView.classList.remove("hidden");
    els.resultView.textContent = JSON.stringify(payload.result, null, 2);
  } else {
    els.resultLink.classList.add("hidden");
    els.resultView.classList.add("hidden");
    els.resultView.textContent = "";
  }
}

function listen(id) {
  source?.close();
  source = new EventSource(`/api/recordings/${id}/events`);
  source.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    if (payload.session) renderSession(payload.session);
    if (payload.session?.status === "succeeded" || payload.session?.status === "failed") {
      loadResult();
    }
  };
}

els.startBtn.addEventListener("click", async () => {
  els.startBtn.disabled = true;
  els.outcome.textContent = "正在启动 PI";
  try {
    const response = await fetch("/api/recordings", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        targetUrl: els.targetUrl.value,
        goal: els.goal.value,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.publicMessage || payload.error || "启动失败");
    }
    recordingId = payload.id;
    renderSession(payload);
    listen(recordingId);
    els.stopBtn.disabled = false;
    els.cancelBtn.disabled = false;
  } catch (error) {
    els.outcome.textContent = error.message || "PI 未完成，本次录制失败，没有产出能力";
    els.outcome.className = "outcome bad";
    els.capabilityCount.textContent = "能力数量：0";
    els.startBtn.disabled = false;
  }
});

els.stopBtn.addEventListener("click", async () => {
  els.stopBtn.disabled = true;
  els.cancelBtn.disabled = true;
  const response = await fetch(`/api/recordings/${recordingId}/stop`, { method: "POST" });
  const payload = await response.json();
  renderSession(payload.session);
  await loadResult();
  els.startBtn.disabled = false;
});

els.cancelBtn.addEventListener("click", async () => {
  els.stopBtn.disabled = true;
  els.cancelBtn.disabled = true;
  const response = await fetch(`/api/recordings/${recordingId}/cancel`, { method: "POST" });
  const payload = await response.json();
  renderSession(payload.session);
  await loadResult();
  els.startBtn.disabled = false;
});
