const endpointList = document.querySelector("#endpoint-list");
const methods = ["get", "post", "patch", "delete"];
const contact = {
  firstName: "Jamie",
  lastName: "Taylor",
  email: "jamie@example.com",
  phone: "07700900123",
};
const exampleBodies = {
  "/api/sales-enquiries": {
    dealershipId: "northstar-manchester",
    vehicleId: "veh-001",
    enquiryType: "availability",
    ...contact,
    message: "Is this vehicle available to view on Saturday?",
  },
  "/api/test-drive-bookings": {
    slotId: "replace-with-a-slot-id",
    ...contact,
  },
  "/api/vehicle-interests": {
    vehicleId: "veh-007",
    ...contact,
  },
  "/api/callback-requests": {
    dealershipId: "northstar-manchester",
    department: "sales",
    ...contact,
    preferredTime: "Weekday afternoon",
    reason: "I would like to discuss finance options.",
  },
  "/api/workshop-bookings": {
    slotId: "replace-with-a-slot-id",
    registration: "AB12 CDE",
    mileage: 42000,
    ...contact,
  },
  "/api/workshop-bookings/lookup": {
    reference: "WORK-10001",
    lastName: "Taylor",
    registration: "AB12 CDE",
    phone: "07700900123",
  },
  "/api/dealership-messages": {
    dealershipId: "northstar-manchester",
    department: "parts",
    subject: "Roof bar availability",
    message: "Please let me know whether roof bars are in stock.",
    preferredContactMethod: "email",
    ...contact,
  },
  "/api/part-exchange-valuations": {
    dealershipId: "northstar-manchester",
    registration: "AB12 CDE",
    mileage: 54000,
    condition: "good",
    ...contact,
  },
};

function exampleBody(path, method) {
  if (["get", "delete"].includes(method)) {
    return "";
  }
  return JSON.stringify(exampleBodies[path] ?? { notes: "Updated details" }, null, 2);
}

function concretePath(path) {
  return path
    .replace("{dealershipId}", "northstar-manchester")
    .replace("{vehicleId}", "veh-001")
    .replace("{offerId}", "offer-01")
    .replace("{recordId}", "replace-with-a-record-id");
}

function idempotencyField(needsIdempotency, needsAuth) {
  if (needsIdempotency) {
    return `<label>
      Idempotency-Key
      <input class="idempotency-key" value="docs-${crypto.randomUUID()}" />
    </label>`;
  }
  return `<label>
    Access
    <input value="${needsAuth ? "X-API-Key required" : "Public read"}" disabled />
  </label>`;
}

function bodyField(body) {
  if (!body) {
    return "";
  }
  return `<label style="margin-top: 14px">
    JSON body
    <textarea class="request-body">${body}</textarea>
  </label>`;
}

function endpointMarkup({ path, method, operation, needsAuth, needsIdempotency, body }) {
  return `
    <summary>
      <span class="method ${method}">${method.toUpperCase()}</span>
      <span class="path">${path}</span>
      <span class="summary">${operation.summary || ""}</span>
    </summary>
    <div class="endpoint-body">
      ${operation.description ? `<p>${operation.description}</p>` : ""}
      <div class="try-grid">
        <label>
          Request URL
          <input class="request-url" value="${concretePath(path)}" />
        </label>
        ${idempotencyField(needsIdempotency, needsAuth)}
      </div>
      ${bodyField(body)}
      <div class="button-row">
        <button class="send-request">Send request</button>
      </div>
      <div class="status"></div>
      <pre class="response" hidden></pre>
    </div>
  `;
}

function requestOptions(details, method, needsAuth) {
  const headers = { Accept: "application/json" };
  if (needsAuth) {
    headers["X-API-Key"] = "northstar-local-development";
  }
  const idempotencyInput = details.querySelector(".idempotency-key");
  if (idempotencyInput) {
    headers["Idempotency-Key"] = idempotencyInput.value;
  }
  const requestBody = details.querySelector(".request-body");
  const options = { method: method.toUpperCase(), headers };
  if (requestBody) {
    headers["Content-Type"] = "application/json";
    options.body = requestBody.value;
  }
  return options;
}

async function sendRequest(details, method, needsAuth) {
  const status = details.querySelector(".status");
  const responseElement = details.querySelector(".response");
  status.textContent = "Sending…";
  responseElement.hidden = true;
  try {
    const requestPath = details.querySelector(".request-url").value;
    const response = await fetch(requestPath, requestOptions(details, method, needsAuth));
    const payload = await response.json();
    status.textContent = `${response.status} ${response.statusText}`;
    responseElement.textContent = JSON.stringify(payload, null, 2);
    responseElement.hidden = false;
  } catch (error) {
    status.textContent = error.message;
  }
}

function renderEndpoint(path, method, operation) {
  const details = document.createElement("details");
  details.className = "endpoint";
  const needsAuth = Boolean(operation.security);
  const needsIdempotency = (operation.parameters || []).some(
    (parameter) =>
      parameter.name === "Idempotency-Key" || parameter.$ref?.includes("IdempotencyKey"),
  );
  details.innerHTML = endpointMarkup({
    path,
    method,
    operation,
    needsAuth,
    needsIdempotency,
    body: exampleBody(path, method),
  });
  details
    .querySelector(".send-request")
    .addEventListener("click", () => sendRequest(details, method, needsAuth));
  return details;
}

async function loadContract() {
  try {
    const response = await fetch("/openapi.json");
    const contract = await response.json();
    endpointList.replaceChildren();
    const endpointCards = Object.entries(contract.paths).flatMap(([path, pathDefinition]) =>
      methods
        .filter((method) => pathDefinition[method])
        .map((method) => renderEndpoint(path, method, pathDefinition[method])),
    );
    endpointList.append(...endpointCards);
  } catch (error) {
    endpointList.innerHTML = `<div class="empty">Could not load the API contract: ${error.message}</div>`;
  }
}

loadContract();
