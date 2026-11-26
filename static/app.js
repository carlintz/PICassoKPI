"use strict";
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
const number = value => value.toLocaleString("ko-KR");
const pct = (n, d) => d ? (100 * n / d).toFixed(1) + "%" : "—";
const brands = {SPAO: "스파오", WHO: "후아유"};
const kinds = {
  model: "모델 착장컷", similar: "기준 얼굴 유사", unclear: "유사도 검토 필요",
  product_only: "상품 단독컷", unknown: "미분류"
};
const titles = {overview: "신상품 썸네일 현황", products: "상품 검토", settings: "수집 설정"};
const pageSize = 30;
let demo, state, current, editing, page = 0, lastJob, syncing = false, uploading = false, collectAfterLogin = false;

async function api(url, options = {}) {
  const headers = new Headers(options.headers);
  if (options.method && options.method !== "GET") headers.set("X-PICasso-Request", "1");
  const response = await fetch(url, {...options, headers});
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || "요청을 처리하지 못했습니다.");
    error.status = response.status;
    throw error;
  }
  return data;
}
function send(url, payload, method = "POST") {
  return api(url, {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
}
function notice(message, error = false) {
  $("notice").hidden = !message;
  $("notice").classList.toggle("error", error);
  $("notice").textContent = message;
}
function date(value) {
  return value.includes("T") ? new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul", dateStyle: "medium", timeStyle: "short"
  }).format(new Date(value)) : value;
}
function go() {
  const active = location.hash.slice(1) in titles ? location.hash.slice(1) : "overview";
  for (const key of Object.keys(titles)) {
    $(key).hidden = key !== active;
    document.querySelector(`[data-page="${key}"]`).setAttribute("aria-current", key === active ? "page" : "false");
  }
  $("title").textContent = titles[active];
  if (active === "products") gallery();
}
async function select(id) {
  if (!id) return;
  const result = demo ? demo.run : await api("/api/runs/" + encodeURIComponent(id));
  current = result;
  $("runs").value = id;
  $("export").href = demo ? "/demo.csv" : "/api/runs/" + encodeURIComponent(id) + "/csv";
  if (demo) $("export").download = "picasso-demo.csv";
  page = 0;
  render();
}
function render() {
  if (!current) return;
  const s = current.summary;
  $("source").textContent = `${current.source} · ${s.unknown}개 미분류 · ${s.code}/${s.total}개 품번 확인`;
  const metrics = [
    ["전체 상품", s.total, "브랜드별 중복 제외"],
    ["상품 단독컷", s.product, pct(s.product, s.total) + " · 전체 대비"],
    ["모델 착장컷", s.model, pct(s.model, s.total) + " · 전체 대비"],
    ["기준 얼굴 유사", s.match, pct(s.match, s.model) + " · 착장컷 대비"]
  ];
  $("metrics").innerHTML = metrics.map(([title, count, label]) =>
    `<div class="metric"><span>${title}</span><strong>${number(count)}</strong><small>${label}</small></div>`).join("");
  $("bars").innerHTML = current.groups.map(g => {
    const label = g.tab || brands[g.brand];
    const detail = `${label} · 단독 ${g.product} · 착장 ${g.model} · 미분류 ${g.unknown}`;
    const parts = [g.product, g.model, g.unknown].map(n => `<i style="width:${g.total ? 100 * n / g.total : 0}%"></i>`).join("");
    return `<div class="bar-row ${g.tab ? "sub" : ""}"><span>${esc(label)}</span><div class="bar" role="img" aria-label="${esc(detail)}" title="${esc(detail)}">${parts}</div><strong>${pct(g.model, g.total)}</strong></div>`;
  }).join("");
  $("comparison").innerHTML = current.groups.filter(g => g.tab).map(g =>
    `<tr><th scope="row">${esc(brands[g.brand])} ${esc(g.tab)}</th><td>${number(g.total)}</td>
      <td>${number(g.model)}</td><td>${number(g.compared)}</td>
      <td><a href="#products" data-similar-tab="${esc(g.brand + ":" + g.tab)}" aria-label="${esc(brands[g.brand] + " " + g.tab + " 유사 상품 보기")}">${number(g.match)}</a></td>
      <td>${pct(g.match, g.total)}</td><td>${pct(g.match, g.model)}</td></tr>`).join("");
  $("history").innerHTML = state.runs.slice(0, 6).map(r =>
    `<button class="history-row" data-run="${esc(r.id)}" type="button"><span>${esc(date(r.at))} · ${esc(r.source)}</span><b>${number(r.summary.total)}개 · ${pct(r.summary.model, r.summary.total)}</b></button>`).join("");
  gallery();
}
function imageUrl(row) {
  if (demo) return row.thumbnail_url;
  if (current.id === "20261124-000000-seed") {
    return `/seed-images/${encodeURIComponent(row.brand)}/${encodeURIComponent(row.product_id)}`;
  }
  return row.image_file ? `/images/${encodeURIComponent(current.id)}/${encodeURIComponent(row.image_file)}` : row.thumbnail_url;
}
function classification(row) {
  if (row.thumbnail_type === "model" && ["similar", "unclear"].includes(row.similarity)) {
    return row.similarity;
  }
  return row.thumbnail_type;
}
function gallery() {
  if (!current || $("products").hidden) return;
  const term = $("search").value.trim().toLowerCase();
  const [brand, tab] = $("tab-filter").value.split(":");
  const kind = $("kind-filter").value;
  const filtered = current.products.filter(r =>
    (!brand || brand === r.brand) && (!tab || r.tabs.includes(tab)) &&
    (!term || [r.name, r.product_id, r.code].some(v => String(v).toLowerCase().includes(term))) &&
    (!kind || (kind === "missing" ? !r.code :
      kind === "all_models" ? r.thumbnail_type === "model" : classification(r) === kind)));
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  page = Math.min(page, pages - 1);
  $("filtered").textContent = `${number(filtered.length)}개 상품`;
  $("gallery").innerHTML = filtered.slice(page * pageSize, (page + 1) * pageSize).map(r => {
    const label = kinds[classification(r)];
    return `<button class="card" data-product="${esc(r.brand + ":" + r.product_id)}"><img src="${esc(imageUrl(r))}" alt="" loading="lazy"><span>${esc(brands[r.brand])} · ${esc(label)}</span><strong title="${esc(r.name)}">${esc(r.name)}</strong><small>${esc(r.code || "품번 미확인")} · ${esc(r.product_id)}</small></button>`;
  }).join("") || "<p>조건에 맞는 상품이 없습니다.</p>";
  $("pagination").innerHTML = `<button id="prev" ${page ? "" : "disabled"}>이전</button><span>${page + 1} / ${pages}</span><button id="next" ${page + 1 < pages ? "" : "disabled"}>다음</button>`;
}
function edit(row) {
  editing = {...row, run_id: current.id};
  $("edit-name").textContent = row.name;
  $("edit-image").src = imageUrl(row);
  $("detail").href = row.detail_url || row.list_url;
  $("item-error").textContent = row.error || (row.price ? number(row.price) + "원" : "");
  $("edit-code").value = row.code;
  const kind = classification(row);
  $("edit-type").value = kind === "model" && row.similarity !== "other" ? "unscanned" : kind;
  $("review-error").textContent = "";
  $("save-review").disabled = Boolean(demo) || state.operator === false;
  $("edit").showModal();
}
function renderReferences() {
  for (const cell of document.querySelectorAll("[data-reference-count]")) {
    const [brand, group] = cell.dataset.referenceCount.split(":");
    cell.textContent = number(state.references?.[brand]?.[group] ?? 0) + "장";
  }
  const message = demo ? "수집 서버 연결 후 기준 이미지를 등록할 수 있습니다."
    : state.operator === false ? "운영자 로그인 후 기준 이미지를 등록할 수 있습니다."
    : !state.vision ? "얼굴 분석 모델을 사용할 수 없습니다."
    : state.job.state === "running" ? "수집 완료 후 기준 이미지를 등록할 수 있습니다." : "";
  $("reference").querySelector("fieldset").disabled = Boolean(message) || uploading;
  $("reference-status").textContent = message;
}
async function sync(initial = false) {
  if (syncing) return;
  syncing = true;
  try {
    if (!demo) {
      try {
        state = await api("/api/state");
      } catch (error) {
        if (error.status !== 503 || !["/", "/index.html"].includes(location.pathname)) throw error;
        const [baseline, storage] = await Promise.all([
          api("/demo.json"),
          api("/api/seed-products").catch(() => null)
        ]);
        demo = baseline;
        if (storage) {
          const key = row => row.brand + ":" + row.product_id;
          const products = new Map(storage.products.map(row => [key(row), row]));
          if (products.size === demo.run.products.length &&
              demo.run.products.every(row => products.has(key(row)))) {
            demo.run.products = demo.run.products.map(row => products.get(key(row)));
            demo.run.source += " · Supabase";
          }
        }
      }
    }
    if (demo) {
      state = demo.state;
      $("schedule").querySelectorAll("input, button").forEach(input => { input.disabled = true; });
    }
    const {job, runs, schedule} = state;
    const selected = current?.id;
    $("runs").innerHTML = runs.map(r => `<option value="${esc(r.id)}">${esc(date(r.at))} · ${esc(r.source)}</option>`).join("");
    $("runs").value = selected || runs[0]?.id || "";
    $("collect").disabled = Boolean(demo) || job.state === "running";
    $("operator-login").hidden = state.operator !== false;
    if (!demo) {
      $("schedule").querySelector("button").disabled = state.operator === false;
      $("enabled").disabled = Boolean(state.scheduling_paused);
      if (state.scheduling_paused) {
        $("schedule-note").textContent = "자동 실행 보류 중 · 한국 시간 기준 시각을 저장할 수 있습니다.";
      }
    }
    if (demo) notice("검증 데이터 미리보기 · 수집 서버 연결 후 새 수집과 설정 변경을 사용할 수 있습니다.");
    $("collect").textContent = job.state === "running" ? "수집 중…" : "↻ 지금 수집";
    if (job.state === "running") notice(job.total ? `수집·분류 중 ${job.done} / ${job.total}개` : job.message);
    if (job.state === "failed" && job.state !== lastJob) notice(job.message, true);
    if (job.state === "complete" && lastJob === "running") {
      notice("수집을 완료했습니다.");
      await select(job.id);
    }
    lastJob = job.state;
    if (initial) {
      $("enabled").checked = schedule.enabled;
      $("time").value = schedule.time;
      await select($("runs").value);
    } else if (current) renderHistory();
    renderReferences();
  } finally { syncing = false; }
}
function renderHistory() {
  const existing = $("history").querySelector("[data-run]");
  if (existing?.dataset.run !== state.runs[0]?.id) render();
}
async function action(task) {
  try { await task(); } catch (error) { notice(error.message, true); }
}
window.addEventListener("hashchange", go);
$("runs").onchange = e => action(() => select(e.target.value));
$("history").onclick = e => {
  const id = e.target.closest("[data-run]")?.dataset.run;
  if (id) action(() => select(id));
};
async function collectNow() {
  await send("/api/collect", {});
  await sync();
}
function openLogin(startCollection) {
  collectAfterLogin = startCollection;
  $("login-error").textContent = "";
  $("login-form").reset();
  $("login-dialog").showModal();
}
$("operator-login").onclick = () => openLogin(false);
$("close-login").onclick = () => $("login-dialog").close();
$("login-form").onsubmit = async e => {
  e.preventDefault();
  const input = $("operator-password");
  try {
    await send("/api/login", {password: input.value});
    input.value = "";
    $("login-dialog").close();
    await action(async () => {
      await sync();
      if (collectAfterLogin) await collectNow();
    });
  } catch (error) {
    $("login-error").textContent = error.message;
  }
};
$("collect").onclick = () => state?.operator === false ? openLogin(true) : action(collectNow);
function showSimilar(tab = "") {
  $("tab-filter").value = tab;
  $("kind-filter").value = "similar";
  $("search").value = "";
  page = 0;
  gallery();
}
$("similar-link").onclick = () => showSimilar();
$("comparison").onclick = e => {
  const link = e.target.closest("[data-similar-tab]");
  if (link) showSimilar(link.dataset.similarTab);
};
for (const id of ["search", "tab-filter", "kind-filter"]) $(id).oninput = () => { page = 0; gallery(); };
$("gallery").onclick = e => {
  const key = e.target.closest("[data-product]")?.dataset.product;
  if (key) edit(current.products.find(r => r.brand + ":" + r.product_id === key));
};
$("pagination").onclick = e => {
  if (e.target.id === "prev") page--;
  else if (e.target.id === "next") page++;
  else return;
  gallery();
};
$("schedule").onsubmit = e => {
  e.preventDefault();
  action(async () => {
    await send("/api/schedule", {enabled: $("enabled").checked, time: $("time").value});
    notice("자동 수집 설정을 저장했습니다.");
  });
};
$("reference").onsubmit = async e => {
  e.preventDefault();
  const form = e.currentTarget;
  const {brand, group, image} = form.elements;
  const selection = [...image.files];
  const status = $("reference-upload-status");
  let added = 0, duplicate = 0;
  const rejected = [];
  uploading = true;
  renderReferences();
  for (const [index, file] of selection.entries()) {
    status.textContent = `기준 이미지 등록 중 ${index + 1} / ${selection.length}장`;
    try {
      if (file.size >= 4_000_000) throw new Error("한 장당 4MB 미만이어야 합니다.");
      const body = new FormData();
      body.set("brand", brand.value);
      body.set("group", group.value);
      body.set("image", file);
      const result = await api("/api/reference", {method: "POST", body});
      if (result.added) added++;
      else duplicate++;
    } catch (error) {
      rejected.push(`${file.name} (${error.message})`);
    }
  }
  image.value = "";
  uploading = false;
  renderReferences();
  status.textContent = [`${added}장 등록`, duplicate ? `이미 등록 ${duplicate}장` : "",
    rejected.length ? `등록 실패 ${rejected.length}장 · ${rejected.join(", ")}` : ""].filter(Boolean).join(" · ");
  await action(sync);
};
$("close").onclick = () => $("edit").close();
$("review").onsubmit = async e => {
  e.preventDefault();
  const save = $("save-review");
  if (save.disabled) return;
  const item = editing;
  const {run_id, brand, product_id} = item;
  const kind = $("edit-type").value;
  const thumbnailType = ["product_only", "unknown"].includes(kind) ? kind : "model";
  const similarity = thumbnailType !== "model" ? "not_applicable" : kind === "model" ? "other" : kind;
  save.disabled = true;
  $("review-error").textContent = "";
  try {
    await send(`/api/runs/${run_id}/product`, {
      brand, product_id, code: $("edit-code").value,
      thumbnail_type: thumbnailType, similarity
    }, "PATCH");
  } catch (error) {
    if (editing === item && $("edit").open) {
      $("review-error").textContent = error.message;
      save.disabled = false;
    } else {
      notice(error.message, true);
    }
    return;
  }
  if (editing === item) $("edit").close();
  await action(async () => {
    if (current.id === run_id) await select(run_id);
    notice("상품 분류를 저장했습니다.");
  });
};
go();
action(() => sync(true));
setInterval(() => action(sync), 4500);
