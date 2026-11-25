import {handleCallback, send} from "@vercel/queue";
import {topic, worker} from "../worker.mjs";

async function requireOk(path) {
  const response = await worker(path);
  if (!response.ok) throw new Error("Collection service status " + response.status);
  return response.json();
}

export const POST = handleCallback(async ({run, index}, metadata) => {
  if (!/^[a-f0-9-]{36}$/.test(run) || (index !== undefined && (!Number.isInteger(index) || index < 0))) {
    throw new Error("Invalid collection message");
  }
  try {
    if (index !== undefined) {
      await requireOk(`/internal/batch/${run}/${index}`);
      return;
    }
    const {batches} = await requireOk("/internal/plan/" + run);
    // Publish bounded groups; idempotency keys make partial fan-out safe to retry.
    for (let from = 0; from < batches; from += 8) {
      await Promise.all(Array.from({length: Math.min(8, batches - from)}, (_, offset) => {
        const item = from + offset;
        return send(topic, {run, index: item}, {
          idempotencyKey: `${run}:${item}`,
          delaySeconds: Math.floor(item / 3) * 4
        });
      }));
    }
  } catch (error) {
    if (metadata.deliveryCount < 5) throw error;
    await requireOk("/internal/fail/" + run);
  }
});
