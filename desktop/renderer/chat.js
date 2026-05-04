const form = document.getElementById("chatForm");
const input = document.getElementById("chatInput");
const sendBtn = document.getElementById("sendBtn");

function setBusy(next) {
  input.disabled = next;
  sendBtn.disabled = next;
  sendBtn.textContent = next ? "处理中" : "发送";
}

form?.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text || input.disabled) return;
  input.value = "";
  setBusy(true);
  window.bishoujo.submitPetChat(text);
});

input?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
  }
});

window.bishoujo.onPetChatBusyChanged?.((busy) => {
  setBusy(!!busy);
  if (!busy) {
    input.focus();
  }
});

window.addEventListener("DOMContentLoaded", () => {
  input.focus();
});
