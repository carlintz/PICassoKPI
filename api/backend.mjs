const seedRunId = "00000000-0000-4000-8000-000000000001";
const seedProductCount = 874;

async function seedProducts(request) {
  if (request.method !== "GET") return new Response(null, {status: 405, headers: {Allow: "GET"}});
  const base = process.env.SUPABASE_URL;
  const secret = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!base || !secret) return Response.json({error: "Supabase 연결이 필요합니다."}, {status: 503});

  try {
    const url = new URL("/rest/v1/picasso_products", base);
    url.search = new URLSearchParams({
      run_id: "eq." + seedRunId, select: "result", order: "brand,product_id", limit: String(seedProductCount)
    }).toString();
    const response = await fetch(url, {
      headers: {apikey: secret, Authorization: "Bearer " + secret}, redirect: "error", signal: AbortSignal.timeout(5000)
    });
    if (!response.ok) throw new Error("Supabase request failed");
    const rows = await response.json();
    if (rows.length !== seedProductCount) throw new Error("Incomplete baseline");
    return Response.json({products: rows.map(row => row.result)},
                         {headers: {"Cache-Control": "private, no-store"}});
  } catch {
    return Response.json({error: "Supabase의 검증 데이터를 불러오지 못했습니다."}, {status: 502});
  }
}

const paths = [
  /^\/$/,
  /^\/api\/(state|collect|schedule|reference)$/,
  /^\/api\/runs\/[\w-]+(?:\/(?:csv|product))?$/,
  /^\/images\/[\w-]+\/[\w-]+\.jpg$/,
  /^\/seed-images\/(?:SPAO|WHO)\/\d+$/
];

async function handler(request) {
  const url = new URL(request.url);
  const route = url.searchParams.get("route");
  const pathname = route === null ? url.pathname : "/" + route;
  if (pathname === "/api/seed-products") return seedProducts(request);
  if (!paths.some(pattern => pattern.test(pathname))) {
    return Response.json({error: "지원하지 않는 경로입니다."}, {status: 404});
  }

  const backend = process.env.PICASSO_BACKEND_URL;
  if (!backend) {
    if (pathname === "/") return Response.redirect(new URL("/index.html", request.url), 302);
    return Response.json({error: "수집 서버 URL이 아직 설정되지 않았습니다."}, {status: 503});
  }

  const origin = URL.canParse(backend) ? new URL(backend) : null;
  if (!origin || origin.protocol !== "https:" || origin.username || origin.password) {
    return Response.json({error: "수집 서버의 HTTPS 주소를 확인하세요."}, {status: 503});
  }
  const headers = new Headers();
  for (const name of ["authorization", "content-type", "x-picasso-request"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  try {
    const response = await fetch(new URL(pathname, origin), {
      method: request.method,
      headers,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : await request.arrayBuffer(),
      redirect: "manual"
    });
    const resultHeaders = new Headers({"Cache-Control": "private, no-store"});
    for (const name of ["content-type", "content-disposition", "www-authenticate"]) {
      const value = response.headers.get(name);
      if (value) resultHeaders.set(name, value);
    }
    return new Response(response.body, {status: response.status, headers: resultHeaders});
  } catch {
    return Response.json({error: "수집 서버에 연결하지 못했습니다."}, {status: 502});
  }
}

export {handler as GET, handler as HEAD, handler as POST, handler as PATCH};
