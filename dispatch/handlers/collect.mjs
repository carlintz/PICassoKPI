import {send} from "@vercel/queue";
import {failRun, topic, worker} from "../worker.mjs";

export async function POST(request) {
  if (request.headers.get("X-PICasso-Request") !== "1") {
    return Response.json({error: "대시보드에서 요청을 다시 실행하세요."}, {status: 403});
  }
  try {
    const response = await worker("/internal/start", {Cookie: request.headers.get("cookie") || ""});
    const result = await response.json();
    if (!response.ok) return Response.json(result, {status: response.status});
    try {
      await send(topic, {run: result.id}, {idempotencyKey: result.id + ":plan"});
    } catch {
      await failRun(result.id, "enqueue", "수집 시작 요청을 대기열에 등록할 수 없습니다.");
      throw new Error("Queue publish failed");
    }
    return Response.json({ok: true, id: result.id}, {status: 202});
  } catch {
    return Response.json({error: "수집 요청을 큐에 등록하지 못했습니다. 다시 시도해 주세요."}, {status: 502});
  }
}
