export const topic = "picasso-collection";

export function worker(path, headers = {}, body) {
  const requestHeaders = {...headers, "X-Picasso-Worker": process.env.PICASSO_WORKER_KEY};
  if (body) requestHeaders["Content-Type"] = "application/json";
  return fetch(new URL(path, process.env.PICASSO_CONTAINER_URL), {
    method: "POST",
    headers: requestHeaders,
    body: body && JSON.stringify(body),
    signal: AbortSignal.timeout(270_000)
  });
}

export function failRun(run, stage, detail) {
  return worker("/internal/fail/" + run, {}, {stage, detail});
}
