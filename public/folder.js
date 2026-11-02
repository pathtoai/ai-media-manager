// Shared folder picker: opens the native macOS chooser (via the server) and
// remembers the last folder per browser.
const KEY = "pics-manager-dir";

export function savedDir(fallback) {
  try {
    return localStorage.getItem(KEY) || fallback;
  } catch {
    return fallback;
  }
}

export function rememberDir(dir) {
  try { localStorage.setItem(KEY, dir); } catch {}
}

// Resolves to the chosen folder path, or null if the user cancelled.
export async function pickFolder(current) {
  const res = await fetch("/api/pick-folder", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current }),
  });
  const d = await res.json();
  if (!res.ok) throw new Error(d.error || "Could not open the folder picker");
  if (d.cancelled) return null;
  rememberDir(d.dir);
  return d.dir;
}
