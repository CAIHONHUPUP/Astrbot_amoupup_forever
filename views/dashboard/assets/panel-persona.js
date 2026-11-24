/* 人设面板：当前人设 / 切换 / 候选 / 语气预览 / 导入导出（2026-11-21，2026-11-24 重设计） */
(function () {
  const PREVIEW_HINT = "点「生成预览」，看看她现在的提示词长什么样。";
  const PREVIEW_EMPTY = "（这个人设还没有语料，提示词里只有人设卡）";
  // 快捷场景：点一下直接代入 scene / mood 并刷新预览；空字符串 = 不指定（不挑语料）
  const SCENES = [
    { label: "通用", scene: "", mood: "" },
    { label: "吵架", scene: "quarrel", mood: "委屈" },
    { label: "生病", scene: "sick", mood: "示弱" },
    { label: "认错", scene: "makeup", mood: "认错" },
    { label: "撒娇", scene: "remember", mood: "撒娇" },
    { label: "被夸", scene: "praise", mood: "骄傲" },
    { label: "吃饭", scene: "choose_food", mood: "随和" },
    { label: "安慰", scene: "comfort_fail", mood: "坚定" }
  ];

  let lastStatus = {};

  function el(id) {
    return document.getElementById(id);
  }

  function unwrap(result) {
    if (!result) return {};
    if (typeof result.code === "number" && result.code !== 0) {
      throw new Error(result.msg || result.message || "接口返回错误");
    }
    return result.data || result;
  }

  async function callApi(action, payload) {
    if (typeof window.call !== "function") throw new Error("API 未加载");
    return unwrap(await window.call(action, payload || {}));
  }

  function toastMsg(text, isError) {
    if (typeof window.toast === "function") window.toast(text, !!isError);
  }

  function setText(id, value) {
    const node = el(id);
    if (!node) return;
    node.textContent = value === undefined || value === null || value === "" ? "—" : String(value);
  }

  function fieldValue(id) {
    const node = el(id);
    return node ? String(node.value || "").trim() : "";
  }

  function renderHead(data) {
    const name = String(data.name || data.active || "").trim();
    const core = String(data.core || "").trim();
    setText("personaName", name || "未命名人设");
    setText("personaCore", core || "还没有写人设内核（card.core）");
    const actionBtn = el("personaActionToggle");
    if (actionBtn) {
      const on = !!data.action_narration;
      actionBtn.textContent = "动作 · " + (on ? "开" : "关");
      actionBtn.dataset.enabled = on ? "1" : "0";
      actionBtn.classList.toggle("hot", on);
    }
    const avatar = el("personaAvatar");
    if (avatar) avatar.textContent = (name || "她").slice(0, 1);
    setText("personaCorpus", String(Number(data.corpus_count) || 0));
    // 2026-11-24：进化=已通过（池子），待批=待通过例句；以前待批读的是手动候选，进化读了个不存在的字段，永远 0
    setText("personaEvolved", String(Number(data.pool_count) || 0));
    setText("personaPending", String((data.pending_pool || []).length));
  }

  function renderProfiles(data) {
    const box = el("personaProfiles");
    if (!box) return;
    box.textContent = "";
    const rows = data.profiles || [];
    if (!rows.length) {
      const empty = document.createElement("span");
      empty.className = "sub";
      empty.textContent = "还没有其他人设，可以导入一份人设包。";
      box.appendChild(empty);
      return;
    }
    const labels = data.profile_labels || {};
    rows.forEach((pid) => {
      const active = pid === data.active;
      const label = String(labels[pid] || pid);
      const button = document.createElement("button");
      button.type = "button";
      button.className = active ? "active" : "";
      button.textContent = (active ? "✓ " : "") + label;
      button.disabled = active;
      button.title = (active ? "当前正在使用" : "切换到 " + label) + "（" + pid + "）";
      button.addEventListener("click", () => switchProfile(pid));
      box.appendChild(button);
    });
  }

  function candidateRows(data) {
    const rich = Array.isArray(data.candidate_rows) ? data.candidate_rows : [];
    if (rich.length) {
      return rich.map((row) => ({
        text: String(row.text || ""),
        source: String(row.source || "manual"),
      }));
    }
    return (data.candidates || []).map((text) => ({ text: String(text || ""), source: "manual" }));
  }

  function renderCandidates(data) {
    const box = el("personaCandidates");
    if (!box) return;
    box.textContent = "";
    const rows = candidateRows(data);
    const pill = el("personaCandidatePill");
    if (pill) {
      pill.textContent = rows.length ? rows.length + " 条待批" : "0 条";
      pill.className = "persona-pill" + (rows.length ? " hot" : "");
    }
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "persona-empty";
      const icon = document.createElement("span");
      icon.className = "icon";
      icon.textContent = "♡";
      const title = document.createElement("div");
      title.textContent = "没有待批准的新语料";
      const hint = document.createElement("span");
      hint.className = "hint";
      hint.textContent = "想手动塞一条：聊天里发 /人设 候选加 这句话";
      empty.appendChild(icon);
      empty.appendChild(title);
      empty.appendChild(hint);
      box.appendChild(empty);
      return;
    }
    rows.forEach((item, index) => {
      const row = document.createElement("div");
      row.className = "persona-candidate";
      const body = document.createElement("div");
      body.className = "persona-candidate-text";
      const tag = document.createElement("span");
      tag.className = "sub persona-source";
      tag.textContent = item.source === "auto" ? "自动 · " : "手动 · ";
      body.appendChild(tag);
      body.appendChild(document.createTextNode(item.text));
      const button = document.createElement("button");
      button.type = "button";
      button.className = "persona-approve";
      button.textContent = "通过";
      button.addEventListener("click", () => approve(index));
      row.appendChild(body);
      row.appendChild(button);
      box.appendChild(row);
    });
  }

  function emptyBox(titleText, hintText) {
    const empty = document.createElement("div");
    empty.className = "persona-empty";
    const title = document.createElement("div");
    title.textContent = titleText;
    const hint = document.createElement("span");
    hint.className = "hint";
    hint.textContent = hintText;
    empty.appendChild(title);
    empty.appendChild(hint);
    return empty;
  }

  function mkBtn(label, cls) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "persona-approve" + (cls ? " " + cls : "");
    b.textContent = label;
    return b;
  }

  function poolRow(item, opts) {
    const options = opts || {};
    const row = document.createElement("div");
    row.className = "persona-candidate";
    const body = document.createElement("div");
    body.className = "persona-candidate-text";
    const tag = document.createElement("span");
    tag.className = "sub persona-source";
    tag.textContent = String(item.scene || "通用") + " · ";
    const shown = document.createElement("span");
    shown.textContent = String(item.text || "");
    body.appendChild(tag);
    body.appendChild(shown);

    const logIndex = Number(item.log_index);
    const canEdit = Number.isFinite(logIndex) && logIndex >= 0;
    const edit = mkBtn("编辑");
    const save = mkBtn("保存", "primary");
    const cancel = mkBtn("取消");
    const undo = mkBtn("撤销");
    save.hidden = true;
    cancel.hidden = true;
    edit.hidden = !canEdit;
    undo.hidden = !canEdit;

    const area = document.createElement("textarea");
    area.rows = 2;
    area.value = String(item.text || "");
    area.hidden = true;
    area.style.cssText = "width:100%;min-height:54px;resize:vertical;border:1px solid var(--line);" +
      "border-radius:10px;padding:8px;font:inherit;font-size:13px;line-height:1.6;" +
      "background:transparent;color:inherit;outline:none;";

    edit.addEventListener("click", () => {
      area.value = String(item.text || "");
      area.hidden = false;
      body.textContent = "";
      body.appendChild(area);
      edit.hidden = true; undo.hidden = true;
      save.hidden = false; cancel.hidden = false;
      area.focus();
    });
    cancel.addEventListener("click", () => render(lastStatus));
    save.addEventListener("click", () => saveEdit(logIndex, area.value));
    undo.addEventListener("click", () => undoAuto(logIndex));

    if (options.approveIndex !== undefined) {
      const ok = mkBtn("通过", "primary");
      ok.addEventListener("click", () => approvePool(options.approveIndex));
      row.appendChild(ok);
    }
    row.appendChild(body);
    row.appendChild(edit);
    row.appendChild(save);
    row.appendChild(cancel);
    row.appendChild(undo);
    return row;
  }

  function renderRecent(data) {
    const approved = el("personaRecent");
    const pending = el("personaPendingList");
    const pendingRows = Array.isArray(data.pending_pool) ? data.pending_pool : [];
    const poolRows = Array.isArray(data.pool_rows) ? data.pool_rows
      : (Array.isArray(data.recent_auto) ? data.recent_auto : []);

    setText("personaPoolPill", (Number(data.pool_count) || 0) + " 条池");
    setText("personaPendingPill", pendingRows.length + " 条");
    setText("personaApprovedPill", poolRows.length + " 条");

    if (pending) {
      pending.textContent = "";
      if (!pendingRows.length) {
        pending.appendChild(emptyBox("没有待通过的句子",
          "她说过、你反应不错的新句子会先落到这里，点「通过」才写进例句池。"));
      } else {
        pendingRows.forEach((item, index) => {
          pending.appendChild(poolRow(item, { approveIndex: index }));
        });
      }
    }
    if (approved) {
      approved.textContent = "";
      if (!poolRows.length) {
        approved.appendChild(emptyBox("池子是空的", "通过之后才会进池，池子只学语气、不抄原话。"));
      } else {
        poolRows.forEach((item) => approved.appendChild(poolRow(item, {})));
      }
    }
  }

  async function approvePool(index) {
    try {
      render(await callApi("persona/pool/approve", { index }));
      toastMsg("已通过，这条进池了");
    } catch (error) {
      toastMsg("通过失败：" + (error.message || error), true);
    }
  }

  function renderAuto(data) {
    const on = !!data.auto_evolve;
    setText("personaAutoState", on ? "开（只入池，不自动通过）" : "关");
    setText("personaAutoHint", "今日 +" + (Number(data.today_added) || 0) + " 条");
    const button = el("personaAutoToggle");
    if (button) {
      button.textContent = on ? "暂停" : "开启";
      button.dataset.enabled = on ? "1" : "0";
    }
    renderRecent(data);
  }

  function render(data) {
    lastStatus = data || {};
    renderHead(lastStatus);
    renderAuto(lastStatus);
    renderProfiles(lastStatus);
  }

  async function load() {
    try {
      render(await callApi("persona/status"));
    } catch (error) {
      const name = el("personaName");
      if (name) name.textContent = "读取失败";
      toastMsg("读取人设失败：" + (error.message || error), true);
    }
  }

  async function copyProfile() {
    const input = el("personaCopyName");
    const name = String((input && input.value) || "").trim();
    if (!name) { toastMsg("先给它起个名字", true); return; }
    try {
      const data = await callApi("persona/export", { id: lastStatus.active || "" });
      const pack = (data && (data.pack || (data.data && data.data.pack))) || null;
      if (!pack) throw new Error("读不到当前人设包");
      const copy = JSON.parse(JSON.stringify(pack));
      copy.id = String(lastStatus.active || "pack") + "-copy-" + Date.now().toString(36);
      copy.name = name;
      render(await callApi("persona/import", { pack: copy }));
      if (input) input.value = "";
      toastMsg("已复制成「" + name + "」，点它切换过去");
    } catch (error) {
      toastMsg("复制失败：" + (error.message || error), true);
    }
  }

  async function switchProfile(id) {
    try {
      render(await callApi("persona/switch", { id }));
      toastMsg("已切换到 " + id);
    } catch (error) {
      toastMsg("切换失败：" + (error.message || error), true);
    }
  }

  async function approve(index) {
    try {
      render(await callApi("persona/approve", { index }));
      toastMsg("已通过，这条会加进她的人设");
    } catch (error) {
      toastMsg("通过失败：" + (error.message || error), true);
    }
  }

  async function toggleAuto() {
    const button = el("personaAutoToggle");
    const next = !(button && button.dataset.enabled === "1");
    try {
      render(await callApi("persona/auto", { key: "auto_evolve", enabled: next }));
      toastMsg(next ? "自动进化已开启（只入池，不自动通过）" : "自动进化已暂停");
    } catch (error) {
      toastMsg("切换失败：" + (error.message || error), true);
    }
  }

  async function undoAuto(index) {
    try {
      render(await callApi("persona/undo", { index }));
      toastMsg("已撤销这条自动进化");
    } catch (error) {
      toastMsg("撤销失败：" + (error.message || error), true);
    }
  }

  async function saveEdit(index, value) {
    const text = String(value || "").trim();
    if (!text) {
      toastMsg("不能存空句子", true);
      return;
    }
    try {
      render(await callApi("persona/pool/edit", { index, text }));
      toastMsg("已保存");
    } catch (error) {
      toastMsg("保存失败：" + (error.message || error), true);
    }
  }

  /** 导出的是 { pack, state } 整包；导入时允许直接粘回来（取里面的 pack）。 */
  function pickPack(parsed) {
    let data = parsed;
    if (data && data.pack && typeof data.pack === "object") return data.pack;
    if (data && data.data && data.data.pack && typeof data.data.pack === "object") return data.data.pack;
    return data;
  }

  async function importPack(text) {
    let parsed;
    try {
      parsed = JSON.parse(String(text || "").trim());
    } catch (error) {
      toastMsg("JSON 解析失败，检查一下是不是整段粘全了", true);
      return;
    }
    const pack = pickPack(parsed);
    if (!pack || typeof pack !== "object" || Array.isArray(pack)) {
      toastMsg("这不是一份人设包（应当是一个 JSON 对象）", true);
      return;
    }
    try {
      const out = await callApi("persona/import", { pack });
      render(out);
      toastMsg("导入成功；在上面点它切换过去才会生效");
    } catch (error) {
      const message = String(error.message || error || "");
      const hint = message.indexOf("已存在") >= 0 ? "（把包里的 id 改成别的，比如 " +
        String(pack.id || "pack") + "-v2，再导入）" : "";
      toastMsg("导入失败：" + message + hint, true);
    }
  }

  async function exportCurrent() {
    try {
      const out = await callApi("persona/export", {});
      const box = el("personaJson");
      if (box) box.value = JSON.stringify(out, null, 2);
      const io = el("personaIo");
      if (io) io.open = true;
      toastMsg("已导出到下面的文本框");
    } catch (error) {
      toastMsg("导出失败：" + (error.message || error), true);
    }
  }

  async function copyJson() {
    const box = el("personaJson");
    const text = box ? String(box.value || "").trim() : "";
    if (!text) {
      toastMsg("文本框是空的，先点「导出当前」", true);
      return;
    }
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else if (box) {
        box.focus();
        box.select();
        document.execCommand("copy");
      }
      toastMsg("已复制");
    } catch (error) {
      toastMsg("复制失败，手动全选复制吧", true);
    }
  }

  function downloadJson() {
    const box = el("personaJson");
    const text = box ? String(box.value || "").trim() : "";
    if (!text) {
      toastMsg("文本框是空的，先点「导出当前」", true);
      return;
    }
    const pid = String(lastStatus.active || "persona");
    const blob = new Blob([text + "\n"], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "persona-" + pid + ".json";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    toastMsg("已下载 persona-" + pid + ".json");
  }

  function markScene(active) {
    document.querySelectorAll("#personaScenes .persona-chip").forEach((chip) => {
      chip.classList.toggle("active", chip === active);
    });
  }

  /** 参数变了就把上一次的预览结果作废，避免旧结果看起来像新场景的。 */
  function invalidatePreview(reason) {
    const box = el("personaPreviewBox");
    const meta = el("personaPreviewMeta");
    if (box) box.textContent = PREVIEW_HINT;
    if (meta) meta.textContent = (reason || "参数已改") + " · 点「生成预览」刷新结果";
  }

  function renderScenes() {
    const box = el("personaScenes");
    if (!box) return;
    box.textContent = "";
    SCENES.forEach((item) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "persona-chip";
      chip.textContent = item.label;
      chip.addEventListener("click", () => {
        const scene = el("personaScene");
        const mood = el("personaMood");
        if (scene) scene.value = item.scene;
        if (mood) mood.value = item.mood;
        markScene(chip);
        invalidatePreview("场景已填好");
      });
      box.appendChild(chip);
    });
  }

  async function preview() {
    const scene = fieldValue("personaScene");
    const mood = fieldValue("personaMood");
    const box = el("personaPreviewBox");
    const meta = el("personaPreviewMeta");
    const button = el("personaPreview");
    if (box) box.textContent = "正在取她这个场景的语料…";
    if (meta) meta.textContent = "读取中…";
    if (button) button.disabled = true;
    try {
      const out = await callApi("persona/preview", { scene, mood });
      const prompt = String(out.prompt || "");
      const samples = out.samples || [];
      if (box) box.textContent = prompt || PREVIEW_EMPTY;
      if (meta) {
        const learned = out.learned || [];
        const fromPool = learned.filter((item) => item && item.source === "pool").length;
        const sourceText = fromPool ? "她的池 " + fromPool + " 条" : "固定池兜底";
        meta.textContent = "只读预览 · 场景 " + (scene || "不指定") + " · 情绪 " + (mood || "不指定") +
          " · 命中语料 " + samples.length + " 条 · " + sourceText + "（只学语气，不逐字复用）";
      }
    } catch (error) {
      if (box) box.textContent = PREVIEW_HINT;
      if (meta) meta.textContent = "";
      toastMsg("预览失败：" + (error.message || error), true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  function bind() {
    const actionToggle = el("personaActionToggle");
  if (actionToggle) {
    actionToggle.addEventListener("click", async () => {
      const next = actionToggle.dataset.enabled !== "1";
      try {
        render(await callApi("persona/auto", { key: "action_narration", enabled: next }));
        toastMsg(next ? "动作描写已开（一条最多一句短动作）" : "动作描写已关");
      } catch (error) {
        toastMsg("切换失败：" + (error.message || error), true);
      }
    });
  }

  const dupBtn = el("personaCopy");
  if (dupBtn) dupBtn.addEventListener("click", copyProfile);

  const importBtn = el("personaImport");
    const exportBtn = el("personaExport");
    const copyBtn = el("personaCopy");
    const downloadBtn = el("personaDownload");
    const fileBtn = el("personaFile");
    const fileInput = el("personaFileInput");
    const previewBtn = el("personaPreview");
    const autoBtn = el("personaAutoToggle");
    if (autoBtn) autoBtn.addEventListener("click", toggleAuto);
    if (importBtn) importBtn.addEventListener("click", () => {
      const box = el("personaJson");
      importPack(box ? box.value : "");
    });
    if (exportBtn) exportBtn.addEventListener("click", exportCurrent);
    if (copyBtn) copyBtn.addEventListener("click", copyJson);
    if (downloadBtn) downloadBtn.addEventListener("click", downloadJson);
    if (fileBtn && fileInput) {
      fileBtn.addEventListener("click", () => fileInput.click());
      fileInput.addEventListener("change", () => {
        const file = fileInput.files && fileInput.files[0];
        fileInput.value = "";
        if (!file) return;
        const reader = new FileReader();
        reader.onload = () => importPack(String(reader.result || ""));
        reader.onerror = () => toastMsg("读取文件失败", true);
        reader.readAsText(file);
      });
    }
    if (previewBtn) previewBtn.addEventListener("click", preview);
    ["personaScene", "personaMood"].forEach((id) => {
      const input = el(id);
      if (input) input.addEventListener("input", () => {
        markScene(null);
        invalidatePreview("参数已改");
      });
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    bind();
    renderScenes();
    const tab = document.querySelector('[data-tab="persona"]');
    if (tab) tab.addEventListener("click", load);
    load();
  });
})();
