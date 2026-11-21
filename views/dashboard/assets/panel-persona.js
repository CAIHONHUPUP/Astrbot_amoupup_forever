/* 人设面板：当前人设 / 切换 / 候选 / 导入导出 / 预览（2026-11-21） */
(function () {
  function unwrap(result) {
    if (!result) return {};
    return result.data || result;
  }

  async function callApi(action, payload) {
    if (typeof window.call !== "function") throw new Error("API 未加载");
    return unwrap(await window.call(action, payload || {}));
  }

  function toastMsg(text, isError) {
    if (typeof window.toast === "function") window.toast(text, !!isError);
  }

  function renderProfiles(data) {
    const box = document.getElementById("personaProfiles");
    if (!box) return;
    box.textContent = "";
    (data.profiles || []).forEach((pid) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = pid === data.active ? "● " + pid : pid;
      button.disabled = pid === data.active;
      button.addEventListener("click", () => switchProfile(pid));
      box.appendChild(button);
    });
  }

  function renderCandidates(data) {
    const box = document.getElementById("personaCandidates");
    if (!box) return;
    box.textContent = "";
    const rows = data.candidates || [];
    if (!rows.length) {
      box.textContent = "没有待批准的新语料。";
      return;
    }
    rows.forEach((text, index) => {
      const row = document.createElement("div");
      row.className = "persona-candidate";
      const span = document.createElement("span");
      span.textContent = text;
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "通过";
      button.addEventListener("click", () => approve(index));
      row.appendChild(span);
      row.appendChild(button);
      box.appendChild(row);
    });
  }

  function render(data) {
    const current = document.getElementById("personaCurrent");
    if (current) {
      current.textContent = (data.name || data.active) + " · " + (data.core || "") +
        "（语料 " + (data.corpus_count || 0) + "，进化 " + (data.evolved || 0) + "）";
    }
    renderProfiles(data);
    renderCandidates(data);
  }

  async function load() {
    try {
      render(await callApi("persona/status"));
    } catch (error) {
      toastMsg("读取人设失败：" + (error.message || error), true);
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
      toastMsg("已通过");
    } catch (error) {
      toastMsg("通过失败：" + (error.message || error), true);
    }
  }

  async function importPack(text) {
    let pack;
    try {
      pack = JSON.parse(String(text || ""));
    } catch (error) {
      toastMsg("JSON 解析失败", true);
      return;
    }
    try {
      const out = await callApi("persona/import", { pack });
      render(out);
      toastMsg("导入成功");
    } catch (error) {
      toastMsg("导入失败：" + (error.message || error), true);
    }
  }

  async function exportCurrent() {
    try {
      const out = await callApi("persona/export", {});
      const box = document.getElementById("personaJson");
      if (box) box.value = JSON.stringify(out, null, 2);
      toastMsg("已导出到文本框");
    } catch (error) {
      toastMsg("导出失败：" + (error.message || error), true);
    }
  }

  async function preview() {
    const scene = (document.getElementById("personaScene") || {}).value || "";
    const mood = (document.getElementById("personaMood") || {}).value || "";
    try {
      const out = await callApi("persona/preview", { scene, mood });
      const box = document.getElementById("personaPreviewBox");
      if (box) {
        box.textContent = String(out.prompt || "") + "\n\n--- few-shot ---\n" +
          (out.samples || []).join("\n");
      }
    } catch (error) {
      toastMsg("预览失败：" + (error.message || error), true);
    }
  }

  function bind() {
    const importBtn = document.getElementById("personaImport");
    const exportBtn = document.getElementById("personaExport");
    const fileBtn = document.getElementById("personaFile");
    const fileInput = document.getElementById("personaFileInput");
    const previewBtn = document.getElementById("personaPreview");
    if (importBtn) importBtn.addEventListener("click", () => {
      const box = document.getElementById("personaJson");
      importPack(box ? box.value : "");
    });
    if (exportBtn) exportBtn.addEventListener("click", exportCurrent);
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
  }

  document.addEventListener("DOMContentLoaded", () => {
    bind();
    const tab = document.querySelector('[data-tab="persona"]');
    if (tab) tab.addEventListener("click", load);
    load();
  });
})();