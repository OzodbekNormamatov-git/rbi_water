// Narx formati: 22000 -> "22 000 so'm" (tiyinlarsiz, 1000 ajratilgan).

export function fmtMoney(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  const rounded = Math.round(n);
  return `${rounded.toLocaleString("ru-RU").replace(/,/g, " ")} so'm`;
}

export function fmtDate(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleString("uz-UZ", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function escapeHtml(str) {
  return String(str ?? "").replace(/[&<>"']/g, (s) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[s]));
}

// Butun son: 1234 -> "1 234". Tilim — narx emas, faqat soni.
export function fmtCount(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return Math.round(n).toLocaleString("ru-RU").replace(/,/g, " ");
}
