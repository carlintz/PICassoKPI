import {timingSafeEqual} from "node:crypto";
import {send} from "@vercel/queue";
import {failRun, topic, worker} from "../worker.mjs";

export async function POST(request) {
  const secret = process.env.PICASSO_SCHEDULER_KEY;
  const expected = Buffer.from("Bearer " + (secret || ""));
  const auth = Buffer.from(request.headers.get("authorization") || "");
  if (!secret || auth.length !== expected.length || !timingSafeEqual(auth, expected)) {
    return Response.json({error: "찾을 수 없습니다."}, {status: 404});
  }
  try {
    const response = await worker("/internal/schedule/tick");
    if (!response.ok) return Response.json({error: "예약 수집을 확인할 수 없습니다."}, {status: 502});
    const {id} = await response.json();
    if (!id) return Response.json({queued: false});
    try {
      await send(topic, {run: id}, {idempotencyKey: id + ":plan"});
    } catch {
      await failRun(id, "enqueue", "예약 수집 요청을 대기열에 등록할 수 없습니다.");
      return Response.json({error: "예약 수집을 대기열에 등록할 수 없습니다."}, {status: 502});
    }
    return Response.json({queued: true}, {status: 202});
  } catch {
    return Response.json({error: "예약 수집 서비스 연결 오류"}, {status: 502});
  }
}
