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
