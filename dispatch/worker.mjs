export const topic = "picasso-collection";

export function worker(path, headers = {}) {
  return fetch(new URL(path, process.env.PICASSO_CONTAINER_URL), {
    method: "POST",
    headers: {...headers, "X-Picasso-Worker": process.env.PICASSO_WORKER_KEY},
    signal: AbortSignal.timeout(270_000)
  });
}
