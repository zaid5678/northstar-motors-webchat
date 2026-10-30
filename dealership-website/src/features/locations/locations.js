import { get } from "../../shared/api.js";
import { escapeHtml } from "../../shared/format.js";

export async function renderLocations(container) {
  try {
    const result = await get("/api/dealerships");
    container.innerHTML = result.items
      .map(
        (location) => `
          <article>
            <p>${escapeHtml(location.town)}</p>
            <h3>${escapeHtml(location.name)}</h3>
            <address>${escapeHtml(location.addressLine)}, ${escapeHtml(location.postcode)}</address>
            <a href="tel:${escapeHtml(location.phone.replaceAll(" ", ""))}">${escapeHtml(location.phone)}</a>
          </article>
        `,
      )
      .join("");
  } catch {
    container.innerHTML = "<p>Dealership details are temporarily unavailable.</p>";
  }
}
