/* 用户三视图（合拍用）：2026-11-21 */
(function () {
  const VIEWS = ["front", "side", "full"];
  const LABELS = { front: "正面", side: "侧面", full: "全身" };
  const selected = { front: "", side: "", full: "" };

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

  function previewBox(src, onPick, checked) {
    const wrap = document.createElement("label");
    wrap.style.cssText = "display:inline-flex;flex-direction:column;gap:4px;align-items:center;cursor:pointer";
    if (src) {
      const img = document.createElement("img");
      img.src = src;
      img.style.cssText = "max-height:120px;border-radius:8px;border:1px solid #eadfe5";
      wrap.appendChild(img);
    }
    if (onPick) {
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = !!checked;
      box.addEventListener("change", () => onPick(box.checked, wrap));
      wrap.appendChild(box);
    }
    return wrap;
  }

  function render(info) {
    const box = document.getElementById("userViews");
    if (!box) return;
    const templates = (info && info.templates) || {};
    const candidates = (info && info.candidates) || {};
    box.textContent = "";
    VIEWS.forEach((view) => {
      const row = document.createElement("div");
      row.style.cssText = "margin-top:12px;padding:10px;border:1px solid #eadfe5;border-radius:12px";
      const title = document.createElement("div");
      title.className = "label";
      title.textContent = LABELS[view];
      row.appendChild(title);

      const gen = document.createElement("button");
      gen.type = "button";
      gen.textContent = "生成这个视角";
      gen.style.marginTop = "8px";
      gen.addEventListener("click", () => generate(view));
      row.appendChild(gen);

      const candTitle = document.createElement("div");
      candTitle.className = "sub";
      candTitle.textContent = "生成预览（勾选一张后保存）";
      candTitle.style.marginTop = "6px";
      row.appendChild(candTitle);
      const candBox = document.createElement("div");
      candBox.style.cssText = "display:flex;gap:10px;flex-wrap:wrap;min-height:50px;padding:6px;border:1px dashed #d9c3cf;border-radius:10px;margin-top:4px";
      const cand = candidates[view];
      if (cand && (cand.preview || cand.url)) {
        const node = previewBox(cand.preview || cand.url, (checked) => { if (checked) selected[view] = cand.file || cand.url || cand.preview; }, true);
        selected[view] = cand.file || cand.url || cand.preview;
        candBox.appendChild(node);
      } else {
        const empty = document.createElement("div");
        empty.className = "sub";
        empty.textContent = "还没有生成";
        candBox.appendChild(empty);
      }
      row.appendChild(candBox);

      const save = document.createElement("button");
      save.type = "button";
      save.textContent = "保存这个视角的模板";
      save.style.marginTop = "8px";
      save.addEventListener("click", () => saveView(view));
      row.appendChild(save);

      const tplTitle = document.createElement("div");
      tplTitle.className = "sub";
      tplTitle.textContent = "模板预览（勾选 = 启用这个视角）";
      tplTitle.style.marginTop = "8px";
      row.appendChild(tplTitle);
      const tplBox = document.createElement("div");
      tplBox.style.cssText = "display:flex;gap:10px;flex-wrap:wrap;min-height:50px;padding:6px;border:1px dashed #cbd8d0;border-radius:10px;margin-top:4px";
      const tpl = templates[view] || {};
      if (tpl.preview) {
        tplBox.appendChild(previewBox(tpl.preview, (checked) => toggleView(view, checked), tpl.enabled));
      } else {
        const empty = document.createElement("div");
        empty.className = "sub";
        empty.textContent = "这个视角还没有模板";
        tplBox.appendChild(empty);
      }
      row.appendChild(tplBox);

      const actions = document.createElement("div");
      actions.style.cssText = "display:flex;gap:8px;flex-wrap:wrap;margin-top:8px";
      const imp = document.createElement("button");
      imp.type = "button";
      imp.textContent = "导入成品";
      imp.className = "ghost";
      imp.addEventListener("click", () => importView(view));
      actions.appendChild(imp);
      const del = document.createElement("button");
      del.type = "button";
      del.textContent = "删除这个视角的模板";
      del.addEventListener("click", () => deleteView(view));
      actions.appendChild(del);
      row.appendChild(actions);
      box.appendChild(row);
    });
    const status = document.getElementById("userSheetStatus");
    if (status) status.textContent = "";
  }

  async function load() {
    try {
      const info = await callApi("photo/user/status");
      render(info);
      const ref = (info && info.ref) || {};
      const thumb = document.getElementById("userRefThumb");
      const status = document.getElementById("userRefStatus");
      const clear = document.getElementById("userRefClear");
      if (thumb) {
        thumb.src = ref.preview || "";
        thumb.style.display = ref.preview ? "" : "none";
      }
      if (status) status.textContent = ref.has_ref ? "已设置参考图" : "未设置参考图（会按描述生成）";
      if (clear) clear.hidden = !ref.has_ref;
    } catch (error) {
      toastMsg("加载用户三视图失败：" + (error.message || error), true);
    }
  }

  async function generate(view) {
    const status = document.getElementById("userSheetStatus");
    try {
      if (status) status.textContent = "生成中…";
      await callApi("photo/user/sheet", { view });
      for (let i = 0; i < 90; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        const info = await callApi("photo/user/status");
        if ((info.candidates || {})[view]) {
          render(info);
          toastMsg("已生成" + LABELS[view]);
          return;
        }
      }
      toastMsg("生成超时，看日志", true);
    } catch (error) {
      toastMsg("生成失败：" + (error.message || error), true);
    } finally {
      if (status) status.textContent = "";
    }
  }

  async function saveView(view) {
    if (!selected[view]) { toastMsg("先勾选一张生成预览", true); return; }
    try {
      const r = await callApi("photo/user/save", { view, url: selected[view] });
      toastMsg(r.msg || "已保存");
      await load();
    } catch (error) {
      toastMsg("保存失败：" + (error.message || error), true);
    }
  }

  async function toggleView(view, enabled) {
    try {
      const r = await callApi("photo/user/toggle", { view, enabled });
      toastMsg(r.msg || "已更新");
    } catch (error) {
      toastMsg("更新失败：" + (error.message || error), true);
    }
  }

  async function deleteView(view) {
    if (!window.confirm("确定删除用户" + LABELS[view] + "模板？")) return;
    try {
      const r = await callApi("photo/user/delete", { view });
      toastMsg(r.msg || "已删除");
      await load();
    } catch (error) {
      toastMsg("删除失败：" + (error.message || error), true);
    }
  }

  function importView(view) {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.addEventListener("change", async () => {
      const file = input.files && input.files[0];
      if (!file) return;
      try {
        const data = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result || ""));
          reader.onerror = reject;
          reader.readAsDataURL(file);
        });
        const r = await callApi("photo/user/import", { view, data });
        toastMsg(r.msg || "已导入");
        await load();
      } catch (error) {
        toastMsg("导入失败：" + (error.message || error), true);
      }
    });
    input.click();
  }

  function bindRef() {
    const pick = document.getElementById("userRefPick");
    const file = document.getElementById("userRefFile");
    const clear = document.getElementById("userRefClear");
    if (pick && file) {
      pick.addEventListener("click", () => file.click());
      file.addEventListener("change", async () => {
        const item = file.files && file.files[0];
        file.value = "";
        if (!item) return;
        try {
          const data = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result || ""));
            reader.onerror = reject;
            reader.readAsDataURL(item);
          });
          const r = await callApi("photo/user/ref/save", { data });
          toastMsg(r.msg || "已保存");
          await load();
        } catch (error) {
          toastMsg("上传失败：" + (error.message || error), true);
        }
      });
    }
    if (clear) {
      clear.addEventListener("click", async () => {
        try {
          await callApi("photo/user/ref/save", { clear: true });
          await load();
        } catch (error) {
          toastMsg("清除失败：" + (error.message || error), true);
        }
      });
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    bindRef();
    const nav = document.querySelector('#photoNav [data-photo-pane="user"]');
    if (nav) nav.addEventListener("click", load, { once: false });
    load();
  });
})();
