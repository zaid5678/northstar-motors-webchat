const API_BASE_URL = "http://localhost:4010";

export async function get(path, parameters = {}) {
  const url = new URL(path, API_BASE_URL);
  const populatedParameters = Object.entries(parameters).filter(
    ([, value]) => ![undefined, null, ""].includes(value),
  );
  url.search = new URLSearchParams(populatedParameters).toString();
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`The dealership platform returned ${response.status}.`);
  }
  return response.json();
}

export function assetUrl(path) {
  return new URL(path, API_BASE_URL).toString();
}
