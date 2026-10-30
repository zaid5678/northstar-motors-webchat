import { assetUrl, get } from "../../shared/api.js";
import { escapeHtml, money, number, titleCase } from "../../shared/format.js";

function availabilityMessage(availability) {
  if (availability.canBookTestDrive) {
    return "Available for viewing and test drives.";
  }
  if (availability.canRegisterInterest) {
    return "Currently reserved. Ask us to contact you if it becomes available.";
  }
  return "Please ask the dealership about current availability.";
}

function monthlyPrice(vehicle) {
  return vehicle.monthlyPricePence
    ? `<p class="monthly-price">From ${money(vehicle.monthlyPricePence)} per month</p>`
    : "";
}

function vehicleDetail(vehicle, availability) {
  const allowedStatuses = new Set(["available", "reserved", "sold"]);
  const statusClass = allowedStatuses.has(vehicle.availability) ? vehicle.availability : "";
  return `
    <div class="detail-grid">
      <div class="detail-image">
        <img src="${escapeHtml(assetUrl(vehicle.images[0]))}" alt="${escapeHtml(vehicle.make)} ${escapeHtml(vehicle.model)}" />
      </div>
      <div class="detail-content">
        <p class="eyebrow">${escapeHtml(vehicle.year)} · ${escapeHtml(vehicle.bodyStyle)}</p>
        <h2>${escapeHtml(vehicle.make)} ${escapeHtml(vehicle.model)}</h2>
        <p class="detail-variant">${escapeHtml(vehicle.variant)}</p>
        <p class="detail-price">${money(vehicle.pricePence)}</p>
        ${monthlyPrice(vehicle)}
        <span class="availability ${statusClass}">
          ${escapeHtml(titleCase(vehicle.availability))}
        </span>
        <dl class="detail-specs">
          <div><dt>Mileage</dt><dd>${number(vehicle.mileage)} miles</dd></div>
          <div><dt>Fuel type</dt><dd>${escapeHtml(vehicle.fuelType)}</dd></div>
          <div><dt>Transmission</dt><dd>${escapeHtml(vehicle.transmission)}</dd></div>
          <div><dt>Colour</dt><dd>${escapeHtml(vehicle.colour)}</dd></div>
        </dl>
        <p class="detail-description">${escapeHtml(vehicle.description)}</p>
        <div class="location-note">
          <strong>${escapeHtml(vehicle.dealershipName)}</strong>
          <span>${escapeHtml(vehicle.dealershipTown)}</span>
        </div>
        <div class="next-action">${availabilityMessage(availability)}</div>
      </div>
    </div>
  `;
}

function detailError(error) {
  return `
    <div class="empty-state">
      <h3>Vehicle details unavailable</h3>
      <p>${escapeHtml(error.message)}</p>
    </div>
  `;
}

export async function showVehicleDetail({ vehicleId, dialog, container }) {
  container.innerHTML = '<div class="loading-state">Loading vehicle details…</div>';
  dialog.showModal();
  try {
    const [vehicle, availability] = await Promise.all([
      get(`/api/vehicles/${vehicleId}`),
      get(`/api/vehicles/${vehicleId}/availability`),
    ]);
    container.innerHTML = vehicleDetail(vehicle, availability);
  } catch (error) {
    container.innerHTML = detailError(error);
  }
}
