import {handleCallback, send} from "@vercel/queue";
import {failRun, topic, worker} from "../worker.mjs";

async function requireOk(path) {
  let response;
  try {
    response = await worker(path);
  } catch (error) {
    const reason = error.name === "TimeoutError" ? "처리 시간 초과" : "수집 서비스 연결 오류";
    throw new Error(reason);
  }
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(data?.error || `수집 서비스 응답 오류 (HTTP ${response.status})`);
  }
  return response.json();
}

export const POST = handleCallback(async ({run, index}, metadata) => {
  if (!/^[a-f0-9-]{36}$/.test(run) || (index !== undefined && (!Number.isInteger(index) || index < 0))) {
    throw new Error("Invalid collection message");
  }
  let stage = index === undefined ? "listing" : "batch";
  try {
    if (index !== undefined) {
      await requireOk(`/internal/batch/${run}/${index}`);
      return;
    }
    const {batches} = await requireOk("/internal/plan/" + run);
    stage = "dispatch";
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
    const detail = stage === "dispatch" ? "상품 처리 작업을 대기열에 등록할 수 없습니다." : error.message;
    const response = await failRun(run, stage, detail);
    if (!response.ok) throw new Error("Could not save collection failure");
  }
});
