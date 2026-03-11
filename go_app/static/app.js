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
  }
  if (name === "tasks") loadTasks();
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

async function loadTasks() {
  const text = document.getElementById("searchText").value;
  const status = document.getElementById("filterStatus").value;
  const qs = new URLSearchParams({
    text,
    status,
    page: S.page,
    page_size: S.pageSize,
  });

  const { ok, data } = await GET(`/api/tasks?${qs}`);
  if (!ok) {
    notify("加载任务失败", "negative");
    return;
  }

  S.totalItems = data.total;
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
      '<tr><td colspan="8" style="text-align:center;color:#9ca3af;padding:32px;">暂无任务</td></tr>';
    return;
  }
  tbody.innerHTML = items
    .map((t) => {
      const chk = S.selected.has(t.uuid) ? "checked" : "";
      const nameCell = `<div style="font-weight:600;font-size:12px;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${t.name || ""}">${t.name || "—"}</div>
      <div style="font-size:11px;color:#9ca3af;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${t.path}">${t.path}</div>`;
      const errCell =
        t.status === "failed" && t.error
          ? `<div style="font-size:11px;color:#dc2626;margin-top:2px;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${t.error}">${t.error}</div>`
          : "";
      return `<tr>
      <td><input type="checkbox" class="chk row-chk" data-uuid="${t.uuid}" ${chk} onchange="onRowCheck(this)"/></td>
      <td>${nameCell}${errCell}</td>
      <td>${badgeForStatus(t.status)}</td>
      <td>${t.season_id != null ? t.season_id : "—"}</td>
      <td>${t.episode_id > 0 ? t.episode_id : "—"}</td>
      <td style="font-size:11px;color:#6b7280;">${t.tmdb_id || "—"}</td>
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

function onRowCheck(el) {
  const uuid = el.dataset.uuid;
  if (el.checked) S.selected.add(uuid);
  else S.selected.delete(uuid);
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
}

// ─────────────────── EDIT TASK ───────────────────
async function openEdit(uuid) {
  const { ok, data } = await GET(`/api/tasks/${uuid}`);
  if (!ok) {
    notify("加载任务失败", "negative");
    return;
  }

  document.getElementById("editUUID").value = uuid;
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

  const body = {
    uuid,
    path: "", // will be preserved server-side
    name: name || null,
    is_anime: toTriBool(animeVal),
    is_movie: toTriBool(movieVal),
    use_ai: aiVal === "启用",
    season_id: seasonRaw !== "" ? parseInt(seasonRaw, 10) : null,
    episode_offset: offsetRaw !== "" ? parseInt(offsetRaw, 10) : 0,
    tmdb_id: tmdbID || null,
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
  const { ok } = await POST("/api/tasks/retry", {
    uuids: [uuid],
    settings: {},
  });
  notify(ok ? "已加入重试队列" : "重试提交失败", ok ? "positive" : "negative");
  if (ok) setTimeout(loadTasks, 800);
}

async function deleteSingle(uuid) {
  if (!confirm("确认删除此任务记录？")) return;
  const { ok } = await DEL(`/api/tasks/${uuid}`);
  notify(ok ? "已删除" : "删除失败", ok ? "positive" : "negative");
  if (ok) {
    S.selected.delete(uuid);
    loadTasks();
  }
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

  const tmdbID = document.getElementById("bRetryTMDBID")?.value.trim();
  const seasonID = document.getElementById("bRetrySeasonID")?.value.trim();
  const offset = document.getElementById("bRetryOffset")?.value.trim();
  if (tmdbID) settings.tmdb_id = tmdbID;
  if (seasonID) settings.season_id = seasonID;
  if (offset) settings.episode_offset = offset;

  const { ok, data } = await POST("/api/tasks/retry", {
    uuids: [...S.selected],
    settings,
  });
  closeModal("batchRetryModal");
  notify(
    ok ? `已将 ${data.queued || 0} 个任务加入队列` : "批量重试失败",
    ok ? "positive" : "negative",
  );
  if (ok) setTimeout(loadTasks, 800);
}

async function batchDelete() {
  if (S.selected.size === 0) {
    notify("请先选择任务", "warning");
    return;
  }
  if (!confirm(`确认删除选中的 ${S.selected.size} 条任务记录？`)) return;
  const { ok, data } = await POST("/api/tasks/delete", {
    uuids: [...S.selected],
    delete_files: false,
  });
  notify(
    ok ? `已删除 ${data.deleted || 0} 条` : "删除失败",
    ok ? "positive" : "negative",
  );
  if (ok) {
    S.selected.clear();
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
  subtitle_extensions: "💬 字幕扩展名",
  secondary_classification: "📂 二级分类",
  docker_mnt: "🐳 Docker挂载路径",
  log_level: "📝 日志等级",
  ai_provider: "🤖 AI提供商",
  ai_api_key: "🔑 OpenAI API Key",
  ai_base_url: "🌐 OpenAI Base URL",
  ai_model: "🧠 OpenAI 模型",
  ai_temperature: "🌡 OpenAI Temperature",
  gemini_api_key: "💎 Gemini API Key",
  gemini_base_url: "🌐 Gemini Base URL",
  gemini_model: "💎 Gemini 模型",
  gemini_temperature: "🌡 Gemini Temperature",
  ai_enabled: "🚀 启用 AI",
  ai_confidence_threshold: "📊 AI 置信度阈值",
  openai_output_format: "🎯 OpenAI 输出格式",
  ai_auto_save: "💾 自动保存 AI 分析",
  monitor_enabled: "👁 启用监控",
  monitor_mode: "⚙ 监控模式",
  monitor_paths: "📁 监控目录 (JSON)",
  monitor_exclude_dirs: "🚫 监控排除目录 (JSON)",
};

const CONFIG_SELECT_OPTIONS = {
  mode: ["硬链接", "软链接", "复制", "剪切"],
  overwrite_mode: ["从不覆盖", "总是覆盖", "保留最新"],
  log_level: ["DEBUG", "INFO", "WARNING", "ERROR"],
  ai_provider: ["openai", "gemini"],
  ai_confidence_threshold: ["High", "Medium", "Low"],
  openai_output_format: ["function_calling", "json_object"],
  monitor_mode: ["compatibility", "native"],
};

const CONFIG_BOOL_KEYS = new Set([
  "scrape_metadata",
  "secondary_classification",
  "ai_enabled",
  "ai_auto_save",
  "monitor_enabled",
]);
const CONFIG_JSON_KEYS = new Set([
  "scrape_image_types",
  "subtitle_extensions",
  "exclude_dirs",
  "monitor_paths",
  "monitor_exclude_dirs",
  "secondary_rules",
]);
const CONFIG_HIDDEN_KEYS = new Set(["secondary_rules"]);

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
        "scrape_image_types",
        "subtitle_extensions",
        "secondary_classification",
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

      if (CONFIG_BOOL_KEYS.has(key)) {
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
      } else {
        input = `<input type="text" id="cfg_${key}" value="${escHtml(String(val ?? ""))}"/>`;
      }

      html += `<div class="cfg-row"><span class="cfg-label">${label}</span><div class="cfg-val">${input}</div></div>`;
    });
  });
  form.innerHTML = html;
}

async function saveConfig() {
  const cfg = { ...S.configData };

  Object.keys(CONFIG_LABELS).forEach((key) => {
    if (CONFIG_HIDDEN_KEYS.has(key)) return;
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
    } else {
      cfg[key] = el.value;
    }
  });

  const { ok } = await POST("/api/config", cfg);
  if (ok) {
    closeModal("configModal");
    notify("配置已保存", "positive");
    await POST("/api/monitor/restart", {});
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
  setInterval(() => {
    const pane = document.getElementById("pane-tasks");
    if (pane && pane.style.display !== "none") loadTasks();
  }, 15000);
});
