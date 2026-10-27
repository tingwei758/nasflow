// The only module that talks to the server. Everything else imports these.

async function request(method, url, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      // Non-JSON error body; the status text will do.
    }
    throw new Error(`${response.status} ${detail}`);
  }
  return response.json();
}

const item = (hash) => `/api/items/${encodeURIComponent(hash)}`;

export const getMeta = () => request("GET", "/api/meta");
export const getBoard = () => request("GET", "/api/board");
export const getHealth = () => request("GET", "/api/health");
export const resolveItem = (hash, tmdbId) =>
  request("POST", `${item(hash)}/resolve`, { tmdb_id: tmdbId });
export const ignoreItem = (hash) => request("POST", `${item(hash)}/ignore`);
export const searchItem = (hash, query) =>
  request("GET", `${item(hash)}/search?q=${encodeURIComponent(query)}`);
export const previewItem = (hash, tmdbId) =>
  request("GET", `${item(hash)}/preview?tmdb_id=${encodeURIComponent(tmdbId)}`);
