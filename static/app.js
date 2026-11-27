"use strict";
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
const number = value => value.toLocaleString("ko-KR");
const pct = (n, d) => d ? (100 * n / d).toFixed(1) + "%" : "—";
const brands = {SPAO: "스파오", WHO: "후아유"};
const kinds = {
  product_only: "상품 단독컷", model: "일반 모델 착장컷", similar: "AI 모델 착장컷", review: "검토 필요"
};
const generalModels = summary => summary.model - summary.match - summary.unclear;
const needsReview = summary => summary.unknown + summary.unclear;
const titles = {overview: "신상품 썸네일 현황", products: "상품 검토", settings: "수집 설정", guide: "사용 매뉴얼"};
const seedId = "20261124-000000-seed";
const pageSize = 30;
const selection = new Set();
let demo, state, current, editing, page = 0, lastJob, syncing = false, uploading = false, collectAfterLogin = false;
let savingBulk = false;
let weekly, weeklyCatalog;
let weeklyRuns = [];
let scheduleDirty = false, logPage = 1, logRequest, loggedJob;

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
  const hash = location.hash.slice(1);
  const guideSection = hash.startsWith("guide-") ? $(hash) : null;
  const active = guideSection?.classList.contains("guide-section") ? "guide" : Object.hasOwn(titles, hash) ? hash : "overview";
  for (const key of Object.keys(titles)) {
    $(key).hidden = key !== active;
    document.querySelector(`[data-page="${key}"]`).setAttribute("aria-current", key === active ? "page" : "false");
  }
  $("title").textContent = titles[active];
  if (active === "products") gallery();
  if (active === "overview") loadWeekly();
  if (active === "settings") loadScheduleLogs();
  if (active === "guide" && guideSection) {
    guideSection.focus({preventScroll: true});
    guideSection.scrollIntoView({block: "start"});
  } else if (active === "guide") {
    window.scrollTo(0, 0);
  }
}
async function select(id) {
  if (!id) return;
  const result = demo ? demo.run : await api("/api/runs/" + encodeURIComponent(id));
  if (current?.id !== result.id) {
    clearSelection();
    page = 0;
  }
  current = result;
  $("runs").value = id;
  $("export").href = demo ? "/demo.csv" : "/api/runs/" + encodeURIComponent(id) + "/csv";
  if (demo) $("export").download = "picasso-demo.csv";
  render();
}
function renderMetrics(id, s, isWeekly = false) {
  const metrics = [
    [isWeekly ? "신규 상품" : "전체 상품", s.total, isWeekly ? "브랜드·스타일코드별 중복 제외" : "브랜드별 중복 제외"],
    [kinds.product_only, s.product, pct(s.product, s.total) + (isWeekly ? " · 신규 상품 대비" : " · 전체 대비")],
    [kinds.model, generalModels(s), pct(generalModels(s), s.model) + " · 전체 모델 착장컷 대비"],
    [kinds.similar, s.match, pct(s.match, s.model) + " · 전체 모델 착장컷 대비"]
  ];
  $(id).innerHTML = metrics.map(([title, count, label]) =>
    `<div class="metric"><span>${title}</span><strong>${number(count)}</strong><small>${label}</small></div>`).join("");
}
function renderWeekly() {
  const brand = $("weekly-brand").value;
  const data = weekly?.data;
  if (!data?.summary) return;
  renderMetrics("weekly-metrics", brand ? data.brands[brand] : data.summary, true);
}
function weeklyOptions(runs) {
  return runs.map(run => `<option value="${esc(run.id)}">${esc(date(run.at))}</option>`).join("");
}
function updateWeeklyComparison() {
  const picker = $("weekly-compare");
  const selected = picker.value;
  const index = weeklyRuns.findIndex(run => run.id === $("weekly-run").value);
  const earlier = index < 0 ? [] : weeklyRuns.slice(index + 1);
  picker.innerHTML = '<option value="">최근 7일 자동 비교</option>' + weeklyOptions(earlier);
  picker.value = earlier.some(run => run.id === selected) ? selected : "";
  picker.disabled = !earlier.length;
}
async function loadWeeklyCatalog(revision) {
  const request = {revision};
  weeklyCatalog = request;
  weekly = null;
  $("weekly-run").disabled = true;
  $("weekly-compare").disabled = true;
  $("weekly-brand").disabled = true;
  $("weekly-metrics").hidden = true;
  $("weekly-status").textContent = "비교할 수집 기록을 확인하는 중입니다.";
  try {
    const runs = demo ? [] : (await api("/api/weekly/runs")).runs;
    if (weeklyCatalog !== request) return;
    const picker = $("weekly-run");
    const selected = picker.value;
    const followLatest = !selected || selected === weeklyRuns[0]?.id;
    weeklyRuns = runs;
    picker.innerHTML = weeklyOptions(runs);
    picker.value = !followLatest && runs.some(run => run.id === selected) ? selected : runs[0]?.id || "";
    picker.disabled = !runs.length;
    updateWeeklyComparison();
    request.ready = true;
  } catch {
    if (weeklyCatalog !== request) return;
    weeklyCatalog = undefined;
    $("weekly-status").textContent = "수집 기록을 불러오지 못했습니다.";
  }
}
async function loadWeekly() {
  if (!state || $("overview").hidden) return;
  const revision = state.runs.find(run => run.id !== seedId)?.id ?? "";
  if (weeklyCatalog?.revision !== revision) await loadWeeklyCatalog(revision);
  if (!weeklyCatalog?.ready || $("overview").hidden) return;
  const run = $("weekly-run").value;
  const compare = $("weekly-compare").value;
  if (weekly?.run === run && weekly.compare === compare) return;
  const request = {run, compare};
  weekly = request;
  $("weekly-brand").disabled = true;
  $("weekly-metrics").hidden = true;
  const status = $("weekly-status");
  if (!run || $("weekly-compare").disabled) {
    status.textContent = "완료된 공식몰 수집 기록이 두 건 이상이면 비교 결과를 표시합니다.";
    return;
  }
  status.textContent = "신규 상품을 비교하는 중입니다.";
  try {
    const query = compare ? `?compare=${encodeURIComponent(compare)}` : "";
    const data = await api(`/api/runs/${encodeURIComponent(run)}/weekly${query}`);
    if (weekly !== request) return;
    if (!data?.summary) {
      status.textContent = "완료된 공식몰 수집 기록이 두 건 이상이면 비교 결과를 표시합니다.";
      return;
    }
    request.data = data;
    const mode = compare ? "선택 기록 비교" : "최근 7일 자동 비교";
    status.textContent = `${date(data.start_at)} ~ ${date(data.end_at)} · ${mode} · 스타일코드 확인 상품 기준`;
    $("weekly-brand").disabled = false;
    renderWeekly();
    $("weekly-metrics").hidden = false;
  } catch {
    if (weekly === request) status.textContent = "신규 상품 집계를 불러오지 못했습니다.";
  }
}
function renderBrandCharts(groups) {
  $("brand-charts").innerHTML = groups.filter(g => !g.tab).map(g => {
    const brand = brands[g.brand];
    const share = pct(g.match, g.model);
    const progress = g.model ? 100 * g.match / g.model : 0;
    const ratio = `${number(g.match)} / ${number(g.model)}개`;
    const chartLabel = `${brand} AI 모델 착장컷 ${number(g.match)}개, 전체 모델 착장컷 ${number(g.model)}개, 비중 ${share}`;
    const tabs = groups.filter(tab => tab.brand === g.brand && tab.tab).map(tab => `
      <a class="brand-chart-tab" href="#products" data-similar-tab="${esc(tab.brand + ":" + tab.tab)}"
         aria-label="${esc(`${brand} ${tab.tab} AI 모델 착장컷 ${pct(tab.match, tab.model)} 보기`)}">
        <span>${esc(tab.tab)}</span><strong>${pct(tab.match, tab.model)}</strong>
        <small>AI ${number(tab.match)} / 모델 ${number(tab.model)}개</small>
      </a>`).join("");
    const counts = {product_only: g.product, model: generalModels(g), similar: g.match, review: needsReview(g)};
    const breakdown = Object.entries(kinds).map(([kind, label]) => `
      <div data-kind="${kind}"><dt>${esc(label)}</dt><dd>${number(counts[kind])}<small>개</small></dd></div>`).join("");
    return `<article class="brand-chart">
      <div class="brand-chart-heading">
        <div><h3>${esc(brand)}</h3><span>전체 상품 ${number(g.total)}개</span></div>
        <a href="#products" data-similar-tab="${esc(g.brand)}" aria-label="${esc(brand + " AI 모델 착장컷 보기")}">AI 상품 보기 →</a>
      </div>
      <div class="brand-chart-body">
        <div class="brand-donut" style="--share: ${progress}%" role="img" aria-label="${esc(chartLabel)}">
          <div><strong>${share}</strong><small>${ratio}</small></div>
        </div>
        <div class="brand-chart-tabs">${tabs}</div>
      </div>
      <dl class="brand-breakdown" aria-label="${esc(brand + " 분류별 상품 수")}">${breakdown}</dl>
    </article>`;
  }).join("");
}
function render() {
  if (!current) return;
  const s = current.summary;
  $("source").textContent = `${current.source} · ${number(needsReview(s))}개 검토 필요 · ${s.code}/${s.total}개 스타일코드 확인`;
  renderMetrics("metrics", s);
  renderBrandCharts(current.groups);
  $("comparison").innerHTML = current.groups.filter(g => g.tab).map(g =>
    `<tr><th scope="row">${esc(brands[g.brand])} ${esc(g.tab)}</th><td>${number(g.total)}</td>
      <td>${number(g.model)}</td>
      <td><a href="#products" data-similar-tab="${esc(g.brand + ":" + g.tab)}" aria-label="${esc(brands[g.brand] + " " + g.tab + " AI 모델 착장컷 보기")}">${number(g.match)}</a></td>
      <td>${pct(g.match, g.total)}</td><td>${pct(g.match, g.model)}</td></tr>`).join("");
  $("history").innerHTML = state.runs.slice(0, 6).map(r =>
    `<button class="history-row" data-run="${esc(r.id)}" type="button"><span>${esc(date(r.at))} · ${esc(r.source)}</span><b>${number(r.summary.total)}개 · ${pct(r.summary.model, r.summary.total)}</b></button>`).join("");
  gallery();
}
function imageUrl(row) {
  if (demo) return row.thumbnail_url;
  if (current.id === seedId) {
    return `/seed-images/${encodeURIComponent(row.brand)}/${encodeURIComponent(row.product_id)}`;
  }
  return row.image_file ? `/images/${encodeURIComponent(current.id)}/${encodeURIComponent(row.image_file)}` : row.thumbnail_url;
}
function classification(row) {
  if (row.thumbnail_type === "product_only") return "product_only";
  if (row.thumbnail_type !== "model" || row.similarity === "unclear") return "review";
  return row.similarity === "similar" ? "similar" : "model";
}
function reviewFields(kind, row) {
  if (kind === "product_only" || kind === "review") {
    return {thumbnail_type: kind, similarity: "not_applicable"};
  }
  const similarity = kind === "similar" ? "similar" : row && classification(row) === "model" ? row.similarity : "other";
  return {thumbnail_type: "model", similarity};
}
function selectPageBoxes() {
  return [...$("gallery").querySelectorAll("[data-select-product]")];
}
function bulkError(message = "") {
  $("bulk-error").textContent = message;
  $("bulk-error").hidden = !message;
}
function clearSelection() {
  selection.clear();
  $("bulk-type").value = "";
  bulkError();
}
function renderSelection() {
  const allowed = Boolean(current) && !demo && state.operator !== false;
  const boxes = selectPageBoxes();
  $("bulk-review").hidden = !allowed;
  for (const box of boxes) {
    box.closest("label").hidden = !allowed;
    box.disabled = !allowed || savingBulk;
    box.checked = selection.has(box.dataset.selectProduct);
  }
  const checked = boxes.filter(box => box.checked).length;
  $("select-page").checked = checked > 0 && checked === boxes.length;
  $("select-page").indeterminate = checked > 0 && checked < boxes.length;
  $("select-page").disabled = savingBulk || !boxes.length;
  $("bulk-count").textContent = `${number(selection.size)}개 선택`;
  $("clear-selection").disabled = savingBulk || !selection.size;
  $("bulk-type").disabled = savingBulk || !selection.size;
  $("save-bulk").disabled = !allowed || savingBulk || !selection.size || !$("bulk-type").value;
  $("save-bulk").textContent = savingBulk ? "저장 중…" : "일괄 분류 저장";
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
    const kind = classification(r);
    const label = kinds[kind];
    const key = esc(r.brand + ":" + r.product_id);
    return `<div class="card-tile"><button class="card" data-product="${key}"><img src="${esc(imageUrl(r))}" alt="" loading="lazy"><span class="card-kind" data-kind="${esc(kind)}">${esc(brands[r.brand])} · ${esc(label)}</span><strong title="${esc(r.name)}">${esc(r.name)}</strong><small>${esc(r.code || "스타일코드 미확인")} · ${esc(r.product_id)}</small></button><label class="card-select" hidden><input type="checkbox" data-select-product="${key}" aria-label="${esc(r.name + " · " + r.product_id + " 선택")}"></label></div>`;
  }).join("") || "<p>조건에 맞는 상품이 없습니다.</p>";
  $("pagination").innerHTML = `<button id="prev" ${page ? "" : "disabled"}>이전</button><span>${page + 1} / ${pages}</span><button id="next" ${page + 1 < pages ? "" : "disabled"}>다음</button>`;
  renderSelection();
}
function edit(row) {
  editing = {...row, run_id: current.id};
  $("edit-name").textContent = row.name;
  $("edit-image").src = imageUrl(row);
  $("detail").href = row.detail_url || row.list_url;
  $("item-error").textContent = row.error?.replace("품번: ", "스타일코드: ") || (row.price ? number(row.price) + "원" : "");
  $("edit-code").value = row.code;
  $("edit-type").value = classification(row);
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
function showScheduleFields() {
  const cadence = $("schedule-cadence").value;
  $("schedule-weekdays").hidden = $("schedule-weekdays").disabled = cadence !== "weekly";
  $("schedule-monthly").hidden = $("schedule-month-day").disabled = cadence !== "monthly";
  $("schedule-once").hidden = $("schedule-date").disabled = cadence !== "once";
}
function renderSchedule(rule) {
  const editable = !demo && state.operator !== false;
  $("schedule-fields").disabled = !editable;
  $("schedule-note").textContent = editable ? "한국 시간 기준으로 실행할 일정을 설정합니다."
    : demo ? "수집 서버 연결 후 예약을 설정할 수 있습니다." : "운영자 로그인 후 일정을 변경할 수 있습니다.";
  if (state.scheduler_ready === false) {
    $("schedule-note").textContent = editable ? "자동 실행 연결 대기 중 · 일정은 한국 시간 기준으로 저장할 수 있습니다."
      : "자동 실행 연결 대기 중 · 운영자 로그인 후 일정을 저장할 수 있습니다.";
  }
  $("guide-schedule-availability").hidden = state.scheduler_ready !== false;
  if (!scheduleDirty) {
    $("enabled").checked = rule.enabled;
    $("schedule-cadence").value = rule.cadence || "daily";
    $("time").value = rule.time;
    $("schedule-month-day").value = rule.month_day || 1;
    $("schedule-date").value = rule.date || "";
    for (const input of $("schedule-weekdays").querySelectorAll("input")) {
      input.checked = (rule.weekdays || [1]).includes(Number(input.value));
    }
    showScheduleFields();
  }
  const prefix = state.scheduler_ready === false ? "저장된 예약" : "다음 실행";
  $("schedule-next").textContent = `${prefix} · ${rule.next_at ? date(rule.next_at) : "예약 없음"}`;
}
async function loadScheduleLogs() {
  if (!state || $("settings").hidden) return;
  const request = {};
  logRequest = request;
  const status = $("schedule-log-status");
  try {
    const {logs, more} = demo ? {logs: [], more: false} : await api(`/api/schedule/logs?page=${logPage}`);
    if (logRequest !== request) return;
    const labels = {queued: "대기 중", running: "수집 중", complete: "성공", failed: "실패"};
    const stamp = value => value ? esc(date(value)) : "—";
    $("schedule-logs").innerHTML = logs.map(row => {
      const finished = row.status === "complete";
      const warning = finished && row.errors > 0;
      const result = warning ? "성공 · 일부 오류" : labels[row.status];
      const detail = finished ? `상품 ${number(row.expected_count)}개${warning ? ` · 오류 ${number(row.errors)}건` : ""}`
        : row.error || "—";
      const report = warning ? ` <a href="/api/runs/${encodeURIComponent(row.id)}/csv">CSV</a>` : "";
      return `<tr><th scope="row">${stamp(row.scheduled_at)}</th><td>${stamp(row.created_at)}</td><td>${stamp(row.finished_at)}</td>
        <td><span class="schedule-result" data-status="${esc(warning ? "warning" : row.status)}">${esc(result)}</span></td>
        <td>${esc(detail)}${report}</td></tr>`;
    }).join("");
    status.textContent = logs.length ? "한국 시간 기준 · 최신 예약부터 표시합니다." : "아직 자동 수집 실행 기록이 없습니다.";
    $("schedule-log-table").hidden = !logs.length && logPage === 1;
    $("schedule-log-pages").hidden = !more && logPage === 1;
    $("schedule-log-prev").disabled = logPage === 1;
    $("schedule-log-next").disabled = !more;
    $("schedule-log-page").textContent = `${logPage} 페이지`;
  } catch {
    if (logRequest === request) status.textContent = "실행 기록을 불러오지 못했습니다. 새로고침을 눌러 주세요.";
  }
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
    }
    const {job, runs, schedule} = state;
    const selected = current?.id;
    $("runs").innerHTML = runs.map(r => `<option value="${esc(r.id)}">${esc(date(r.at))} · ${esc(r.source)}</option>`).join("");
    $("runs").value = selected || runs[0]?.id || "";
    $("collect").disabled = Boolean(demo) || job.state === "running";
    $("operator-login").hidden = state.operator !== false;
    renderSchedule(schedule);
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
      await select($("runs").value);
    } else if (current) {
      renderHistory();
      if (!$("products").hidden) renderSelection();
    }
    renderReferences();
    loadWeekly();
    const latestJob = `${job.id}:${job.state}:${job.total}`;
    if (loggedJob !== latestJob) {
      loggedJob = latestJob;
      if (logPage === 1) loadScheduleLogs();
    }
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
$("weekly-run").onchange = () => { updateWeeklyComparison(); loadWeekly(); };
$("weekly-compare").onchange = loadWeekly;
$("weekly-brand").onchange = renderWeekly;
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
  clearSelection();
  page = 0;
  gallery();
}
$("similar-link").onclick = () => showSimilar();
for (const id of ["brand-charts", "comparison"]) {
  $(id).onclick = e => {
    const link = e.target.closest("[data-similar-tab]");
    if (link) showSimilar(link.dataset.similarTab);
  };
}
for (const id of ["search", "tab-filter", "kind-filter"]) $(id).oninput = () => {
  clearSelection();
  page = 0;
  gallery();
};
$("gallery").onclick = e => {
  const key = e.target.closest("[data-product]")?.dataset.product;
  if (key) edit(current.products.find(r => r.brand + ":" + r.product_id === key));
};
function toggleProduct(box, checked) {
  const key = box.dataset.selectProduct;
  if (checked) selection.add(key);
  else selection.delete(key);
}
$("gallery").onchange = e => {
  if (!e.target.matches("[data-select-product]")) return;
  toggleProduct(e.target, e.target.checked);
  renderSelection();
};
$("select-page").onchange = e => {
  for (const box of selectPageBoxes()) toggleProduct(box, e.target.checked);
  renderSelection();
};
$("clear-selection").onclick = () => { clearSelection(); renderSelection(); };
$("bulk-type").append(...Array.from($("edit-type").options, option => option.cloneNode(true)));
$("bulk-type").onchange = renderSelection;
$("bulk-review").onsubmit = async e => {
  e.preventDefault();
  if ($("save-bulk").disabled) return;
  const id = current.id;
  const products = Array.from(selection, key => {
    const [brand, product_id] = key.split(":");
    return {brand, product_id};
  });
  const fields = reviewFields($("bulk-type").value);
  savingBulk = true;
  bulkError();
  renderSelection();
  let result;
  try {
    result = await send(`/api/runs/${encodeURIComponent(id)}/products`, {products, ...fields}, "PATCH");
  } catch (error) {
    bulkError(error.message);
  } finally {
    savingBulk = false;
    if (result && current.id === id) clearSelection();
    renderSelection();
  }
  if (!result) return;
  weekly = null;
  const message = `${number(result.updated)}개 상품의 분류를 저장했습니다.`;
  try {
    if (current.id === id) await select(id);
    notice(message);
  } catch {
    notice(`${message} 최신 목록을 보려면 페이지를 새로고침하세요.`, true);
  }
};
$("pagination").onclick = e => {
  if (e.target.id === "prev") page--;
  else if (e.target.id === "next") page++;
  else return;
  gallery();
};
$("schedule").oninput = () => { scheduleDirty = true; showScheduleFields(); };
$("schedule-log-refresh").onclick = loadScheduleLogs;
$("schedule-log-prev").onclick = () => { if (logPage > 1) { logPage--; loadScheduleLogs(); } };
$("schedule-log-next").onclick = () => { logPage++; loadScheduleLogs(); };
$("schedule").onsubmit = e => {
  e.preventDefault();
  action(async () => {
    const {schedule} = await send("/api/schedule", {enabled: $("enabled").checked, time: $("time").value,
      cadence: $("schedule-cadence").value,
      weekdays: Array.from($("schedule-weekdays").querySelectorAll("input:checked"), input => Number(input.value)),
      month_day: Number($("schedule-month-day").value), date: $("schedule-date").value});
    state.schedule = schedule;
    scheduleDirty = false;
    renderSchedule(schedule);
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
  save.disabled = true;
  $("review-error").textContent = "";
  try {
    await send(`/api/runs/${run_id}/product`, {
      brand, product_id, code: $("edit-code").value,
      ...reviewFields($("edit-type").value, item)
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
  weekly = null;
  await action(async () => {
    if (current.id === run_id) await select(run_id);
    notice("상품 분류를 저장했습니다.");
  });
};
go();
action(() => sync(true));
setInterval(() => action(sync), 4500);
