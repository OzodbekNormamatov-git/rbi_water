export function fmtMoney(value, currency = "so'm") {
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return `${Math.round(n).toLocaleString("ru-RU").replace(/,/g, " ")} ${currency}`;
}

export function fmtCount(value) {
  return Number(value || 0).toLocaleString("ru-RU").replace(/,/g, " ");
}

export function fmtDate(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("uz-UZ", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}

export function fmtTimeOnly(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("uz-UZ", { hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}

export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export function statusPill(code, label) {
  const key = (code || "").toLowerCase();
  return `<span class="pill pill--${key}">${escapeHtml(label || code)}</span>`;
}
