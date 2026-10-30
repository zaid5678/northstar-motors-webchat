export function money(pence) {
  if (pence === null || pence === undefined) {
    return "Price on request";
  }
  return new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: "GBP",
    maximumFractionDigits: 0,
  }).format(pence / 100);
}

export function number(value) {
  return new Intl.NumberFormat("en-GB").format(value);
}

export function titleCase(value) {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : "";
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
