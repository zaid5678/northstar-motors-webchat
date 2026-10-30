import { assetUrl, get } from "../../shared/api.js";
import { escapeHtml, money, number, titleCase } from "../../shared/format.js";

function vehicleCard(vehicle, onSelect) {
  const article = document.createElement("article");
  article.className = "vehicle-card";
  const statusClass = ["reserved", "sold"].includes(vehicle.availability)
    ? vehicle.availability
    : "";
  article.innerHTML = `
    <button class="vehicle-card-action" type="button" aria-label="View ${escapeHtml(vehicle.make)} ${escapeHtml(vehicle.model)}">
      <div class="vehicle-image-wrap">
        <img src="${escapeHtml(assetUrl(vehicle.images[0]))}" alt="${escapeHtml(vehicle.make)} ${escapeHtml(vehicle.model)}" loading="lazy" />
        ${
          vehicle.availability !== "available"
            ? `<span class="availability ${statusClass}">${escapeHtml(titleCase(vehicle.availability))}</span>`
            : ""
        }
      </div>
      <div class="vehicle-card-content">
        <p class="vehicle-year">${escapeHtml(vehicle.year)} · ${escapeHtml(vehicle.dealershipTown)}</p>
        <h3>${escapeHtml(vehicle.make)} ${escapeHtml(vehicle.model)}</h3>
        <p class="variant">${escapeHtml(vehicle.variant)}</p>
        <dl class="vehicle-specs">
          <div><dt>Mileage</dt><dd>${number(vehicle.mileage)} miles</dd></div>
          <div class="vehicle-spec-divider"><dt>Fuel</dt><dd>${escapeHtml(vehicle.fuelType)}</dd></div>
          <div class="vehicle-spec-divider"><dt>Gearbox</dt><dd>${escapeHtml(vehicle.transmission)}</dd></div>
        </dl>
        <div class="vehicle-price">
          <strong>${money(vehicle.pricePence)}</strong>
          ${
            vehicle.monthlyPricePence
              ? `<span>or ${money(vehicle.monthlyPricePence)} / month</span>`
              : "<span>Speak to our team</span>"
          }
        </div>
      </div>
    </button>
  `;
  article.querySelector("button").addEventListener("click", () => onSelect(vehicle.id));
  return article;
}

function emptyResults() {
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.innerHTML = `
    <h3>No matching vehicles</h3>
    <p>Try changing one or two filters to see more of our current stock.</p>
  `;
  return empty;
}

function renderResults({ grid, summary, loadMore, result, onSelect, append, page }) {
  const cards = result.items.map((vehicle) => vehicleCard(vehicle, onSelect));
  const visibleCards = cards.length ? cards : [emptyResults()];
  if (append) {
    grid.append(...visibleCards);
  } else {
    grid.replaceChildren(...visibleCards);
  }
  const total = result.pagination.totalItems;
  const suffix = total === 1 ? "" : "s";
  summary.textContent = `${number(total)} vehicle${suffix} found`;
  loadMore.hidden = page >= result.pagination.totalPages;
  return total;
}

function renderFailure({ grid, summary, loadMore }, error) {
  grid.innerHTML = `
    <div class="empty-state">
      <h3>Stock is temporarily unavailable</h3>
      <p>${escapeHtml(error.message)} Check that the local dealership platform is running.</p>
    </div>
  `;
  summary.textContent = "Could not load vehicles";
  loadMore.hidden = true;
}

function showLoading(grid, append) {
  if (!append) {
    grid.innerHTML = '<div class="loading-state">Finding matching vehicles…</div>';
  }
}

export function createInventory({ grid, summary, loadMore, onSelect }) {
  let page = 1;
  let currentFilters = {};

  async function load(filters = {}, append = false) {
    currentFilters = filters;
    page = append ? page + 1 : 1;
    showLoading(grid, append);
    try {
      const result = await get("/api/vehicles", { ...filters, page, pageSize: 12 });
      return renderResults({ grid, summary, loadMore, result, onSelect, append, page });
    } catch (error) {
      renderFailure({ grid, summary, loadMore }, error);
      return 0;
    }
  }

  loadMore.addEventListener("click", () => load(currentFilters, true));
  return { load };
}
