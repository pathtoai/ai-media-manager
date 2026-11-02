// Shared model picker: lists the models from config.toml and remembers the
// choice per browser.
const KEY = "pics-manager-model";

export function fillModelSelect(select, config) {
  let saved = null;
  try { saved = localStorage.getItem(KEY); } catch {}
  for (const m of config.models) {
    const opt = document.createElement("option");
    opt.value = m.id;
    opt.textContent = m.available ? m.label : `${m.label} (not ready)`;
    opt.title = m.note || "";
    select.appendChild(opt);
  }
  const ids = config.models.map((m) => m.id);
  select.value = ids.includes(saved) ? saved : config.defaultModel;
  select.addEventListener("change", () => {
    try { localStorage.setItem(KEY, select.value); } catch {}
  });
}

export function selectedModel(select, config) {
  return config.models.find((m) => m.id === select.value);
}
