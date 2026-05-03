const BACKEND_URL = "http://127.0.0.1:8787";
const defaultSettings = {
  avatar: "streamer",
  voice: "warm-girl",
  watching: false,
  voiceEnabled: true,
  observeSpeechEnabled: true
};

const chatLog = document.getElementById("chatLog");
const capabilitiesEl = document.getElementById("capabilities");
const composer = document.getElementById("composer");
const messageInput = document.getElementById("message");
const imageInput = document.getElementById("imageInput");
const voiceBtn = document.getElementById("voiceBtn");
const taskContent = document.getElementById("taskContent");
const taskStatus = document.getElementById("taskStatus");
const timelineEl = document.getElementById("timeline");
const artifactsEl = document.getElementById("artifacts");
const avatarSelect = document.getElementById("avatarSelect");
const voiceSelect = document.getElementById("voiceSelect");
const watchingToggle = document.getElementById("watchingToggle");
const voiceEnabledToggle = document.getElementById("voiceEnabledToggle");
const observeSpeechToggle = document.getElementById("observeSpeechToggle");
const tabs = Array.from(document.querySelectorAll("[data-tab]"));
const pages = Array.from(document.querySelectorAll("[data-page]"));

let desktopSettings = { ...defaultSettings };

function switchTab(tabName) {
  tabs.forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === tabName);
  });
  pages.forEach((page) => {
    page.classList.toggle("active", page.dataset.page === tabName);
  });
}

function appendMessage(role, text, meta = "") {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  if (meta) {
    const span = document.createElement("span");
    span.className = "meta";
    span.textContent = meta;
    div.appendChild(span);
  }
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}

async function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function summarizeTools(toolCalls) {
  if (!toolCalls.length) return "none";
  return toolCalls.map((item) => item.name).join(", ");
}

function renderTask(task) {
  taskStatus.textContent = task.status || "completed";
  taskContent.innerHTML = `
    <p class="task-title">${task.title || "Untitled task"}</p>
    <p class="meta-text">${task.owner || "unknown-agent"} · ${task.step_count || 0} steps</p>
    <div class="task-meta">
      <span class="chip">${task.owner || "agent"}</span>
      <span class="chip">${task.status || "completed"}</span>
    </div>
    <div class="task-steps">
      ${(task.steps || []).map((step) => `
        <article class="step">
          <div class="step-head">
            <strong>${step.title}</strong>
            <span class="step-status ${step.status === "failed" ? "failed" : ""}">${step.status}</span>
          </div>
          <p class="step-detail">${step.detail}</p>
        </article>
      `).join("")}
    </div>
  `;
}

function renderTimeline(toolCalls) {
  if (!toolCalls.length) {
    timelineEl.innerHTML = '<p class="empty">工具调用会显示在这里。</p>';
    return;
  }
  timelineEl.innerHTML = toolCalls.map((item, index) => `
    <article class="timeline-item">
      <div class="timeline-head">
        <strong>${index + 1}. ${item.name}</strong>
        <span class="chip">tool</span>
      </div>
      <p class="timeline-detail">${item.result}</p>
    </article>
  `).join("");
}

function renderArtifacts(artifacts) {
  if (!artifacts.length) {
    artifactsEl.innerHTML = '<p class="empty">截图和产物会显示在这里。</p>';
    return;
  }
  artifactsEl.innerHTML = artifacts.map((item) => {
    const src = item.url.startsWith("http") ? item.url : `${BACKEND_URL}${item.url}`;
    return `
      <article class="artifact-item">
        <strong>${item.label}</strong>
        <p class="meta-text">${item.type}</p>
        <img src="${src}" alt="${item.label}" />
      </article>
    `;
  }).join("");
}

function applySettings(next) {
  desktopSettings = { ...desktopSettings, ...next };
  if (!["streamer", "swordswoman", "cyber"].includes(desktopSettings.avatar)) {
    desktopSettings.avatar = "streamer";
  }
  avatarSelect.value = desktopSettings.avatar;
  voiceSelect.value = desktopSettings.voice;
  watchingToggle.checked = !!desktopSettings.watching;
  voiceEnabledToggle.checked = !!desktopSettings.voiceEnabled;
  observeSpeechToggle.checked = !!desktopSettings.observeSpeechEnabled;
}

async function updateSettings(patch) {
  const settings = await window.bishoujo.updateSettings(patch);
  applySettings(settings);
}

async function loadCapabilities() {
  const capabilities = await window.bishoujo.capabilities();
  capabilitiesEl.innerHTML = `
    <div class="status-group">
      <span class="status-label">Runtime</span>
      <span class="chip">${capabilities.provider}</span>
      <span class="chip">${capabilities.model}</span>
      <span class="chip">vision:${capabilities.vision_provider}</span>
      <span class="chip">embed:${capabilities.embedding_provider}</span>
    </div>
    <div class="status-group">
      <span class="status-label">Features</span>
      <span class="chip">vision:${capabilities.features.vision ? "on" : "off"}</span>
      <span class="chip">browser-speech:${capabilities.features.browser_speech ? "on" : "off"}</span>
      <span class="chip">tts:${capabilities.features.tts_engine || "browser-speech"}</span>
      <span class="chip">memory:${capabilities.features.semantic_memory ? "on" : "off"}</span>
    </div>
    <div class="status-group">
      <span class="status-label">Tools</span>
      ${capabilities.tools.slice(0, 10).map((item) => `<span class="chip">${item}</span>`).join("")}
    </div>
  `;
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    switchTab(tab.dataset.tab || "chat");
  });
});

composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = messageInput.value.trim();
  if (!text) return;

  appendMessage("user", text);
  taskStatus.textContent = "running";
  voiceBtn.disabled = true;

  try {
    const attachments = [];
    const file = imageInput.files?.[0];
    if (file) {
      attachments.push({
        kind: "image",
        mime_type: file.type,
        data_url: await fileToDataUrl(file)
      });
    }

    const response = await window.bishoujo.chat({
      message: text,
      session_id: "desktop-session",
      attachments
    });

    appendMessage(
      "assistant",
      response.reply,
      `${response.trace.active_agent} | tools: ${summarizeTools(response.trace.tool_calls)}`
    );

    renderTask(response.task || {});
    renderTimeline(response.trace.tool_calls || []);
    renderArtifacts(response.artifacts || []);
    messageInput.value = "";
    imageInput.value = "";
  } catch (error) {
    taskStatus.textContent = "failed";
    appendMessage("assistant", `请求失败：${error.message}`);
  } finally {
    voiceBtn.disabled = false;
  }
});

voiceBtn.addEventListener("click", () => {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    appendMessage("assistant", "当前环境不支持 Web Speech API，可继续使用文本输入。");
    return;
  }
  const recognition = new Recognition();
  recognition.lang = "zh-CN";
  recognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    messageInput.value = transcript;
    appendMessage("assistant", `已转写语音：${transcript}`);
  };
  try {
    recognition.start();
  } catch (error) {
    appendMessage("assistant", `语音输入启动失败：${error.message}`);
  }
});

watchingToggle.addEventListener("change", () => {
  updateSettings({ watching: watchingToggle.checked }).catch((error) => {
    appendMessage("assistant", `持续陪伴切换失败：${error.message}`);
  });
});

voiceEnabledToggle.addEventListener("change", () => {
  updateSettings({ voiceEnabled: voiceEnabledToggle.checked }).catch((error) => {
    appendMessage("assistant", `语音开关切换失败：${error.message}`);
  });
});

observeSpeechToggle.addEventListener("change", () => {
  updateSettings({ observeSpeechEnabled: observeSpeechToggle.checked }).catch((error) => {
    appendMessage("assistant", `观察发声切换失败：${error.message}`);
  });
});

avatarSelect.addEventListener("change", () => {
  updateSettings({ avatar: avatarSelect.value }).catch((error) => {
    appendMessage("assistant", `形象切换失败：${error.message}`);
  });
});

voiceSelect.addEventListener("change", () => {
  updateSettings({ voice: voiceSelect.value }).catch((error) => {
    appendMessage("assistant", `音色切换失败：${error.message}`);
  });
});

window.bishoujo.onSettingsChanged((settings) => {
  applySettings(settings);
});

loadCapabilities().catch((error) => {
  appendMessage("assistant", `能力加载失败：${error.message}`);
});

window.bishoujo.settings().then((settings) => {
  applySettings(settings);
  switchTab("chat");
}).catch((error) => {
  appendMessage("assistant", `设置加载失败：${error.message}`);
  applySettings(defaultSettings);
});
