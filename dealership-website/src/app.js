import { createInventory } from "./features/inventory/inventory.js";
import { renderLocations } from "./features/locations/locations.js";
import { showVehicleDetail } from "./features/vehicle-detail/vehicle-detail.js";

const controls = {
  query: document.querySelector("#query-filter"),
  make: document.querySelector("#make-filter"),
  body: document.querySelector("#body-filter"),
  fuel: document.querySelector("#fuel-filter"),
  price: document.querySelector("#price-filter"),
  sort: document.querySelector("#sort-filter"),
};

const dialog = document.querySelector("#vehicle-dialog");
const detailContainer = document.querySelector("#vehicle-detail");

function openVehicle(vehicleId, updateUrl = true) {
  if (updateUrl) {
    const url = new URL(window.location);
    url.searchParams.set("vehicle", vehicleId);
    url.hash = "vehicles";
    window.history.pushState({ vehicleId }, "", url);
  }
  return showVehicleDetail({
    vehicleId,
    dialog,
    container: detailContainer,
  });
}

const inventory = createInventory({
  grid: document.querySelector("#vehicle-grid"),
  summary: document.querySelector("#result-summary"),
  loadMore: document.querySelector("#load-more"),
  onSelect: (vehicleId) => openVehicle(vehicleId),
});

function filters() {
  return {
    q: controls.query.value,
    make: controls.make.value,
    bodyStyle: controls.body.value,
    fuelType: controls.fuel.value,
    maxPricePence: controls.price.value,
    sort: controls.sort.value,
  };
}

let filterTimer;
function refresh() {
  window.clearTimeout(filterTimer);
  filterTimer = window.setTimeout(() => inventory.load(filters()), 180);
}

Object.values(controls).forEach((control) => control.addEventListener("input", refresh));

document.querySelector("#clear-filters").addEventListener("click", () => {
  Object.values(controls).forEach((control) => {
    control.value = control === controls.sort ? "newest" : "";
  });
  inventory.load(filters());
});

document.querySelector("#hero-search").addEventListener("submit", (event) => {
  event.preventDefault();
  controls.query.value = document.querySelector("#hero-query").value;
  document.querySelector("#vehicles").scrollIntoView({ behavior: "smooth" });
  inventory.load(filters());
});

document.querySelector("#close-dialog").addEventListener("click", () => dialog.close());
dialog.addEventListener("click", (event) => {
  if (event.target === dialog) {
    dialog.close();
  }
});
dialog.addEventListener("close", () => {
  const url = new URL(window.location);
  url.searchParams.delete("vehicle");
  window.history.replaceState(null, "", url);
});
window.addEventListener("popstate", () => {
  const vehicleId = new URL(window.location).searchParams.get("vehicle");
  if (vehicleId) {
    openVehicle(vehicleId, false);
  } else if (dialog.open) {
    dialog.close();
  }
});

inventory.load({ sort: "newest" }).then((count) => {
  document.querySelector("#vehicle-count").textContent = count;
});
renderLocations(document.querySelector("#location-list"));

const linkedVehicleId = new URL(window.location).searchParams.get("vehicle");
if (linkedVehicleId) {
  openVehicle(linkedVehicleId, false);
}
