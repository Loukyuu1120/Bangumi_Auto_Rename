// ─────────────────── STATE ───────────────────
const S = {
  page: 1,
  pageSize: 100,
  totalItems: 0,
  selected: new Set(),
  browsePath: "",
  browseSel: [],
  addStep: 1,
  configData: {},
  logLines: [],
  autoRefreshTimer: null,
  sortBy: "processed_at",
  sortOrder: "desc",
  _monitorPathEditIndex: null,
  _secondaryRules: null,
  _lastCheckedIndex: null,
};

// ─────────────────── DEFAULT SECONDARY RULES ───────────────────
const DEFAULT_SECONDARY_RULES = {
  movie: [
    { name: "演唱会", conditions: { genre_ids: "10402" } },
    { name: "纪录片电影", conditions: { genre_ids: "99" } },
    { name: "动漫电影", conditions: { genre_ids: "16", origin_country: "JP" } },
    { name: "动画电影", conditions: { genre_ids: "16" } },
    { name: "华语电影", conditions: { original_language: "zh,cn,bo,za" } },
    { name: "外语电影", conditions: {} },
  ],
  tv: [
    { name: "儿童", conditions: { genre_ids: "10762" } },
    {
      name: "国漫",
      conditions: { genre_ids: "16", origin_country: "CN,TW,HK" },
    },
    { name: "日番", conditions: { genre_ids: "16", origin_country: "JP" } },
    {
      name: "美漫",
      conditions: { genre_ids: "16", origin_country: "US,CA,GB,FR,DE" },
    },
    { name: "纪录片剧集", conditions: { genre_ids: "99" } },
    { name: "综艺", conditions: { genre_ids: "10764,10767" } },
    { name: "国产剧", conditions: { origin_country: "CN,TW,HK,SG" } },
    {
      name: "欧美剧",
      conditions: { origin_country: "US,FR,GB,DE,ES,IT,NL,PT,RU,UK" },
    },
    { name: "日韩剧", conditions: { origin_country: "JP,KP,KR,TH,IN,SG" } },
    { name: "其它", conditions: {} },
  ],
};

// ─────────────────── NOTIFY ───────────────────
function notify(msg, type = "info", timeout = 3500) {
  const c = document.getElementById("notifContainer");
  const el = document.createElement("div");
  el.className = `notif ${type}`;
  el.textContent = msg;
  el.onclick = () => el.remove();
  c.appendChild(el);
  setTimeout(() => el.remove(), timeout);
}

// ─────────────────── FETCH HELPERS ───────────────────
async function api(url, opts = {}) {
  try {
    const r = await fetch(url, opts);
    const data = await r.json();
    return { ok: r.ok, status: r.status, data };
  } catch (e) {
    return { ok: false, status: 0, data: { error: e.message } };
  }
}

async function GET(url) {
  return api(url);
}
async function POST(url, body) {
  return api(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
async function PUT(url, body) {
  return api(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
async function DEL(url) {
  return api(url, { method: "DELETE" });
}

// ─────────────────── TAB NAVIGATION ───────────────────
function showTab(name) {
  ["tasks", "info"].forEach((t) => {
    document.getElementById(`pane-${t}`).style.display = "none";
    document.getElementById(`tab-${t}`).classList.remove("active");
  });
  document.getElementById(`pane-${name}`).style.display = "flex";
  document.getElementById(`tab-${name}`).classList.add("active");
  if (name === "info") {
    refreshStats();
    refreshLogs();
    if (S.autoRefreshTimer) clearInterval(S.autoRefreshTimer);
    S.autoRefreshTimer = setInterval(() => {
      refreshStats();
      refreshLogs();
    }, 5000);
  }
  if (name === "tasks") {
    if (S.autoRefreshTimer) {
      clearInterval(S.autoRefreshTimer);
      S.autoRefreshTimer = null;
    }
    loadTasks();
  }
}

// ─────────────────── DROPDOWN MENU ───────────────────
function toggleMenu() {
  const el = document.getElementById("menuDropdown");
  el.style.display = el.style.display === "none" ? "block" : "none";
}
document.addEventListener("click", (e) => {
  const dd = document.getElementById("menuDropdown");
  if (
    dd &&
    !dd.contains(e.target) &&
    !e.target.closest("#menuDropdown") &&
    e.target.textContent.indexOf("菜单") === -1
  ) {
    dd.style.display = "none";
  }
});

function downloadLog() {
  window.open("/api/logs/download", "_blank");
}

// ─────────────────── MODAL HELPERS ───────────────────
function openModal(id) {
  document.getElementById(id).style.display = "flex";
}
function closeModal(id) {
  document.getElementById(id).style.display = "none";
}

// ─────────────────── TOGGLE GROUPS ───────────────────
// toggleId: id of .tg element, idx: button index, value: expected text
function setToggle(toggleId, idx, value) {
  const tg = document.getElementById(toggleId);
  if (!tg) return;
  tg.querySelectorAll("button").forEach((b, i) =>
    b.classList.toggle("active", i === idx),
  );
}

function getToggleValue(toggleId) {
  const tg = document.getElementById(toggleId);
  if (!tg) return null;
  const active = tg.querySelector("button.active");
  return active ? active.textContent.trim() : null;
}

// ─────────────────── TASKS ───────────────────
let searchDebounce = null;
function debouncedSearch() {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => {
    S.page = 1;
    loadTasks();
  }, 300);
}

function changePageSize() {
  S.pageSize = parseInt(document.getElementById("pageSizeSelect").value, 10);
  S.page = 1;
  loadTasks();
}

function changeTaskSort() {
  const sortByEl = document.getElementById("sortBySelect");
  const sortOrderEl = document.getElementById("sortOrderSelect");
  if (sortByEl) S.sortBy = sortByEl.value || "processed_at";
  if (sortOrderEl) S.sortOrder = sortOrderEl.value || "desc";
  S.page = 1;
  loadTasks();
}

async function loadTasks() {
  const text = document.getElementById("searchText").value;
  const status = document.getElementById("filterStatus").value;
  const sortByEl = document.getElementById("sortBySelect");
  const sortOrderEl = document.getElementById("sortOrderSelect");

  if (sortByEl && sortByEl.value) S.sortBy = sortByEl.value;
  if (sortOrderEl && sortOrderEl.value) S.sortOrder = sortOrderEl.value;

  const qs = new URLSearchParams({
    text,
    status,
    sort_by: S.sortBy,
    sort_order: S.sortOrder,
    page: S.page,
    page_size: S.pageSize,
  });

  const { ok, data } = await GET(`/api/tasks?${qs}`);
  if (!ok) {
    notify("加载任务失败", "negative");
    return;
  }

  S.totalItems = data.total;
  if (data.sort_by) S.sortBy = data.sort_by;
  if (data.sort_order) S.sortOrder = data.sort_order;

  if (sortByEl) sortByEl.value = S.sortBy;
  if (sortOrderEl) sortOrderEl.value = S.sortOrder;

  renderTaskTable(data.items || []);
  renderPagination(data.total, data.page, data.page_size);
  document.getElementById("totalCount").textContent = `共 ${data.total} 条`;
  updateSelectionLabel();
}

function badgeForStatus(s) {
  const map = {
    success: "成功",
    failed: "失败",
    pending: "等待中",
    processing: "处理中",
    ignored: "忽略",
  };
  return `<span class="badge badge-${s}">${map[s] || s}</span>`;
}

function renderTaskTable(items) {
  const tbody = document.getElementById("taskTableBody");
  if (!items || items.length === 0) {
    tbody.innerHTML =
      '<tr><td colspan="7" style="text-align:center;color:#9ca3af;padding:32px;">暂无任务</td></tr>';
    return;
  }
  tbody.innerHTML = items
    .map((t, i) => {
      const chk = S.selected.has(t.uuid) ? "checked" : "";

      // Resolve target path: new records use target_paths[], Python records use target_path string
      const targetPath =
        t.target_paths && t.target_paths.length > 0
          ? t.target_paths[0]
          : t.target_path || "";

      const cellStyle =
        "font-size:11px;max-width:none;overflow:visible;text-overflow:initial;white-space:normal;word-break:break-all;line-height:1.5;";

      // Name row
      const nameRow = `<div style="font-weight:600;font-size:12px;max-width:none;overflow:visible;text-overflow:initial;white-space:normal;word-break:break-all;line-height:1.5;" title="${escHtml(t.name || "")}">${escHtml(t.name || "—")}</div>`;

      // Source path row (迁移前)
      const srcRow = `<div style="${cellStyle}color:#6b7280;" title="${escHtml(t.path)}">📂 ${escHtml(t.path)}</div>`;

      // Target path row (迁移后) — only shown when available
      const tgtRow = targetPath
        ? `<div style="${cellStyle}color:#16a34a;" title="${escHtml(targetPath)}">✅ ${escHtml(targetPath)}</div>`
        : "";

      // Error row
      const errRow =
        t.status === "failed" && t.error
          ? `<div style="${cellStyle}color:#dc2626;" title="${escHtml(t.error)}">❌ ${escHtml(t.error)}</div>`
          : "";

      return `<tr>
      <td><input type="checkbox" class="chk row-chk" data-uuid="${t.uuid}" data-index="${i}" ${chk} onclick="onRowCheck(this, event)"/></td>
      <td>${nameRow}${srcRow}${tgtRow}${errRow}</td>
      <td>${badgeForStatus(t.status)}</td>
      <td>${t.season_id != null ? t.season_id : "—"}</td>
      <td style="font-size:11px;color:#6b7280;">${escHtml(t.tmdb_id || "—")}</td>
      <td style="font-size:11px;color:#9ca3af;">${t.processed_at || "—"}</td>
      <td>
        <button class="btn-outline" style="padding:3px 8px;font-size:11px;" onclick="openEdit('${t.uuid}')">✏️</button>
        <button class="btn-outline" style="padding:3px 8px;font-size:11px;" onclick="retrySingle('${t.uuid}')">🔁</button>
        <button class="btn-outline" style="padding:3px 8px;font-size:11px;color:#dc2626;border-color:#dc2626;" onclick="deleteSingle('${t.uuid}')">🗑</button>
      </td>
    </tr>`;
    })
    .join("");
}

function renderPagination(total, page, pageSize) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const bar = document.getElementById("paginationBar");
  let html = `<button ${page <= 1 ? "disabled" : ""} onclick="goPage(${page - 1})">‹</button>`;
  const start = Math.max(1, page - 2);
  const end = Math.min(totalPages, page + 2);
  if (start > 1)
    html += `<button onclick="goPage(1)">1</button>${start > 2 ? "<span>…</span>" : ""}`;
  for (let i = start; i <= end; i++) {
    html += `<button class="${i === page ? "active" : ""}" onclick="goPage(${i})">${i}</button>`;
  }
  if (end < totalPages)
    html += `${end < totalPages - 1 ? "<span>…</span>" : ""}<button onclick="goPage(${totalPages})">${totalPages}</button>`;
  html += `<button ${page >= totalPages ? "disabled" : ""} onclick="goPage(${page + 1})">›</button>`;
  bar.innerHTML = html;
}

function goPage(p) {
  S.page = p;
  loadTasks();
}

function onRowCheck(el, evt) {
  const uuid = el.dataset.uuid;
  const idx = parseInt(el.dataset.index || "-1", 10);
  if (evt && evt.shiftKey && S._lastCheckedIndex != null && idx >= 0) {
    const start = Math.min(S._lastCheckedIndex, idx);
    const end = Math.max(S._lastCheckedIndex, idx);
    const state = el.checked;
    document.querySelectorAll(".row-chk").forEach((c) => {
      const ci = parseInt(c.dataset.index || "-1", 10);
      if (ci >= start && ci <= end) {
        c.checked = state;
        const id = c.dataset.uuid;
        if (state) S.selected.add(id);
        else S.selected.delete(id);
      }
    });
  } else {
    if (el.checked) S.selected.add(uuid);
    else S.selected.delete(uuid);
  }
  if (idx >= 0) S._lastCheckedIndex = idx;
  updateSelectionLabel();
}

function toggleAll(el) {
  document.querySelectorAll(".row-chk").forEach((c) => {
    c.checked = el.checked;
    const uuid = c.dataset.uuid;
    if (el.checked) S.selected.add(uuid);
    else S.selected.delete(uuid);
  });
  updateSelectionLabel();
}

function updateSelectionLabel() {
  const el = document.getElementById("selectedCount");
  if (el)
    el.textContent = S.selected.size > 0 ? `已选 ${S.selected.size} 条` : "";
  if (S.selected.size === 0) {
    const ca = document.getElementById("checkAll");
    if (ca) ca.checked = false;
    document.querySelectorAll(".row-chk").forEach((c) => (c.checked = false));
  }
}

// ─────────────────── EDIT TASK ───────────────────
async function openEdit(uuid) {
  const { ok, data } = await GET(`/api/tasks/${uuid}`);
  if (!ok) {
    notify("加载任务失败", "negative");
    return;
  }

  document.getElementById("editUUID").value = uuid;
  document.getElementById("editPath").value = data.path || "";
  document.getElementById("editName").value = data.name || "";
  document.getElementById("editSeason").value =
    data.season_id != null ? data.season_id : "";
  document.getElementById("editOffset").value = data.episode_offset || 0;
  document.getElementById("editTMDBID").value = data.tmdb_id || "";

  // is_anime toggle
  const animeVal =
    data.is_anime === true ? "是" : data.is_anime === false ? "否" : "自动";
  setToggleByValue("tg_editAnime", animeVal);

  // is_movie toggle
  const movieVal =
    data.is_movie === true ? "是" : data.is_movie === false ? "否" : "自动";
  setToggleByValue("tg_editMovie", movieVal);

  // use_ai toggle
  setToggleByValue("tg_editAI", data.use_ai ? "启用" : "禁用");

  openModal("editTaskModal");
}

function setToggleByValue(toggleId, value) {
  const tg = document.getElementById(toggleId);
  if (!tg) return;
  tg.querySelectorAll("button").forEach((b) =>
    b.classList.toggle("active", b.textContent.trim() === value),
  );
}

async function submitEdit() {
  const uuid = document.getElementById("editUUID").value;
  const animeVal = getToggleValue("tg_editAnime");
  const movieVal = getToggleValue("tg_editMovie");
  const aiVal = getToggleValue("tg_editAI");

  const toTriBool = (v) => (v === "是" ? true : v === "否" ? false : null);

  const seasonRaw = document.getElementById("editSeason").value;
  const offsetRaw = document.getElementById("editOffset").value;
  const tmdbID = document.getElementById("editTMDBID").value.trim();
  const name = document.getElementById("editName").value.trim();
  const path = document.getElementById("editPath").value;

  const body = {
    uuid,
    path,
    name: name || null,
    is_anime: toTriBool(animeVal),
    is_movie: toTriBool(movieVal),
    use_ai: aiVal === "启用",
    season_id: seasonRaw !== "" ? parseInt(seasonRaw, 10) : null,
    episode_offset: offsetRaw !== "" ? parseInt(offsetRaw, 10) : 0,
    tmdb_id: tmdbID,
    status: "pending",
  };

  const { ok } = await PUT(`/api/tasks/${uuid}`, body);
  if (!ok) {
    notify("保存失败", "negative");
    return;
  }

  // Re-queue for processing
  const { ok: ok2 } = await POST("/api/tasks/retry", {
    uuids: [uuid],
    settings: {},
  });
  closeModal("editTaskModal");
  notify(
    ok2 ? "已保存并重新加入队列" : "保存成功，重试失败",
    ok2 ? "positive" : "warning",
  );
  setTimeout(loadTasks, 600);
}

// ─────────────────── SINGLE TASK ACTIONS ───────────────────
async function retrySingle(uuid) {
  const doCleanup = confirm("重试前是否清理已转移目标文件和空目录？");
  const settings = doCleanup
    ? { delete_transferred: true, cleanup_dirs: true }
    : {};
  const { ok } = await POST("/api/tasks/retry", {
    uuids: [uuid],
    settings,
  });
  notify(ok ? "已加入重试队列" : "重试提交失败", ok ? "positive" : "negative");
  if (ok) setTimeout(loadTasks, 800);
}

async function deleteSingle(uuid) {
  openDeleteModal([uuid]);
}

// ─────────────────── BATCH OPERATIONS ───────────────────
function openBatchRetry() {
  if (S.selected.size === 0) {
    notify("请先选择任务", "warning");
    return;
  }
  document.getElementById("batchRetryCount").textContent =
    `已选 ${S.selected.size} 个任务`;
  // Reset toggles
  ["tg_bAnime", "tg_bMovie", "tg_bAI"].forEach((id) =>
    setToggle(id, 0, "保持原样"),
  );
  ["bRetryTMDBID", "bRetrySeasonID", "bRetryOffset"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  openModal("batchRetryModal");
}

async function submitBatchRetry() {
  const settings = {};
  const animeV = getToggleValue("tg_bAnime");
  const movieV = getToggleValue("tg_bMovie");
  const aiV = getToggleValue("tg_bAI");
  if (animeV && animeV !== "保持原样") settings.is_anime = animeV;
  if (movieV && movieV !== "保持原样") settings.is_movie = movieV;
  if (aiV && aiV !== "保持原样") settings.use_ai = aiV;

  const customName = document.getElementById("bRetryName")?.value.trim();
  const tmdbID = document.getElementById("bRetryTMDBID")?.value.trim();
  const seasonID = document.getElementById("bRetrySeasonID")?.value.trim();
  const offset = document.getElementById("bRetryOffset")?.value.trim();
  if (customName) settings.name = customName;
  if (tmdbID) settings.tmdb_id = tmdbID;
  if (seasonID) settings.season_id = seasonID;
  if (offset) settings.episode_offset = offset;

  const delTarget = document.getElementById("bRetryDelTarget")?.checked;
  const cleanDirs = document.getElementById("bRetryCleanupDirs")?.checked;
  if (delTarget) settings.delete_transferred = true;
  if (cleanDirs) settings.cleanup_dirs = true;

  const { ok, data } = await POST("/api/tasks/retry", {
    uuids: [...S.selected],
    settings,
  });
  closeModal("batchRetryModal");
  notify(
    ok ? `已将 ${data.queued || 0} 个任务加入队列` : "批量重试失败",
    ok ? "positive" : "negative",
  );
  if (ok) {
    S.selected.clear();
    updateSelectionLabel();
    setTimeout(loadTasks, 800);
  }
}

async function batchDelete() {
  if (S.selected.size === 0) {
    notify("请先选择任务", "warning");
    return;
  }
  openDeleteModal([...S.selected]);
}

function openDeleteModal(uuids) {
  S._deleteTargets = uuids || [];
  document.getElementById("deleteTaskCount").textContent =
    `将删除 ${S._deleteTargets.length} 条任务记录`;
  document.getElementById("delTargetFiles").checked = false;
  document.getElementById("delSourceFiles").checked = false;
  document.getElementById("delCleanupDirs").checked = false;
  openModal("deleteTaskModal");
}

async function confirmDeleteTasks() {
  const uuids = S._deleteTargets || [];
  if (uuids.length === 0) {
    closeModal("deleteTaskModal");
    return;
  }
  const deleteFiles = document.getElementById("delTargetFiles").checked;
  const deleteSource = document.getElementById("delSourceFiles").checked;
  const cleanupDirs = document.getElementById("delCleanupDirs").checked;
  const { ok, data } = await POST("/api/tasks/delete", {
    uuids,
    delete_files: deleteFiles,
    delete_source: deleteSource,
    cleanup_dirs: cleanupDirs,
  });
  closeModal("deleteTaskModal");
  notify(
    ok ? `已删除 ${data.deleted || 0} 条` : "删除失败",
    ok ? "positive" : "negative",
  );
  if (ok) {
    uuids.forEach((u) => S.selected.delete(u));
    updateSelectionLabel();
    loadTasks();
  }
}

// ─────────────────── ADD TASK ───────────────────
function openAddTask() {
  S.addStep = 1;
  S.browseSel = [];
  document.getElementById("addStep1").style.display = "block";
  document.getElementById("addStep2").style.display = "none";
  document.getElementById("addNextBtn").textContent = "下一步";
  document.getElementById("selectedPathsBox").textContent = "";
  document.getElementById("excludeKeywords").value = "";
  ["ov_tvDir", "ov_movieDir", "ov_tvFormat", "ov_movieFormat"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  openModal("addTaskModal");
  browseTo("");
}

async function browseTo(path) {
  const qs = path ? `?path=${encodeURIComponent(path)}` : "";
  const { ok, data } = await GET(`/api/files${qs}`);
  if (!ok) {
    notify("无法列出目录", "negative");
    return;
  }

  S.browsePath = data.current;
  document.getElementById("browsePathInput").value = data.current;

  const list = document.getElementById("fileList");
  list.innerHTML = (data.entries || [])
    .map((e) => {
      const icon = e.is_dir ? "📁" : "📄";
      const selClass = S.browseSel.includes(e.path) ? "sel" : "";
      return `<div class="fentry ${selClass}" data-path="${e.path}" data-isdir="${e.is_dir}" onclick="onFileEntryClick(this)">
      <span>${icon}</span><span>${e.name}</span>
    </div>`;
    })
    .join("");
}

function onFileEntryClick(el) {
  const path = el.dataset.path;
  const isDir = el.dataset.isdir === "true";
  if (isDir && el.dataset.name === "..") {
    browseTo(path);
    return;
  }

  // Double-click to navigate into directory
  if (isDir) {
    if (el._clickTimer) {
      clearTimeout(el._clickTimer);
      el._clickTimer = null;
      browseTo(path);
      return;
    }
    el._clickTimer = setTimeout(() => {
      el._clickTimer = null;
      toggleBrowseSel(el, path);
    }, 250);
  } else {
    toggleBrowseSel(el, path);
  }
}

function toggleBrowseSel(el, path) {
  const idx = S.browseSel.indexOf(path);
  if (idx >= 0) {
    S.browseSel.splice(idx, 1);
    el.classList.remove("sel");
  } else {
    S.browseSel.push(path);
    el.classList.add("sel");
  }
  updateSelectedPathsBox();
}

function updateSelectedPathsBox() {
  const box = document.getElementById("selectedPathsBox");
  box.textContent =
    S.browseSel.length > 0
      ? `已选 ${S.browseSel.length} 项: ${S.browseSel.map((p) => p.split("/").pop()).join(", ")}`
      : "";
}

function navToParent() {
  const parts = S.browsePath.split("/");
  parts.pop();
  browseTo(parts.join("/") || "/");
}

function addTaskNext() {
  if (S.addStep === 1) {
    if (S.browseSel.length === 0) {
      // Use current directory if nothing selected
      S.browseSel = [S.browsePath];
    }
    S.addStep = 2;
    document.getElementById("addStep1").style.display = "none";
    document.getElementById("addStep2").style.display = "block";
    document.getElementById("addNextBtn").textContent = "确认提交";
  } else {
    submitAddTask();
  }
}

async function submitAddTask() {
  const isAnimeVal = getToggleValue("tg_isAnime");
  const isAnime = isAnimeVal === "是" ? true : false;

  const overrides = {};
  const ovTvDir = document.getElementById("ov_tvDir")?.value.trim();
  const ovMovieDir = document.getElementById("ov_movieDir")?.value.trim();
  const ovTvFmt = document.getElementById("ov_tvFormat")?.value.trim();
  const ovMovieFmt = document.getElementById("ov_movieFormat")?.value.trim();
  const ovMode = document.getElementById("ov_mode")?.value;
  const ovOw = document.getElementById("ov_overwrite")?.value;

  if (ovTvDir) overrides.target_tv_dir = ovTvDir;
  if (ovMovieDir) overrides.target_movie_dir = ovMovieDir;
  if (ovTvFmt) overrides.tv_rename_format = ovTvFmt;
  if (ovMovieFmt) overrides.movie_rename_format = ovMovieFmt;
  if (ovMode) overrides.mode = ovMode;
  if (ovOw) overrides.overwrite_mode = ovOw;

  const body = {
    paths: S.browseSel,
    is_anime: isAnime,
    exclude_keywords: document.getElementById("excludeKeywords").value,
    overrides,
  };

  const { ok, data } = await POST("/api/submit", body);
  closeModal("addTaskModal");
  if (ok) {
    notify(
      `已将 ${data.added} 个文件加入队列${data.ignored > 0 ? `（忽略 ${data.ignored} 个）` : ""}`,
      "positive",
    );
    setTimeout(loadTasks, 1000);
  } else {
    notify("提交失败: " + (data?.error || "未知错误"), "negative");
  }
}

// ─────────────────── CONFIG ───────────────────
const CONFIG_LABELS = {
  api_key: "🔑 TMDB API Key",
  bangumi_path: "📁 电视剧路径",
  movie_path: "🎬 电影路径",
  anime_path: "🎌 动漫路径",
  anime_movie_path: "🎌 动漫电影路径",
  tv_rename_format: "📄 TV 重命名模板",
  movie_rename_format: "📄 Movie 重命名模板",
  mode: "💿 重命名模式",
  overwrite_mode: "💥 覆盖模式",
  scrape_metadata: "📥 刮削元数据",
  scrape_image_types: "🖼 刮削图片类型",
  scrape_language: "🌐 刮削首选语言",
  subtitle_extensions: "💬 字幕扩展名",
  secondary_classification: "📂 二级分类",
  secondary_rules: "🎨 二级分类规则",
  docker_mnt: "🐳 Docker挂载路径",
  log_level: "📝 日志等级",
  ai_provider: "🤖 AI提供商",
  ai_api_key: "🔑 OpenAI API Key",
  ai_base_url: "🌐 OpenAI Base URL",
  ai_model: "🧠 OpenAI 模型",
  ai_temperature: "🌡 OpenAI Temperature",
  ai_rate_limit_rpm: "⏱ AI 限速 RPM",
  ai_rate_limit_tpm: "🪙 AI 限速 TPM",
  gemini_api_key: "💎 Gemini API Key",
  gemini_base_url: "🌐 Gemini Base URL",
  gemini_model: "💎 Gemini 模型",
  gemini_temperature: "🌡 Gemini Temperature",
  ai_enabled: "🚀 启用 AI",
  ai_confidence_threshold: "📊 AI 置信度阈值",
  openai_output_format: "🎯 OpenAI 输出格式",
  ai_auto_save: "💾 自动保存 AI 分析",
  title_languages: "🌐 标题首选语言",
  overview_languages: "📝 简介首选语言",
  monitor_enabled: "👁 启用监控",
  monitor_mode: "⚙ 监控模式",
  monitor_paths: "📁 监控目录",
  monitor_exclude_dirs: "🚫 监控排除目录",
};

const CONFIG_SELECT_OPTIONS = {
  mode: ["硬链接", "软链接", "复制", "剪切"],
  overwrite_mode: ["从不覆盖", "总是覆盖", "保留最新"],
  log_level: ["DEBUG", "INFO", "WARNING", "ERROR"],
  ai_provider: ["openai", "gemini"],
  ai_confidence_threshold: ["High", "Medium", "Low"],
  openai_output_format: ["function_calling", "json_object"],
  monitor_mode: ["compatibility", "native"],
  scrape_language: ["zh-CN", "zh-TW", "en-US", "ja-JP", "ko-KR"],
};

const CONFIG_BOOL_KEYS = new Set([
  "scrape_metadata",
  "secondary_classification",
  "ai_enabled",
  "ai_auto_save",
  "monitor_enabled",
]);
const CONFIG_JSON_KEYS = new Set(["exclude_dirs"]);
const CONFIG_HIDDEN_KEYS = new Set([]);

// Keys that get a friendly UI instead of raw JSON textareas
const CONFIG_FRIENDLY_KEYS = new Set([
  "scrape_image_types",
  "subtitle_extensions",
  "secondary_rules",
  "title_languages",
  "overview_languages",
  "monitor_paths",
  "monitor_exclude_dirs",
]);

// Predefined options for checkbox-style friendly keys
const SCRAPE_IMAGE_OPTIONS = [
  "poster",
  "backdrop",
  "logo",
  "banner",
  "thumb",
  "art",
  "clearlogo",
  "landscape",
];
const SUBTITLE_EXT_OPTIONS = [".ass", ".srt", ".sub", ".ssa", ".vtt"];
const MONITOR_RECOGNITION_OPTIONS = [
  { value: "smart", label: "智能平衡", desc: "默认，文件名和目录名一起判断" },
  { value: "standard", label: "标准命名", desc: "优先相信文件名，适合命名规范目录" },
  { value: "directory_first", label: "目录优先", desc: "优先相信上级目录，适合文件名很乱的目录" },
];

async function openConfig() {
  const { ok, data } = await GET("/api/config");
  if (!ok) {
    notify("加载配置失败", "negative");
    return;
  }
  S.configData = data;
  renderConfigForm(data);
  openModal("configModal");
}

function renderConfigForm(cfg) {
  const form = document.getElementById("configForm");
  const sections = [
    {
      label: "基础配置",
      keys: [
        "api_key",
        "bangumi_path",
        "movie_path",
        "anime_path",
        "anime_movie_path",
        "docker_mnt",
      ],
    },
    {
      label: "重命名设置",
      keys: [
        "tv_rename_format",
        "movie_rename_format",
        "mode",
        "overwrite_mode",
      ],
    },
    {
      label: "元数据",
      keys: [
        "scrape_metadata",
        "scrape_language",
        "scrape_image_types",
        "subtitle_extensions",
        "secondary_classification",
        "secondary_rules",
      ],
    },
    { label: "日志", keys: ["log_level"] },
    {
      label: "AI 配置",
      keys: [
        "ai_enabled",
        "ai_provider",
        "ai_api_key",
        "ai_base_url",
        "ai_model",
        "ai_temperature",
        "ai_rate_limit_rpm",
        "ai_rate_limit_tpm",
        "ai_confidence_threshold",
        "openai_output_format",
        "ai_auto_save",
      ],
    },
    {
      label: "Gemini 配置",
      keys: [
        "gemini_api_key",
        "gemini_base_url",
        "gemini_model",
        "gemini_temperature",
      ],
    },
    {
      label: "语言偏好",
      keys: ["title_languages", "overview_languages"],
    },
    {
      label: "监控配置",
      keys: [
        "monitor_enabled",
        "monitor_mode",
        "monitor_paths",
        "monitor_exclude_dirs",
      ],
    },
  ];

  let html = "";
  sections.forEach((sec) => {
    html += `<div class="section-label" style="margin-top:16px;">${sec.label}</div>`;
    sec.keys.forEach((key) => {
      if (CONFIG_HIDDEN_KEYS.has(key)) return;
      const label = CONFIG_LABELS[key] || key;
      const val = cfg[key];
      let input = "";

      if (CONFIG_FRIENDLY_KEYS.has(key)) {
        input = renderFriendlyConfig(key, val);
      } else if (CONFIG_BOOL_KEYS.has(key)) {
        const checked = val ? "checked" : "";
        input = `<label style="display:flex;align-items:center;gap:8px;cursor:pointer;"><input type="checkbox" class="chk" id="cfg_${key}" ${checked}/><span style="font-size:13px;">启用</span></label>`;
      } else if (CONFIG_SELECT_OPTIONS[key]) {
        const opts = CONFIG_SELECT_OPTIONS[key]
          .map((o) => `<option ${o === val ? "selected" : ""}>${o}</option>`)
          .join("");
        input = `<select id="cfg_${key}">${opts}</select>`;
      } else if (CONFIG_JSON_KEYS.has(key)) {
        const jsonStr =
          val == null
            ? "[]"
            : typeof val === "string"
              ? val
              : JSON.stringify(val, null, 2);
        input = `<textarea id="cfg_${key}" rows="2" style="resize:vertical;font-family:monospace;font-size:11px;">${escHtml(jsonStr)}</textarea>`;
      } else if (
        key.includes("password") ||
        key.includes("api_key") ||
        key.includes("api_secret")
      ) {
        input = `<input type="password" id="cfg_${key}" value="${escHtml(String(val ?? ""))}"/>`;
      } else if (key.includes("temperature")) {
        input = `<input type="number" id="cfg_${key}" value="${val ?? 0}" step="0.1" min="0" max="2" style="max-width:100px;"/>`;
      } else if (key.includes("rate_limit")) {
        const placeholder =
          key === "ai_rate_limit_rpm" ? "0=不限制，如 800" : "0=不限制，如 40000";
        input = `<input type="number" id="cfg_${key}" value="${val ?? 0}" min="0" step="1" placeholder="${placeholder}" style="max-width:160px;"/>`;
      } else {
        input = `<input type="text" id="cfg_${key}" value="${escHtml(String(val ?? ""))}"/>`;
      }

      html += `<div class="cfg-row"><span class="cfg-label">${label}</span><div class="cfg-val">${input}</div></div>`;
    });
  });
  form.innerHTML = html;
}

// ─────────────────── FRIENDLY CONFIG RENDERERS ───────────────────

function renderFriendlyConfig(key, val) {
  if (key === "scrape_image_types")
    return renderCheckboxList(key, SCRAPE_IMAGE_OPTIONS, toStringArray(val));
  if (key === "subtitle_extensions")
    return renderCheckboxList(key, SUBTITLE_EXT_OPTIONS, toStringArray(val));
  if (key === "secondary_rules") return renderSecondaryRules(val);
  if (key === "title_languages")
    return renderMultilineTextEditor(
      key,
      toStringArray(val),
      "一行一个语言代码，按优先级排序，例如 zh-CN\nen-US",
    );
  if (key === "overview_languages")
    return renderMultilineTextEditor(
      key,
      toStringArray(val),
      "一行一个语言代码，按优先级排序，例如 zh-CN\nen-US",
    );
  if (key === "monitor_paths") return renderMonitorPaths(val);
  if (key === "monitor_exclude_dirs")
    return renderMultilineTextEditor(
      key,
      toStringArray(val),
      "一行一个排除目录关键词，支持正则，例如 C\\+\\+",
    );
  return `<textarea id="cfg_${key}" rows="6">${escHtml(
    JSON.stringify(val ?? [], null, 2),
  )}</textarea>`;
}

function toStringArray(val) {
  if (!val) return [];
  if (Array.isArray(val))
    return val.map((v) =>
      typeof v === "string" ? v : v.path || JSON.stringify(v),
    );
  if (typeof val === "string") {
    try {
      const p = JSON.parse(val);
      return Array.isArray(p)
        ? p.map((v) => (typeof v === "string" ? v : v.path || ""))
        : [];
    } catch {
      return [];
    }
  }
  return [];
}

function renderCheckboxList(key, options, selected) {
  const items = options
    .map((opt) => {
      const checked = selected.includes(opt) ? "checked" : "";
      return `<label style="display:inline-flex;align-items:center;gap:4px;margin-right:12px;cursor:pointer;font-size:13px;">
      <input type="checkbox" class="chk friendly-chk" data-cfg-key="${key}" value="${escHtml(opt)}" ${checked}/>${escHtml(opt)}
    </label>`;
    })
    .join("");
  return `<div id="cfg_${key}" style="display:flex;flex-wrap:wrap;gap:4px 0;">${items}</div>`;
}

function renderMonitorPaths(val) {
  // Parse monitor_paths: can be [{path, is_anime?, ...}] or ["str"] or JSON string
  let paths = [];
  const raw = val || [];
  const arr = Array.isArray(raw)
    ? raw
    : (() => {
        try {
          return JSON.parse(raw);
        } catch {
          return [];
        }
      })();
  for (const item of arr) {
    if (typeof item === "string") {
      paths.push({ path: item, is_anime: null, is_movie: null });
    } else if (item && typeof item === "object") {
      paths.push({
        path: item.path || "",
        is_anime:
          item.is_anime === true
            ? true
            : item.is_anime === false
              ? false
              : null,
        is_movie:
          item.is_movie === true
            ? true
            : item.is_movie === false
              ? false
              : null,
        tv_rename_format: item.tv_rename_format || "",
        movie_rename_format: item.movie_rename_format || "",
        mode: item.mode || "",
        overwrite_mode: item.overwrite_mode || "",
        monitor_mode: item.monitor_mode || "",
        recognition_mode: item.recognition_mode || "smart",
        scrape_language: item.scrape_language || "",
        scan_now: item.scan_now === true,
      });
    }
  }

  // Store in global state for dynamic add/remove
  S._monitorPaths = paths;

  return `<div id="cfg_monitor_paths">
    <div id="monitorPathsList">${renderMonitorPathItems(paths)}</div>
    <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;">
      <input type="text" id="newMonitorPathInput" placeholder="输入目录路径，如 /media/downloads" style="flex:1;min-width:280px;"/>
      <select id="newMonitorPathAnime" style="width:auto;">
        <option value="">自动</option>
        <option value="true">是动漫</option>
        <option value="false">非动漫</option>
      </select>
      <select id="newMonitorPathMode" style="width:auto;">
        <option value="">继承全局监控模式</option>
        <option value="compatibility">compatibility</option>
        <option value="native">native</option>
      </select>
      <select id="newMonitorPathRecognition" style="width:auto;">
        ${MONITOR_RECOGNITION_OPTIONS.map(
          (opt) => `<option value="${opt.value}" ${opt.value === "smart" ? "selected" : ""}>${opt.label}</option>`,
        ).join("")}
      </select>
      <button class="btn-outline" style="padding:4px 12px;font-size:12px;white-space:nowrap;" onclick="addMonitorPath()">+ 添加</button>
    </div>
  </div>`;
}

function renderMonitorPathItems(paths) {
  if (!paths || paths.length === 0) {
    return '<div style="color:#9ca3af;font-size:12px;padding:8px 0;">暂未配置监控目录</div>';
  }
  return paths
    .map((p, i) => {
      const animeLabel =
        p.is_anime === true ? "动漫" : p.is_anime === false ? "非动漫" : "自动";
      const animeBadge =
        p.is_anime === true
          ? '<span style="background:#dbeafe;color:#2563eb;padding:1px 6px;border-radius:9999px;font-size:10px;margin-left:6px;">动漫</span>'
          : p.is_anime === false
            ? '<span style="background:#f3f4f6;color:#6b7280;padding:1px 6px;border-radius:9999px;font-size:10px;margin-left:6px;">非动漫</span>'
            : "";
      const movieBadge =
        p.is_movie === true
          ? '<span style="background:#fee2e2;color:#b91c1c;padding:1px 6px;border-radius:9999px;font-size:10px;margin-left:6px;">电影</span>'
          : p.is_movie === false
            ? '<span style="background:#e0f2fe;color:#0369a1;padding:1px 6px;border-radius:9999px;font-size:10px;margin-left:6px;">电视剧</span>'
            : "";
      const tvBadge = p.tv_rename_format
        ? '<span style="background:#ecfccb;color:#4d7c0f;padding:1px 6px;border-radius:9999px;font-size:10px;margin-left:6px;">TV 模板</span>'
        : "";
      const movieTplBadge = p.movie_rename_format
        ? '<span style="background:#fee2e2;color:#b91c1c;padding:1px 6px;border-radius:9999px;font-size:10px;margin-left:6px;">Movie 模板</span>'
        : "";
      const monitorModeBadge = p.monitor_mode
        ? `<span style="background:#ede9fe;color:#6d28d9;padding:1px 6px;border-radius:9999px;font-size:10px;margin-left:6px;">${escHtml(p.monitor_mode)}</span>`
        : "";
      const recognitionLabels = {
        smart: "智能平衡",
        standard: "标准命名",
        directory_first: "目录优先",
      };
      const recognitionBadge =
        p.recognition_mode && p.recognition_mode !== "smart"
          ? `<span style="background:#dcfce7;color:#166534;padding:1px 6px;border-radius:9999px;font-size:10px;margin-left:6px;">${escHtml(recognitionLabels[p.recognition_mode] || p.recognition_mode)}</span>`
          : "";
      const scrapeLangBadge = p.scrape_language
        ? `<span style="background:#fef3c7;color:#92400e;padding:1px 6px;border-radius:9999px;font-size:10px;margin-left:6px;">🌐 ${escHtml(p.scrape_language)}</span>`
        : "";
      return `<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;background:#f9fafb;border-radius:8px;margin-bottom:4px;">
      <span style="font-size:13px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escHtml(p.path)}">📁 ${escHtml(p.path)}</span>
      ${animeBadge}${movieBadge}${tvBadge}${movieTplBadge}${monitorModeBadge}${recognitionBadge}${scrapeLangBadge}
      <button class="btn-outline" style="padding:2px 8px;font-size:11px;flex-shrink:0;" onclick="openMonitorPathEditor(${i})">配置</button>
      <button class="btn-outline" style="padding:2px 8px;font-size:11px;color:#dc2626;border-color:#dc2626;flex-shrink:0;" onclick="removeMonitorPath(${i})">✕</button>
    </div>`;
    })
    .join("");
}

function addMonitorPath() {
  const input = document.getElementById("newMonitorPathInput");
  const animeSelect = document.getElementById("newMonitorPathAnime");
  const modeSelect = document.getElementById("newMonitorPathMode");
  const recognitionSelect = document.getElementById("newMonitorPathRecognition");
  const path = input.value.trim();
  if (!path) {
    notify("请输入目录路径", "warning");
    return;
  }

  const animeVal = animeSelect.value;
  const entry = {
    path,
    is_anime: animeVal === "true" ? true : animeVal === "false" ? false : null,
    is_movie: null,
    tv_rename_format: "",
    movie_rename_format: "",
    mode: "",
    overwrite_mode: "",
    monitor_mode: modeSelect.value || "",
    recognition_mode: recognitionSelect.value || "smart",
    scrape_language: "",
    scan_now: false,
  };
  S._monitorPaths.push(entry);

  document.getElementById("monitorPathsList").innerHTML =
    renderMonitorPathItems(S._monitorPaths);
  input.value = "";
  animeSelect.value = "";
  modeSelect.value = "";
  recognitionSelect.value = "smart";
}

function removeMonitorPath(idx) {
  S._monitorPaths.splice(idx, 1);
  document.getElementById("monitorPathsList").innerHTML =
    renderMonitorPathItems(S._monitorPaths);
}

function openMonitorPathEditor(idx) {
  const p = (S._monitorPaths || [])[idx];
  if (!p) return;
  S._monitorPathEditIndex = idx;
  document.getElementById("monitorPathEditPath").value = p.path || "";
  document.getElementById("monitorPathEditAnime").value =
    p.is_anime === true ? "true" : p.is_anime === false ? "false" : "";
  document.getElementById("monitorPathEditMovie").value =
    p.is_movie === true ? "true" : p.is_movie === false ? "false" : "";
  document.getElementById("monitorPathTvTemplate").value =
    p.tv_rename_format || "";
  document.getElementById("monitorPathMovieTemplate").value =
    p.movie_rename_format || "";
  document.getElementById("monitorPathMode").value = p.mode || "";
  document.getElementById("monitorPathOverwrite").value =
    p.overwrite_mode || "";
  document.getElementById("monitorPathMonitorMode").value =
    p.monitor_mode || "";
  document.getElementById("monitorPathRecognitionMode").value =
    p.recognition_mode || "smart";
  document.getElementById("monitorPathScrapeLanguage").value =
    p.scrape_language || "";
  document.getElementById("monitorPathScanNow").checked = p.scan_now === true;
  openModal("monitorPathModal");
}

function saveMonitorPathEditor() {
  const idx = S._monitorPathEditIndex;
  if (idx === null || idx === undefined) return;
  const p = (S._monitorPaths || [])[idx];
  if (!p) return;

  const animeVal = document.getElementById("monitorPathEditAnime").value;
  const movieVal = document.getElementById("monitorPathEditMovie").value;
  const tvFormat = document
    .getElementById("monitorPathTvTemplate")
    .value.trim();
  const movieFormat = document
    .getElementById("monitorPathMovieTemplate")
    .value.trim();
  const mode = document.getElementById("monitorPathMode").value;
  const overwrite = document.getElementById("monitorPathOverwrite").value;
  const monitorMode = document.getElementById("monitorPathMonitorMode").value;
  const recognitionMode = document.getElementById("monitorPathRecognitionMode").value;
  const scrapeLanguage = document.getElementById("monitorPathScrapeLanguage").value;
  const scanNow = document.getElementById("monitorPathScanNow").checked;

  p.is_anime = animeVal === "true" ? true : animeVal === "false" ? false : null;
  p.is_movie = movieVal === "true" ? true : movieVal === "false" ? false : null;
  p.tv_rename_format = tvFormat;
  p.movie_rename_format = movieFormat;
  p.mode = mode;
  p.overwrite_mode = overwrite;
  p.monitor_mode = monitorMode;
  p.recognition_mode = recognitionMode || "smart";
  p.scrape_language = scrapeLanguage;
  p.scan_now = scanNow;

  document.getElementById("monitorPathsList").innerHTML =
    renderMonitorPathItems(S._monitorPaths);
  closeModal("monitorPathModal");
}

function renderStringListEditor(key, items, placeholder) {
  S["_list_" + key] = [...items];

  return `<div id="cfg_${key}">
    <div id="${key}List">${renderStringListItems(key, items)}</div>
    <div style="display:flex;gap:8px;margin-top:8px;">
      <input type="text" id="new_${key}_input" placeholder="${escHtml(placeholder)}" style="flex:1;"/>
      <button class="btn-outline" style="padding:4px 12px;font-size:12px;white-space:nowrap;" onclick="addStringListItem('${key}')">+ 添加</button>
    </div>
  </div>`;
}

function renderMultilineTextEditor(key, items, placeholder) {
  const value = (items || []).join("\n");
  return `<div id="cfg_${key}">
    <textarea id="${key}_textarea" rows="8" style="resize:vertical;font-family:monospace;font-size:12px;line-height:1.6;" placeholder="${escHtml(placeholder)}">${escHtml(value)}</textarea>
    <div style="margin-top:6px;font-size:11px;color:#9ca3af;">一行一条规则，留空行会自动忽略。</div>
  </div>`;
}

function renderStringListItems(key, items) {
  if (!items || items.length === 0) {
    return '<div style="color:#9ca3af;font-size:12px;padding:8px 0;">暂未配置</div>';
  }
  return items
    .map(
      (item, i) =>
        `<div style="display:flex;align-items:center;gap:8px;padding:4px 10px;background:#f9fafb;border-radius:8px;margin-bottom:4px;">
      <span style="font-size:13px;flex:1;">${escHtml(item)}</span>
      <button class="btn-outline" style="padding:2px 8px;font-size:11px;color:#dc2626;border-color:#dc2626;" onclick="removeStringListItem('${key}',${i})">✕</button>
    </div>`,
    )
    .join("");
}

function addStringListItem(key) {
  const input = document.getElementById(`new_${key}_input`);
  const val = input.value.trim();
  if (!val) return;
  S["_list_" + key].push(val);
  document.getElementById(`${key}List`).innerHTML = renderStringListItems(
    key,
    S["_list_" + key],
  );
  input.value = "";
}

function removeStringListItem(key, idx) {
  S["_list_" + key].splice(idx, 1);
  document.getElementById(`${key}List`).innerHTML = renderStringListItems(
    key,
    S["_list_" + key],
  );
}

// ─────────────────── SECONDARY RULES EDITOR ───────────────────

function renderSecondaryRules(val) {
  let rules = { movie: [], tv: [] };
  try {
    if (val && typeof val === "object") {
      rules.movie = Array.isArray(val.movie)
        ? JSON.parse(JSON.stringify(val.movie))
        : [];
      rules.tv = Array.isArray(val.tv)
        ? JSON.parse(JSON.stringify(val.tv))
        : [];
    }
  } catch (e) {}
  S._secondaryRules = rules;

  return `<div id="cfg_secondary_rules">
  <div style="font-size:11px;color:#6b7280;margin-bottom:8px;line-height:1.6;background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:8px;">
    规则按顺序匹配，第一个命中的规则生效。所有条件留空则为兜底规则（匹配其余所有内容）。<br>
    <strong>genre_ids：</strong>16=动漫 &nbsp;99=纪录片 &nbsp;10762=儿童 &nbsp;10764=真人秀 &nbsp;10767=综艺 &nbsp;10402=音乐<br>
    <strong>origin_country：</strong>CN=中国 &nbsp;HK=香港 &nbsp;TW=台湾 &nbsp;JP=日本 &nbsp;KR=韩国 &nbsp;US=美国 &nbsp;GB=英国
  </div>
  <div style="margin-bottom:12px;">
    <div style="font-size:12px;font-weight:600;color:#374151;margin-bottom:6px;">🎬 电影规则</div>
    <div style="font-size:11px;color:#9ca3af;margin-bottom:4px;display:flex;gap:4px;padding:0 2px;">
      <span style="width:20px;"></span>
      <span style="width:100px;">文件夹名称</span>
      <span style="width:110px;">genre_ids</span>
      <span style="width:120px;">origin_country</span>
      <span style="width:80px;">语言(lang)</span>
    </div>
    <div id="sec_movie_list">${renderSecRuleItems("movie")}</div>
    <button class="btn-gray" style="margin-top:6px;font-size:12px;padding:3px 12px;" onclick="addSecRule('movie')">+ 添加电影规则</button>
  </div>
  <div>
    <div style="font-size:12px;font-weight:600;color:#374151;margin-bottom:6px;">📺 TV 规则</div>
    <div style="font-size:11px;color:#9ca3af;margin-bottom:4px;display:flex;gap:4px;padding:0 2px;">
      <span style="width:20px;"></span>
      <span style="width:100px;">文件夹名称</span>
      <span style="width:110px;">genre_ids</span>
      <span style="width:120px;">origin_country</span>
      <span style="width:80px;">语言(lang)</span>
    </div>
    <div id="sec_tv_list">${renderSecRuleItems("tv")}</div>
    <button class="btn-gray" style="margin-top:6px;font-size:12px;padding:3px 12px;" onclick="addSecRule('tv')">+ 添加 TV 规则</button>
  </div>
  <div style="margin-top:10px;">
    <button class="btn-outline" style="font-size:12px;padding:3px 12px;" onclick="resetSecRules()">↩ 重置为默认规则</button>
  </div>
</div>`;
}

function renderSecRuleItems(type) {
  const rules = (S._secondaryRules || {})[type] || [];
  if (rules.length === 0) {
    return '<div style="color:#9ca3af;font-size:12px;padding:4px 2px;">暂无规则</div>';
  }
  return rules
    .map((rule, idx) => {
      const conds = rule.conditions || {};
      const isLast = idx === rules.length - 1;
      return `<div style="display:flex;align-items:center;gap:4px;padding:4px 6px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:4px;margin-bottom:3px;">
      <span style="color:#9ca3af;font-size:11px;width:20px;text-align:center;flex-shrink:0;">#${idx + 1}</span>
      <input type="text" value="${escHtml(rule.name || "")}" placeholder="文件夹名称"
        title="分类文件夹名称"
        style="width:100px;font-size:12px;padding:2px 4px;border:1px solid #d1d5db;border-radius:3px;"
        oninput="updateSecRule('${type}',${idx},'name',null,this.value)"/>
      <input type="text" value="${escHtml(conds.genre_ids || "")}" placeholder="如: 16,99"
        title="类型ID，逗号分隔，如: 16,99"
        style="width:110px;font-size:12px;padding:2px 4px;border:1px solid #d1d5db;border-radius:3px;"
        oninput="updateSecRule('${type}',${idx},'conditions','genre_ids',this.value)"/>
      <input type="text" value="${escHtml(conds.origin_country || "")}" placeholder="如: CN,JP,US"
        title="国家/地区代码，逗号分隔，如: CN,JP"
        style="width:120px;font-size:12px;padding:2px 4px;border:1px solid #d1d5db;border-radius:3px;"
        oninput="updateSecRule('${type}',${idx},'conditions','origin_country',this.value)"/>
      <input type="text" value="${escHtml(conds.original_language || "")}" placeholder="如: zh,ja"
        title="语言代码，逗号分隔，如: zh,ja"
        style="width:80px;font-size:12px;padding:2px 4px;border:1px solid #d1d5db;border-radius:3px;"
        oninput="updateSecRule('${type}',${idx},'conditions','original_language',this.value)"/>
      <button onclick="moveSecRule('${type}',${idx},-1)" title="上移"
        style="padding:2px 6px;font-size:11px;background:#e5e7eb;border:1px solid #d1d5db;border-radius:3px;cursor:pointer;"
        ${idx === 0 ? "disabled" : ""}>▲</button>
      <button onclick="moveSecRule('${type}',${idx},1)" title="下移"
        style="padding:2px 6px;font-size:11px;background:#e5e7eb;border:1px solid #d1d5db;border-radius:3px;cursor:pointer;"
        ${isLast ? "disabled" : ""}>▼</button>
      <button onclick="removeSecRule('${type}',${idx})" title="删除此规则"
        style="padding:2px 6px;font-size:11px;color:#ef4444;background:#fee2e2;border:1px solid #fca5a5;border-radius:3px;cursor:pointer;">×</button>
    </div>`;
    })
    .join("");
}

function updateSecRule(type, idx, field, subfield, value) {
  if (!S._secondaryRules || !S._secondaryRules[type]) return;
  const rule = S._secondaryRules[type][idx];
  if (!rule) return;
  if (subfield) {
    if (!rule.conditions) rule.conditions = {};
    rule.conditions[subfield] = value;
  } else {
    rule[field] = value;
  }
}

function addSecRule(type) {
  if (!S._secondaryRules) S._secondaryRules = { movie: [], tv: [] };
  S._secondaryRules[type].push({ name: "新分类", conditions: {} });
  const el = document.getElementById(`sec_${type}_list`);
  if (el) el.innerHTML = renderSecRuleItems(type);
}

function removeSecRule(type, idx) {
  if (!S._secondaryRules || !S._secondaryRules[type]) return;
  S._secondaryRules[type].splice(idx, 1);
  const el = document.getElementById(`sec_${type}_list`);
  if (el) el.innerHTML = renderSecRuleItems(type);
}

function moveSecRule(type, idx, dir) {
  if (!S._secondaryRules || !S._secondaryRules[type]) return;
  const arr = S._secondaryRules[type];
  const newIdx = idx + dir;
  if (newIdx < 0 || newIdx >= arr.length) return;
  [arr[idx], arr[newIdx]] = [arr[newIdx], arr[idx]];
  const el = document.getElementById(`sec_${type}_list`);
  if (el) el.innerHTML = renderSecRuleItems(type);
}

function resetSecRules() {
  if (!confirm("确认重置为默认规则？当前规则将被覆盖。")) return;
  S._secondaryRules = JSON.parse(JSON.stringify(DEFAULT_SECONDARY_RULES));
  const movieEl = document.getElementById("sec_movie_list");
  if (movieEl) movieEl.innerHTML = renderSecRuleItems("movie");
  const tvEl = document.getElementById("sec_tv_list");
  if (tvEl) tvEl.innerHTML = renderSecRuleItems("tv");
}

// ─────────────────── AI TEST ───────────────────

async function testAI() {
  const nameEl = document.getElementById("aiTestName");
  const name = nameEl ? nameEl.value.trim() : "";
  const statusEl = document.getElementById("aiTestStatus");
  statusEl.textContent = "🔄 测试中…";
  statusEl.style.color = "#6b7280";

  const { ok, data } = await POST("/api/ai/test", {
    name: name || "进击的巨人 (2013)",
  });

  if (ok && data.ok) {
    statusEl.textContent = data.message;
    statusEl.style.color = "#16a34a";
  } else {
    statusEl.textContent = "❌ " + (data?.message || "测试失败");
    statusEl.style.color = "#dc2626";
  }
}

// Collect friendly config values for save
function collectFriendlyValue(key) {
  if (key === "secondary_rules") {
    return S._secondaryRules || { movie: [], tv: [] };
  }
  if (key === "scrape_image_types" || key === "subtitle_extensions") {
    const checks = document.querySelectorAll(
      `input.friendly-chk[data-cfg-key="${key}"]:checked`,
    );
    return Array.from(checks).map((c) => c.value);
  }
  if (key === "monitor_paths") {
    return (S._monitorPaths || []).map((p) => {
      const hasExtras =
        p.is_anime !== null ||
        p.is_movie !== null ||
        (p.tv_rename_format && p.tv_rename_format.trim() !== "") ||
        (p.movie_rename_format && p.movie_rename_format.trim() !== "") ||
        (p.mode && p.mode.trim() !== "") ||
        (p.overwrite_mode && p.overwrite_mode.trim() !== "") ||
        (p.monitor_mode && p.monitor_mode.trim() !== "") ||
        (p.recognition_mode && p.recognition_mode.trim() !== "" && p.recognition_mode !== "smart") ||
        (p.scrape_language && p.scrape_language.trim() !== "") ||
        p.scan_now === true;
      if (!hasExtras) return p.path;
      const entry = { path: p.path };
      if (p.is_anime !== null) entry.is_anime = p.is_anime;
      if (p.is_movie !== null) entry.is_movie = p.is_movie;
      if (p.tv_rename_format && p.tv_rename_format.trim() !== "")
        entry.tv_rename_format = p.tv_rename_format.trim();
      if (p.movie_rename_format && p.movie_rename_format.trim() !== "")
        entry.movie_rename_format = p.movie_rename_format.trim();
      if (p.mode && p.mode.trim() !== "") entry.mode = p.mode.trim();
      if (p.overwrite_mode && p.overwrite_mode.trim() !== "")
        entry.overwrite_mode = p.overwrite_mode.trim();
      if (p.monitor_mode && p.monitor_mode.trim() !== "")
        entry.monitor_mode = p.monitor_mode.trim();
      if (p.recognition_mode && p.recognition_mode.trim() !== "")
        entry.recognition_mode = p.recognition_mode.trim();
      if (p.scrape_language && p.scrape_language.trim() !== "")
        entry.scrape_language = p.scrape_language.trim();
      if (p.scan_now === true) entry.scan_now = true;
      return entry;
    });
  }
  if (key === "title_languages" || key === "overview_languages") {
    const textarea = document.getElementById(`${key}_textarea`);
    if (!textarea) return [];
    return textarea.value
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
  }
  if (key === "monitor_exclude_dirs") {
    const textarea = document.getElementById(`${key}_textarea`);
    if (!textarea) return [];
    return textarea.value
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
  }
  return [];
}

async function saveConfig() {
  const cfg = { ...S.configData };

  Object.keys(CONFIG_LABELS).forEach((key) => {
    if (CONFIG_HIDDEN_KEYS.has(key)) return;

    // Friendly keys are collected via collectFriendlyValue, not from a single DOM element
    if (CONFIG_FRIENDLY_KEYS.has(key)) {
      cfg[key] = collectFriendlyValue(key);
      return;
    }

    const el = document.getElementById(`cfg_${key}`);
    if (!el) return;

    if (CONFIG_BOOL_KEYS.has(key)) {
      cfg[key] = el.checked;
    } else if (CONFIG_JSON_KEYS.has(key)) {
      try {
        cfg[key] = JSON.parse(el.value);
      } catch {
        cfg[key] = el.value;
      }
    } else if (key.includes("temperature")) {
      cfg[key] = parseFloat(el.value) || 0;
    } else if (key.includes("rate_limit")) {
      cfg[key] = parseInt(el.value || "0", 10) || 0;
    } else {
      cfg[key] = el.value;
    }
  });

  const prevCfg = S.configData || {};
  const needRestart = [
    "monitor_enabled",
    "monitor_mode",
    "monitor_paths",
  ].some((k) => JSON.stringify(cfg[k]) !== JSON.stringify(prevCfg[k]));

  const { ok } = await POST("/api/config", cfg);
  if (ok) {
    closeModal("configModal");
    notify("配置已保存", "positive");
    S.configData = cfg;
    if (S._monitorPaths) {
      S._monitorPaths.forEach((p) => {
        p.scan_now = false;
      });
    }
    if (needRestart) {
      await POST("/api/monitor/restart", {});
    } else {
      await POST("/api/monitor/exclude", {
        exclude_dirs: cfg.monitor_exclude_dirs || [],
      });
    }
  } else {
    notify("保存失败", "negative");
  }
}

// ─────────────────── STATS & MONITOR ───────────────────
async function refreshStats() {
  const { ok, data } = await GET("/api/stats");
  if (!ok) return;
  const el = (id) => document.getElementById(id);
  el("statTotal").textContent = data.total_tasks || 0;
  el("statSuccess").textContent = (data.status && data.status.success) || 0;
  el("statFailed").textContent = (data.status && data.status.failed) || 0;
  el("statQueue").textContent = data.queue_length || 0;

  const { ok: mok, data: md } = await GET("/api/monitor/status");
  if (mok) {
    const st = el("monitorStatus");
    if (md.enabled) {
      st.textContent = md.paused
        ? "⏸ 已暂停"
        : "▶ 运行中  队列: " + md.queue_length;
      st.style.color = md.paused ? "#d97706" : "#16a34a";
    } else {
      st.textContent = "已禁用";
      st.style.color = "#9ca3af";
    }
  }

  const { ok: qok, data: qd } = await GET("/api/queue");
  if (qok) {
    renderQueueDetail(qd.items || []);
  }
}

function renderQueueDetail(items) {
  const listEl = document.getElementById("queueDetailList");
  const countEl = document.getElementById("queueDetailCount");
  if (!listEl || !countEl) return;
  const total = Array.isArray(items) ? items.length : 0;
  countEl.textContent = `当前队列: ${total}`;
  if (!items || items.length === 0) {
    listEl.innerHTML = '<div style="color:#9ca3af;">队列为空</div>';
    return;
  }
  const sliced = items.slice(0, 200);
  listEl.innerHTML = sliced
    .map((it) => {
      const p = it.path || "";
      const name = p.split("/").pop() || p;
      return `<div style="padding:2px 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="${escHtml(p)}">${escHtml(name)}</div>`;
    })
    .join("");
}

async function testTMDB() {
  document.getElementById("tmdbStatus").textContent = "检测中…";
  const { ok, data } = await GET("/api/tmdb/test");
  const el = document.getElementById("tmdbStatus");
  if (ok && data.ok) {
    el.textContent = "✅ " + data.message;
    el.style.color = "#16a34a";
  } else {
    el.textContent = "❌ " + (data.message || "连接失败");
    el.style.color = "#dc2626";
  }
}

async function monitorPause() {
  const { ok } = await POST("/api/monitor/pause", {});
  notify(ok ? "已暂停处理" : "操作失败", ok ? "info" : "negative");
  refreshStats();
}

async function monitorResume() {
  const { ok } = await POST("/api/monitor/resume", {});
  notify(ok ? "已恢复处理" : "操作失败", ok ? "positive" : "negative");
  refreshStats();
}

async function monitorRestart() {
  const { ok } = await POST("/api/monitor/restart", {});
  notify(ok ? "监控已重启" : "重启失败", ok ? "positive" : "negative");
  refreshStats();
}

async function clearQueue() {
  if (!confirm("确认清空所有待处理队列？")) return;
  const { ok } = await POST("/api/queue/clear", {});
  notify(ok ? "队列已清空" : "操作失败", ok ? "positive" : "negative");
  refreshStats();
}

async function saveQueue() {
  const { ok } = await POST("/api/queue/save", {});
  notify(ok ? "队列已保存到磁盘" : "保存失败", ok ? "positive" : "negative");
}

async function loadQueueFromDisk() {
  const { ok } = await POST("/api/queue/load", {});
  notify(ok ? "已从磁盘加载队列" : "加载失败", ok ? "positive" : "negative");
  refreshStats();
}

// ─────────────────── LOGS ───────────────────
async function refreshLogs() {
  const { ok, data } = await GET("/api/logs?n=200");
  if (!ok) return;
  S.logLines = data.lines || [];
  renderLogViewer();
}

function renderLogViewer() {
  const viewer = document.getElementById("logViewer");
  viewer.innerHTML = S.logLines
    .map((line) => {
      let cls = "";
      if (line.includes("[ERROR]")) cls = "ERROR";
      else if (line.includes("[WARNING]") || line.includes("[WARN]"))
        cls = "WARNING";
      else if (line.includes("[DEBUG]")) cls = "DEBUG";
      return `<div class="log-line ${cls}">${escHtml(line)}</div>`;
    })
    .join("");
  viewer.scrollTop = viewer.scrollHeight;
}

function clearLogView() {
  S.logLines = [];
  document.getElementById("logViewer").innerHTML = "";
}

// ─────────────────── UTILITIES ───────────────────
function escHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ─────────────────── INIT ───────────────────
document.addEventListener("DOMContentLoaded", () => {
  showTab("tasks");
  // Auto-refresh stats every 30 seconds
  setInterval(refreshStats, 30000);
  // Auto-refresh task table every 15 seconds when tasks tab is active
  const sortByEl = document.getElementById("sortBySelect");
  const sortOrderEl = document.getElementById("sortOrderSelect");
  if (sortByEl && !sortByEl.value) sortByEl.value = S.sortBy;
  if (sortOrderEl && !sortOrderEl.value) sortOrderEl.value = S.sortOrder;

  setInterval(() => {
    const pane = document.getElementById("pane-tasks");
    if (pane && pane.style.display !== "none") loadTasks();
  }, 15000);
});
