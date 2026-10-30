const resources = [
  ["salesEnquiries", "Sales enquiries"],
  ["testDriveBookings", "Test drives"],
  ["vehicleInterests", "Vehicle interests"],
  ["callbackRequests", "Callbacks"],
  ["workshopBookings", "Workshop bookings"],
  ["dealershipMessages", "Messages"],
  ["partExchangeValuations", "Part exchanges"],
];

let data;
let activeResource = resources[0][0];
const blankValues = new Set([null, undefined, ""]);

function text(value) {
  if (blankValues.has(value)) {
    return "—";
  }
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function escapeHtml(value) {
  return text(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderTable(container, rows) {
  if (!rows?.length) {
    container.innerHTML = '<div class="empty">No records yet.</div>';
    return;
  }
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];
  container.innerHTML = `
    <table>
      <thead><tr>${columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}</tr></thead>
      <tbody>
        ${rows
          .map(
            (row) =>
              `<tr>${columns
                .map(
                  (column) =>
                    `<td title="${escapeHtml(row[column])}">${escapeHtml(row[column])}</td>`,
                )
                .join("")}</tr>`,
          )
          .join("")}
      </tbody>
    </table>
  `;
}

function render() {
  const statGrid = document.querySelector("#stat-grid");
  const topStats = resources.slice(0, 4);
  statGrid.innerHTML = topStats
    .map(
      ([key, label]) => `
        <div class="stat">
          <span class="stat-label">${label}</span>
          <span class="stat-value">${data.counts[key] || 0}</span>
        </div>
      `,
    )
    .join("");

  const tabs = document.querySelector("#tabs");
  tabs.innerHTML = resources
    .map(
      ([key, label]) =>
        `<button data-resource="${key}" class="${key === activeResource ? "active" : ""}">
          ${label} · ${data.counts[key] || 0}
        </button>`,
    )
    .join("");
  tabs.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      activeResource = button.dataset.resource;
      render();
    });
  });

  document.querySelector("#table-title").textContent = resources.find(
    ([key]) => key === activeResource,
  )[1];
  renderTable(document.querySelector("#records"), data.records[activeResource]);
  renderTable(document.querySelector("#request-log"), data.requestLog);
  document.querySelector("#updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

async function load() {
  const response = await fetch("/admin/api/summary");
  if (!response.ok) {
    throw new Error("Could not load platform records.");
  }
  data = await response.json();
  render();
}

document.querySelector("#refresh").addEventListener("click", () => {
  load().catch((error) => window.alert(error.message));
});

document.querySelector("#reset").addEventListener("click", async () => {
  const confirmed = window.confirm(
    "Restore all dealership data to its original seeded state? Saved records will be removed.",
  );
  if (!confirmed) {
    return;
  }
  const response = await fetch("/admin/api/reset", {
    method: "POST",
    headers: { "X-Admin-Key": "northstar-local-admin" },
  });
  if (!response.ok) {
    window.alert("The platform could not be reset.");
    return;
  }
  await load();
});

load().catch((error) => {
  document.querySelector("#records").innerHTML = `<div class="empty">${error.message}</div>`;
});
