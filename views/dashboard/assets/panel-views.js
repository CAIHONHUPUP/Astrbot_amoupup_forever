/* 面板展示页：照片 / 设置 / 用户 / 关系图谱；通信层在 assets/api-client.js。 */
    let photoItems = [];
    const PHOTO_PROB_KEYS = new Set([
      "photo_outfit_prob", "photo_travel_prob", "photo_shopping_prob",
      "photo_weekend_prob", "photo_daily_prob", "photo_selfie_ratio",
    ]);

    function photoControl(item) {
      const wrap = document.createElement("div");
      wrap.className = "control";
      wrap.style.display = "flex";
      wrap.style.alignItems = "center";
      wrap.style.gap = "8px";
      const type = item.type || "string";
      const key = item.key;
      if (type === "bool") {
        const input = document.createElement("input");
        input.type = "checkbox";
        input.dataset.key = key;
        input.dataset.type = "bool";
        input.checked = !!item.value;
        wrap.appendChild(input);
        return wrap;
      }
      if (Array.isArray(item.options) && item.options.length) {
        const select = document.createElement("select");
        select.className = "input";
        select.dataset.key = key;
        select.dataset.type = "string";
        const labels = { topic: "话题触发", explicit: "明确要图", both: "话题+明确" };
        item.options.forEach((opt) => {
          const option = document.createElement("option");
          option.value = opt;
          option.textContent = labels[opt] || opt;
          select.appendChild(option);
        });
        select.value = item.value === undefined || item.value === null ? "" : item.value;
        wrap.appendChild(select);
        return wrap;
      }
      if (PHOTO_PROB_KEYS.has(key)) {
        const range = document.createElement("input");
        range.type = "range";
        range.min = "0";
        range.max = "100";
        range.step = "5";
        range.value = Number(item.value) || 0;
        range.style.width = "150px";
        range.dataset.key = key;
        range.dataset.type = "int";
        const out = document.createElement("span");
        out.className = "sub";
        out.textContent = range.value + "%";
        range.addEventListener("input", () => { out.textContent = range.value + "%"; });
        wrap.appendChild(range);
        wrap.appendChild(out);
        return wrap;
      }
      const input = document.createElement("input");
      input.className = "input";
      input.style.width = "110px";
      input.dataset.key = key;
      input.dataset.type = (type === "int" || type === "float") ? type : "string";
      input.value = item.value === undefined || item.value === null ? "" : item.value;
      if (type === "int" || type === "float") input.type = "number";
      wrap.appendChild(input);
      return wrap;
    }

    function renderPhoto() {
      const box = document.getElementById("photoSettings");
      if (!box) return;
      box.textContent = "";
      if (!photoItems.length) {
        box.textContent = "没有照片配置（检查 _conf_schema.json）";
        return;
      }
      photoItems.forEach((item) => {
        if (["photo_persona_ref", "photo_persona_refs", "photo_persona_enabled"].includes(String(item.key))) return;
        const row = document.createElement("div");
        row.className = "setting-row";
        const left = document.createElement("div");
        const name = document.createElement("div");
        name.className = "name";
        name.textContent = item.description || item.key;
        left.appendChild(name);
        if (item.hint) {
          const hint = document.createElement("div");
          hint.className = "hint";
          hint.textContent = item.hint;
          left.appendChild(hint);
        }
        row.appendChild(left);
        row.appendChild(photoControl(item));
        box.appendChild(row);
      });
    }

    async function loadPhoto() {
      try {
        const data = objectFrom(await call("config"));
        const items = Array.isArray(data.items) ? data.items : [];
        photoItems = items.filter((item) => item.group === "照片" && (
          String(item.key).startsWith("photo_") || String(item.key).startsWith("draw_")
        ));
        renderPhoto();
        loadPhotoTemplate();
        loadPhotoPromptSaved();
        const status = document.getElementById("photoStatus");
        if (status) status.textContent = "";
      } catch (error) {
        const box = document.getElementById("photoSettings");
        if (box) box.textContent = "读取失败：" + (error.message || error);
      }
    }

    function photoControlValue(el) {
      const type = el.dataset.type;
      if (type === "bool") return !!el.checked;
      if (type === "int" || type === "float") return Number(el.value);
      return el.value;
    }

    async function savePhoto() {
      const changes = {};
      document.querySelectorAll("#photoSettings [data-key]").forEach((el) => {
        changes[el.dataset.key] = photoControlValue(el);
      });
      const status = document.getElementById("photoStatus");
      try {
        await call("config_save", { changes });
        if (status) status.textContent = "已保存";
        toast("照片设置已保存");
      } catch (error) {
        if (status) status.textContent = "保存失败";
        toast("保存失败：" + (error.message || error), true);
      }
    }

    async function savePhotoAll() {
      const status = document.getElementById("photoSaveAllStatus");
      const cardStatus = document.getElementById("photoStatus");
      const saveBtn = document.getElementById("photoSaveAll");
      if (saveBtn) saveBtn.disabled = true;
      const done = [];
      const failed = [];
      const renderStatus = () => {
        if (!status) return;
        status.textContent = failed.length
          ? "部分失败：" + failed.join("；")
          : done.length
            ? "已保存：" + done.join(" / ")
            : "没有读到要保存的东西，先点右上角「刷新」";
      };

      // 1) 照片分享的配置项：和卡片里那颗「保存」走同一个接口
      const changes = {};
      document.querySelectorAll("#photoSettings [data-key]").forEach((el) => {
        if (el.dataset.key) changes[el.dataset.key] = photoControlValue(el);
      });
      if (Object.keys(changes).length) {
        try {
          const result = objectFrom(await call("config_save", { changes }));
          if (result && result.code && result.code !== 0) throw new Error(result.msg || "保存失败");
          if (cardStatus) cardStatus.textContent = "已保存";
          done.push("照片设置 " + Object.keys(changes).length + " 项");
        } catch (error) {
          if (cardStatus) cardStatus.textContent = "保存失败";
          failed.push("照片设置（" + (error.message || error) + "）");
        }
        renderStatus();
      }

      // 2) 「按提示词生成」里上次写的提示词
      const promptBox = document.getElementById("photoPrompt");
      try {
        await call("photo/prompt_saved", { text: promptBox ? promptBox.value.trim() : "" });
        done.push("提示词");
      } catch (error) {
        failed.push("提示词（" + (error.message || error) + "）");
      }
      renderStatus();

      // 3) 模板保存位置：没改就不写，免得白白重刷一遍三视图
      const dirBox = document.getElementById("photoTemplateDir");
      const dir = dirBox ? dirBox.value.trim() : "";
      if (dirBox && dir !== photoTemplateDirSaved) {
        try {
          await call("photo/template_dir", { dir });
          photoTemplateDirSaved = dir;
          await loadPhotoTemplate();
          done.push("模板位置");
        } catch (error) {
          failed.push("模板位置（" + (error.message || error) + "）");
        }
        renderStatus();
      }

      if (saveBtn) saveBtn.disabled = false;
      toast(failed.length ? "有东西没存上，看按钮旁边的提示" : "这一栏已全部保存", !!failed.length);
    }

    async function pollPhotoJob(jobId, onDone, onError) {
      for (let i = 0; i < 90; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        try {
          const res = objectFrom(await call("photo/job", { id: jobId }));
          if (!res) continue;
          if (res.code && res.code !== 0) {
            onError(res.msg || "任务不存在（插件可能重载过，重新生成一次）");
            return;
          }
          if (!res.status) {
            onError("任务状态异常，重新生成一次");
            return;
          }
          if (res.status === "done") {
            onDone(res.data || {}, res.msg || "");
            return;
          }
          if (res.status === "error") {
            onError(res.msg || "生成失败");
            return;
          }
        } catch (error) {
          // 桥接偶尔超时，继续轮询
        }
      }
      onError("生成超时（服务端可能还在画，看日志）");
    }

    async function testPhoto() {
      const kind = (document.getElementById("photoTestKind") || {}).value || "daily";
      const status = document.getElementById("photoStatus");
      if (status) status.textContent = "正在发…";
      try {
        const res = objectFrom(await call("photo/test", { category: kind }));
        const jobId = (res && (res.job || (res.data && res.data.job))) || "";
        if (!jobId) throw new Error("没有拿到任务号");
        if (status) status.textContent = "后台生成中…";
        await pollPhotoJob(jobId, (data, msg) => {
          const text = (data && data.msg) || msg || "已触发";
          if (status) status.textContent = text;
          toast(text);
        }, (msg) => {
          if (status) status.textContent = "失败";
          toast("发送失败：" + msg, true);
        });
      } catch (error) {
        if (status) status.textContent = "发送失败";
        toast("发送失败：" + (error.message || error), true);
      }
    }

    async function loadPhotoPromptSaved() {
      try {
        const data = objectFrom(await call("photo/prompt_saved", {}));
        const input = document.getElementById("photoPrompt");
        if (input && !input.value) input.value = (data && data.text) || "";
      } catch (error) {
        // 接口没就绪时忽略
      }
    }

    async function savePhotoPrompt() {
      const input = document.getElementById("photoPrompt");
      const text = input ? input.value.trim() : "";
      try {
        await call("photo/prompt_saved", { text });
        toast("提示词已保存");
      } catch (error) {
        toast("保存失败：" + (error.message || error), true);
      }
    }

    async function clearPhotoPrompt() {
      const input = document.getElementById("photoPrompt");
      if (input) input.value = "";
      try {
        await call("photo/prompt_saved", { text: "" });
        toast("已清空");
      } catch (error) {
        toast("清空失败：" + (error.message || error), true);
      }
    }

    async function generatePhotoPrompt() {
      const input = document.getElementById("photoPrompt");
      const status = document.getElementById("photoGenStatus");
      const preview = document.getElementById("photoGenPreview");
      const prompt = (input.value || "").trim();
      if (!prompt) {
        toast("先写提示词", true);
        return;
      }
      if (status) status.textContent = "后台生成中（约 40-90 秒）…";
      if (preview) preview.textContent = "";
      try {
        const sendBox = document.getElementById("photoPromptSend");
        const res = objectFrom(await call("photo/prompt", {
          prompt,
          send: !!(sendBox && sendBox.checked),
        }));
        const jobId = (res && (res.job || (res.data && res.data.job))) || "";
        if (!jobId) throw new Error("没有拿到任务号");
        await pollPhotoJob(jobId, (data, msg) => {
          const url = (data && data.url) || "";
          const previewUrl = (data && data.preview) || url;
          if (status) status.textContent = (data && data.msg) || msg || "完成";
          if (preview && previewUrl) {
            const img = document.createElement("img");
            img.src = previewUrl;
            img.style.maxWidth = "100%";
            img.style.borderRadius = "12px";
            preview.appendChild(img);
          }
        }, (msg) => {
          if (status) status.textContent = "失败";
          toast("生成失败：" + msg, true);
        });
      } catch (error) {
        if (status) status.textContent = "失败";
        toast("生成失败：" + (error.message || error), true);
      }
    }

    const PHOTO_VIEW_LABELS = { front: "正面", side: "侧面", full: "全身" };
    const photoSelected = { front: "", side: "", full: "" };
    let photoTemplates = { front: {}, side: {}, full: {} };
    let photoTemplateDirSaved = "";

    function photoCard(src, options) {
      const card = document.createElement("div");
      card.dataset.photoCard = "1";
      card.style.cssText = "position:relative;width:150px;padding:3px;border:2px solid transparent;border-radius:10px;cursor:pointer;opacity:0.9";
      const img = document.createElement("img");
      img.src = src;
      img.style.cssText = "width:100%;border-radius:10px;display:block";
      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = !!options.checked;
      check.style.cssText = "position:absolute;top:8px;right:8px;width:20px;height:20px;z-index:2;cursor:pointer";
      const label = document.createElement("div");
      label.className = "sub";
      label.textContent = options.label || "";
      check.addEventListener("click", (event) => event.stopPropagation());
      check.addEventListener("change", () => options.onCheck(check.checked, card, check));
      card.addEventListener("click", () => {
        if (options.isTemplate) {
          check.checked = !check.checked;
          options.onCheck(check.checked, card, check);
        } else {
          options.onCheck(true, card, check);
        }
      });
      card.appendChild(check);
      card.appendChild(img);
      card.appendChild(label);
      return card;
    }

    function refreshPhotoSaveState() {
      document.querySelectorAll("[data-save-view]").forEach((button) => {
        button.disabled = !photoSelected[button.dataset.saveView];
      });
    }

    function selectPhotoCandidate(view, url, card, check) {
      photoSelected[view] = url;
      const box = document.querySelector('[data-cand="' + view + '"]');
      if (box) {
        box.querySelectorAll("[data-photo-card]").forEach((item) => {
          item.style.borderColor = "transparent";
          item.style.opacity = "0.9";
          const input = item.querySelector("input");
          if (input) input.checked = false;
        });
      }
      card.style.borderColor = "#e06a9a";
      card.style.opacity = "1";
      if (check) check.checked = true;
      refreshPhotoSaveState();
    }

    function renderPhotoViews(info) {
      const box = document.getElementById("photoViews");
      if (!box) return;
      const templates = (info && info.templates) || {};
      photoTemplates = templates;
      box.textContent = "";
      ["front", "side", "full"].forEach((view) => {
        const tpl = templates[view] || {};
        photoSelected[view] = "";
        const row = document.createElement("div");
        row.style.cssText = "margin-top:12px;padding:10px;border:1px solid #eadfe5;border-radius:12px";
        const title = document.createElement("div");
        title.className = "label";
        title.textContent = PHOTO_VIEW_LABELS[view];
        row.appendChild(title);

        const candTitle = document.createElement("div");
        candTitle.className = "sub";
        candTitle.textContent = "生成预览（勾选一张后保存）";
        row.appendChild(candTitle);
        const candBox = document.createElement("div");
        candBox.dataset.cand = view;
        candBox.style.cssText = "display:flex;gap:10px;flex-wrap:wrap;min-height:60px;padding:6px;border:1px dashed #d9c3cf;border-radius:10px;margin-top:4px";
        const candEmpty = document.createElement("div");
        candEmpty.className = "sub";
        candEmpty.textContent = "还没有生成，点上面的按钮生成";
        candBox.appendChild(candEmpty);
        const savedCandidate = (info && info.candidates && info.candidates[view]) || null;
        if (savedCandidate && savedCandidate.preview) {
          candBox.textContent = "";
          const candCard = photoCard(savedCandidate.preview, {
            label: PHOTO_VIEW_LABELS[view] || view,
            checked: true,
            onCheck: (checked, element) => {
              if (checked) {
                selectPhotoCandidate(view, savedCandidate.url || savedCandidate.file, element, element.querySelector("input"));
              }
            },
          });
          candBox.appendChild(candCard);
          selectPhotoCandidate(view, savedCandidate.url || savedCandidate.file, candCard, candCard.querySelector("input"));
        }
        row.appendChild(candBox);
        const viewSave = document.createElement("button");
        viewSave.type = "button";
        viewSave.dataset.saveView = view;
        viewSave.textContent = "保存这个视角的模板";
        viewSave.style.marginTop = "8px";
        viewSave.disabled = !photoSelected[view];
        viewSave.addEventListener("click", () => savePhotoPersona(view));
        row.appendChild(viewSave);

        const tplTitle = document.createElement("div");
        tplTitle.className = "sub";
        tplTitle.style.marginTop = "8px";
        tplTitle.textContent = "模板预览（勾选 = 启用这个视角）";
        row.appendChild(tplTitle);
        const tplBox = document.createElement("div");
        tplBox.dataset.tpl = view;
        tplBox.style.cssText = "display:flex;gap:10px;flex-wrap:wrap;min-height:60px;padding:6px;border:1px dashed #cbd8d0;border-radius:10px;margin-top:4px";
        if (tpl.preview) {
          tplBox.appendChild(photoCard(tpl.preview, {
            isTemplate: true,
            checked: !!tpl.enabled,
            label: "模板",
            onCheck: (checked) => togglePhotoTemplate(view, checked),
          }));
        } else {
          const empty = document.createElement("div");
          empty.className = "sub";
          empty.textContent = "这个视角还没有模板";
          tplBox.appendChild(empty);
        }
        row.appendChild(tplBox);

        const del = document.createElement("button");
        del.type = "button";
        del.textContent = "删除这个视角的模板";
        del.style.marginTop = "8px";
        del.addEventListener("click", () => deletePhotoTemplate(view));
        row.appendChild(del);
        box.appendChild(row);
      });
    }

    async function togglePhotoTemplate(view, enabled) {
      try {
        const r = objectFrom(await call("photo/persona/toggle", { view, enabled }));
        toast((r && r.msg) || "已更新");
      } catch (error) {
        toast("更新失败：" + (error.message || error), true);
      }
    }

    async function deletePhotoTemplate(view) {
      if (!window.confirm("确定删除" + (PHOTO_VIEW_LABELS[view] || view) + "模板？")) return;
      try {
        const r = objectFrom(await call("photo/persona/delete", { view }));
        toast((r && r.msg) || "已删除");
        await loadPhotoTemplate();
      } catch (error) {
        toast("删除失败：" + (error.message || error), true);
      }
    }

    async function savePhotoPersona(view) {
      const url = photoSelected[view];
      if (!url) {
        toast("先勾选一张候选图", true);
        return;
      }
      try {
        const r = objectFrom(await call("photo/persona", { url, view }));
        if (r && r.code && r.code !== 0) throw new Error(r.msg || "保存失败");
        toast("模板已保存");
        photoSelected[view] = "";
        const candBox = document.querySelector('[data-cand="' + view + '"]');
        if (candBox) candBox.innerHTML = '<div class="sub">已保存为模板，重新生成可换</div>';
        refreshPhotoSaveState();
        await loadPhotoTemplate();
      } catch (error) {
        toast("保存失败：" + (error.message || error), true);
      }
    }

    async function generatePhotoSheet(view) {
      const status = document.getElementById("photoSheetStatus");
      const candBox = document.querySelector('[data-cand="' + view + '"]');
      if (status) status.textContent = "后台生成中（约 40-90 秒）…";
      if (candBox) candBox.innerHTML = '<div class="sub">生成中…</div>';
      photoSelected[view] = "";
      refreshPhotoSaveState();
      try {
        const res = objectFrom(await call("photo/sheet", { view }));
        const jobId = (res && (res.job || (res.data && res.data.job))) || "";
        if (!jobId) throw new Error("没有拿到任务号");
        await pollPhotoJob(jobId, (data) => {
          const url = (data && data.url) || "";
          const preview = (data && data.preview) || url;
          if (status) status.textContent = (data && data.msg) || "完成";
          if (!url || !candBox) return;
          candBox.textContent = "";
          const card = photoCard(preview, {
            label: PHOTO_VIEW_LABELS[view] || view,
            onCheck: (checked, element) => {
              if (checked) selectPhotoCandidate(view, url, element, element.querySelector("input"));
            },
          });
          candBox.appendChild(card);
          selectPhotoCandidate(view, url, card, card.querySelector("input"));
        }, (msg) => {
          if (status) status.textContent = "失败";
          if (candBox) candBox.innerHTML = '<div class="sub">生成失败，重新点一次生成</div>';
          toast("生成失败：" + msg, true);
        });
      } catch (error) {
        if (status) status.textContent = "失败";
        toast("生成失败：" + (error.message || error), true);
      }
    }

    async function savePhotoTemplateDir() {
      const input = document.getElementById("photoTemplateDir");
      const dir = input ? input.value.trim() : "";
      try {
        await call("photo/template_dir", { dir });
        toast("模板位置已保存");
        await loadPhotoTemplate();
      } catch (error) {
        toast("保存失败：" + (error.message || error), true);
      }
    }

    async function openPhotoTemplateDir() {
      try {
        const data = objectFrom(await call("photo/template_open", {}));
        const root = (data && data.root) || "";
        let copied = false;
        if (root && navigator.clipboard && navigator.clipboard.writeText) {
          try {
            await navigator.clipboard.writeText(root);
            copied = true;
          } catch (error) {
            copied = false;
          }
        }
        const message = copied
          ? "已尝试打开；没弹窗的话路径已复制，可粘贴到资源管理器"
          : "已尝试打开";
        toast(message + "\n" + root, !(data && data.opened));
      } catch (error) {
        toast("打开失败：" + (error.message || error), true);
      }
    }

    async function loadPhotoTemplate() {
      try {
        const info = objectFrom(await call("photo/template_dir", {}));
        const input = document.getElementById("photoTemplateDir");
        if (input) input.value = (info && info.configured) || "";
        photoTemplateDirSaved = (info && info.configured) || "";
        const line = document.getElementById("photoTemplateRoot");
        if (line) {
          const root = (info && info.root) || "";
          line.textContent = root
            ? "保存位置：" + root + (info.inside_plugin ? "（跟插件备份）" : "（不在插件目录，不跟插件备份）")
            : "";
        }
        renderPhotoViews(info || {});
      } catch (error) {
        // 接口没就绪时忽略
      }
    }

    function objectFrom(res) {
      if (res && res.data && typeof res.data === "object" && !Array.isArray(res.data)) return res.data;
      return res && typeof res === "object" ? res : {};
    }

    const tabButtons = Array.from(document.querySelectorAll(".tab[data-tab]"));
    let settingsGroupFilter = "基础";
    function showTab(name, group, activeButton) {
      if (!name) return;
      tabButtons.forEach((button) => {
        if (activeButton) button.classList.toggle("active", button === activeButton);
        else button.classList.toggle("active", button.dataset.tab === name && button.dataset.group === undefined);
      });
      document.querySelectorAll(".tab-panel").forEach((panel) => {
        panel.hidden = panel.id !== "tab-" + name;
      });
      if (name === "world") { loadWorld(); }
      if (name === "schedule") loadSchedule();
      if (name === "profiles") loadProfiles();
      if (name === "gift") loadGift();
      if (name === "wardrobe") { loadWardrobe(); loadPoses(); }
      if (name === "photo") loadPhoto();
      if (name === "diary") loadDiary();
      if (name === "usage") loadUsage();
      if (name === "settings") {
        if (group) settingsGroupFilter = group;
        if (!configItems.length) loadSettings();
        else renderSettings();
      }
    }
    tabButtons.forEach((button) => button.addEventListener("click", () => showTab(button.dataset.tab, button.dataset.group, button)));
    const usageRefreshButton = document.getElementById("usageRefresh");
    if (usageRefreshButton) usageRefreshButton.addEventListener("click", () => loadUsage(true));
    const giftRefreshButton = document.getElementById("giftRefresh");
    if (giftRefreshButton) giftRefreshButton.addEventListener("click", () => loadGift());
    const wardrobeRefreshButton = document.getElementById("wardrobeRefresh");
    if (wardrobeRefreshButton) wardrobeRefreshButton.addEventListener("click", () => loadWardrobe());
    async function pollWardrobeRefs() {
      try {
        const res = await call("wardrobe/refs/status", {});
        const st = (res && res.data) || {};
        if (st.running) {
          toast("参考图生成中：" + (Number(st.done) || 0) + "/" + (Number(st.total) || 0)
                + "（" + (st.current || "") + "）");
          setTimeout(pollWardrobeRefs, 5000);
        } else if (Number(st.total)) {
          toast("参考图生成完成：" + (Number(st.done) || 0) + "/" + (Number(st.total) || 0));
        }
      } catch (error) { /* 轮询失败不打扰 */ }
    }

    const wardrobeRefsButton = document.getElementById("wardrobeRefsBtn");
    if (wardrobeRefsButton) wardrobeRefsButton.addEventListener("click", async () => {
      try {
        const res = await call("wardrobe/refs", {});
        toast((res && res.msg) || "参考图任务已处理");
        if (res && res.data && res.data.running) pollWardrobeRefs();
      } catch (error) {
        toast("生成参考图失败：" + (error && error.message ? error.message : error), true);
      }
    });
    const wardrobeRerollButton = document.getElementById("wardrobeReroll");
    if (wardrobeRerollButton) wardrobeRerollButton.addEventListener("click", () => rerollWardrobe());
    const wardrobeSaveButton = document.getElementById("wardrobeSave");
    if (wardrobeSaveButton) wardrobeSaveButton.addEventListener("click", () => saveWardrobeToday());
    const wardrobeModeDayButton = document.getElementById("wardrobeModeDay");
    if (wardrobeModeDayButton) wardrobeModeDayButton.addEventListener("click", () => openWardrobeMode("day"));
    const wardrobeModeNightButton = document.getElementById("wardrobeModeNight");
    if (wardrobeModeNightButton) wardrobeModeNightButton.addEventListener("click", () => openWardrobeMode("night"));
    const wardrobeModeHotelButton = document.getElementById("wardrobeModeHotel");
    if (wardrobeModeHotelButton) wardrobeModeHotelButton.addEventListener("click", () => openWardrobeMode("hotel"));
    const wardrobeModeSwimButton = document.getElementById("wardrobeModeSwim");
    if (wardrobeModeSwimButton) wardrobeModeSwimButton.addEventListener("click", () => openWardrobeMode("swim"));
    const wardrobeEditorCloseButton = document.getElementById("wardrobeEditorClose");
    if (wardrobeEditorCloseButton) wardrobeEditorCloseButton.addEventListener("click", () => closeWardrobeEditor());
    const poseOpenButton = document.getElementById("poseOpen");
    if (poseOpenButton) poseOpenButton.addEventListener("click", () => openPoseEditor());
    const poseSaveButton = document.getElementById("poseSave");
    if (poseSaveButton) poseSaveButton.addEventListener("click", () => savePoses());
    const poseCloseButton = document.getElementById("poseClose");
    if (poseCloseButton) poseCloseButton.addEventListener("click", () => closePoseEditor());
    const poseAddButton = document.getElementById("poseAdd");
    if (poseAddButton) poseAddButton.addEventListener("click", () => addPoseDraft());
    const poseRefsButton = document.getElementById("poseRefs");
    if (poseRefsButton) poseRefsButton.addEventListener("click", () => generatePoseRefs());
    const poseNameInput = document.getElementById("poseName");
    if (poseNameInput) poseNameInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); addPoseDraft(); }
    });
    document.querySelectorAll("[data-wardrobe-add]").forEach((button) => {
      button.addEventListener("click", () => openWardrobeForm(button.dataset.wardrobeAdd));
    });
    document.querySelectorAll("[data-wardrobe-confirm]").forEach((button) => {
      button.addEventListener("click", () => submitWardrobeItem(button.dataset.wardrobeConfirm));
    });
    document.querySelectorAll("[data-wardrobe-cancel]").forEach((button) => {
      button.addEventListener("click", () => closeWardrobeForm(button.dataset.wardrobeCancel));
    });
    WARDROBE_CATS.forEach((cat) => {
      const input = document.getElementById("wardrobeInput-" + cat.key);
      if (!input) return;
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          submitWardrobeItem(cat.key);
        } else if (event.key === "Escape") {
          closeWardrobeForm(cat.key);
        }
      });
    });
    function showPhotoPane(name) {
      const target = String(name || "share");
      document.querySelectorAll("#tab-photo .photo-pane").forEach((section) => {
        section.hidden = section.dataset.photoPane !== target;
      });
      document.querySelectorAll("#photoNav [data-photo-pane]").forEach((button) => {
        const active = button.dataset.photoPane === target;
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", active ? "true" : "false");
      });
    }
    document.querySelectorAll("#photoNav [data-photo-pane]").forEach((button) => {
      button.addEventListener("click", () => showPhotoPane(button.dataset.photoPane));
    });
    showPhotoPane("share");

    const photoRefreshButton = document.getElementById("photoRefresh");
    if (photoRefreshButton) photoRefreshButton.addEventListener("click", () => loadPhoto());
    const photoSaveButton = document.getElementById("photoSave");
    if (photoSaveButton) photoSaveButton.addEventListener("click", () => savePhoto());
    const photoSaveAllButton = document.getElementById("photoSaveAll");
    if (photoSaveAllButton) photoSaveAllButton.addEventListener("click", () => savePhotoAll());
    const photoTestButton = document.getElementById("photoTest");
    if (photoTestButton) photoTestButton.addEventListener("click", () => testPhoto());
    const photoGenButton = document.getElementById("photoGen");
    if (photoGenButton) photoGenButton.addEventListener("click", () => generatePhotoPrompt());
    const photoPromptSaveButton = document.getElementById("photoPromptSave");
    if (photoPromptSaveButton) photoPromptSaveButton.addEventListener("click", () => savePhotoPrompt());
    const photoPromptClearButton = document.getElementById("photoPromptClear");
    if (photoPromptClearButton) photoPromptClearButton.addEventListener("click", () => clearPhotoPrompt());
    document.querySelectorAll("[data-photo-view]").forEach((button) => {
      button.addEventListener("click", () => generatePhotoSheet(button.dataset.photoView));
    });
    const photoTemplateSaveButton = document.getElementById("photoTemplateSave");
    if (photoTemplateSaveButton) photoTemplateSaveButton.addEventListener("click", () => savePhotoTemplateDir());
    const photoTemplateOpenButton = document.getElementById("photoTemplateOpen");
    if (photoTemplateOpenButton) photoTemplateOpenButton.addEventListener("click", () => openPhotoTemplateDir());

    (function enableTabNav() {
      const wrap = document.querySelector(".tabnav");
      if (!wrap) return;
      const nav = wrap.querySelector(".tabs");
      if (!nav) return;
      const leftBtn = wrap.querySelector(".tabnav-arrow.left");
      const rightBtn = wrap.querySelector(".tabnav-arrow.right");
      const maxScroll = () => Math.max(0, nav.scrollWidth - nav.clientWidth);
      const step = () => Math.max(140, Math.round(nav.clientWidth * 0.6));

      function sync() {
        const max = maxScroll();
        wrap.classList.toggle("has-overflow", max > 2);
        if (leftBtn) leftBtn.disabled = nav.scrollLeft <= 1;
        if (rightBtn) rightBtn.disabled = nav.scrollLeft >= max - 1;
      }
      function move(delta) {
        nav.scrollLeft = Math.max(0, Math.min(maxScroll(), nav.scrollLeft + delta));
        sync();
      }

      // 滚轮：双向，悬浮在 tab 栏上时不吃页面滚动；触控板横向留给系统
      wrap.addEventListener("wheel", (event) => {
        if (Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
        if (maxScroll() <= 0) return;
        const unit = event.deltaMode === 1 ? 16 : (event.deltaMode === 2 ? nav.clientWidth : 1);
        event.preventDefault();
        move((event.deltaY || 0) * unit);
      }, { passive: false });

      if (leftBtn) leftBtn.addEventListener("click", () => move(-step()));
      if (rightBtn) rightBtn.addEventListener("click", () => move(step()));

      // 按住拖动
      let dragging = false, moved = 0, startX = 0, startLeft = 0;
      nav.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        dragging = true; moved = 0; startX = event.clientX; startLeft = nav.scrollLeft;
      });
      nav.addEventListener("pointermove", (event) => {
        if (!dragging) return;
        const dx = event.clientX - startX;
        moved = Math.max(moved, Math.abs(dx));
        nav.scrollLeft = Math.max(0, Math.min(maxScroll(), startLeft - dx));
      });
      const endDrag = () => { if (!dragging) return; dragging = false; sync(); };
      nav.addEventListener("pointerup", endDrag);
      nav.addEventListener("pointercancel", endDrag);
      nav.addEventListener("click", (event) => {
        if (moved > 4) { event.preventDefault(); event.stopPropagation(); }
        moved = 0;
      }, true);
      nav.addEventListener("scroll", sync);
      window.addEventListener("resize", sync);
      sync();
    })();


    let memoryCandidates = [];
    let profilesCache = [];
    let currentProfileUid = "";

    function profileTerm() {
      return (document.getElementById("profileSearch").value || "").trim().toLowerCase();
    }

    function renderProfiles(list) {
      const box = document.getElementById("profileList");
      box.textContent = "";
      const term = profileTerm();
      const filtered = term
        ? list.filter((item) => [item.uid, item.name, item.alias, item.relation, item.birthday]
            .filter(Boolean).join(" ").toLowerCase().includes(term))
        : list;
      if (!filtered.length) {
        const empty = document.createElement("div");
        empty.className = "sub";
        empty.textContent = list.length ? "没有匹配的用户" : "还没有画像，可以点“新建档案”。";
        box.appendChild(empty);
        return;
      }
      filtered.slice(0, 200).forEach((profile) => {
        const row = document.createElement("div");
        row.className = "profile-item";
        const left = document.createElement("div");
        const name = document.createElement("div");
        name.textContent = (profile.name || profile.uid || "未知用户") + (profile.alias ? "（" + profile.alias + "）" : "");
        const meta = document.createElement("div");
        meta.className = "meta";
        meta.textContent = "UID " + (profile.uid || "") + " · 生日 " + (profile.birthday || "未知") + " · 消息 " + (profile.msg_count || 0) + "（累计）";
        left.appendChild(name);
        left.appendChild(meta);
        const badge = document.createElement("span");
        badge.className = "badge";
        badge.textContent = profile.relation || "普通";
        row.appendChild(left);
        row.appendChild(badge);
        row.addEventListener("click", () => openProfile(profile));
        box.appendChild(row);
      });
    }

    async function loadProfiles() {
      try {
        const result = await call("profiles");
        profilesCache = Array.isArray(result) ? result : listFrom(result);
        renderProfiles(profilesCache);
      } catch (error) {
        const box = document.getElementById("profileList");
        box.textContent = "";
        const empty = document.createElement("div");
        empty.className = "sub";
        empty.textContent = "读取失败：" + (error.message || error);
        box.appendChild(empty);
      }
    }

    function openProfile(profile) {
      currentProfileUid = profile.uid || "";
      document.getElementById("pUid").value = currentProfileUid;
      document.getElementById("pUid").readOnly = true;
      document.getElementById("pName").value = profile.name || "";
      document.getElementById("pAlias").value = profile.alias || "";
      document.getElementById("pBirthday").value = profile.birthday || "";
      document.getElementById("pRelation").value = profile.relation || "普通";
      document.getElementById("pAffection").value = Number(profile.affection || 0);
      document.getElementById("pNotes").value = profile.notes || "";
      const linksBox = document.getElementById("pLinks");
      linksBox.textContent = "";
      call("profile/links", { uid: currentProfileUid, platform: profile.platform || "" })
        .then((res) => {
          const links = ((res && res.data && res.data.links) || [])
            .filter((item) => item.uid && item.uid !== currentProfileUid);
          if (links.length) {
            linksBox.textContent = "关联身份：" + links.map((item) =>
              (item.label || item.platform) + " " + item.uid + (item.name ? "（" + item.name + "）" : "")
            ).join(" · ");
          }
        })
        .catch(() => {});
      document.getElementById("profileListCard").hidden = true;
      document.getElementById("profileEditor").hidden = false;
      document.getElementById("btnDeleteProfile").hidden = !profile.uid;
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    document.getElementById("btnNewProfile").addEventListener("click", () => {
      currentProfileUid = "";
      document.getElementById("pUid").readOnly = false;
      document.getElementById("pUid").value = "";
      document.getElementById("pName").value = "";
      document.getElementById("pAlias").value = "";
      document.getElementById("pBirthday").value = "";
      document.getElementById("pRelation").value = "普通";
      document.getElementById("pAffection").value = 10;
      document.getElementById("pNotes").value = "";
      document.getElementById("profileListCard").hidden = true;
      document.getElementById("profileEditor").hidden = false;
      document.getElementById("btnDeleteProfile").hidden = true;
      window.scrollTo({ top: 0, behavior: "smooth" });
    });

    document.getElementById("btnCloseEditor").addEventListener("click", () => {
      document.getElementById("profileEditor").hidden = true;
      document.getElementById("profileListCard").hidden = false;
    });

    document.getElementById("btnSaveProfile").addEventListener("click", async () => {
      const uid = (document.getElementById("pUid").value || "").trim();
      if (!uid) { toast("UID 不能为空", true); return; }
      const payload = {
        uid,
        name: document.getElementById("pName").value.trim(),
        alias: document.getElementById("pAlias").value.trim(),
        birthday: document.getElementById("pBirthday").value.trim(),
        relation: document.getElementById("pRelation").value,
        affection: Number(document.getElementById("pAffection").value || 0),
        notes: document.getElementById("pNotes").value.trim(),
      };
      try {
        const result = await call("profile_save", payload);
        if (result && result.code && result.code !== 0) throw new Error(result.msg || "保存失败");
        toast("画像已保存");
        await loadProfiles();
      } catch (error) {
        toast("保存失败：" + (error.message || error), true);
      }
    });

    document.getElementById("btnDeleteProfile").addEventListener("click", async () => {
      const uid = (currentProfileUid || document.getElementById("pUid").value || "").trim();
      if (!uid) { toast("还没有选中档案", true); return; }
      const name = document.getElementById("pName").value || uid;
      if (!window.confirm("删除画像「" + name + "」？\n会一起删掉他的档案、消息、主动消息记录、渠道标记和礼物心愿，不可恢复。")) return;
      try {
        // 写操作直接走 HTTP：页面桥接有时返回「成功」但其实没执行，
        // 表现就是点了删除、刷新还在。
        const result = await httpCall("profile_delete", { uid, confirm: 1 });
        const counts = (result && result.data && result.data.counts) || {};
        toast("已删除（清理 " + Object.entries(counts).map(([k, v]) => k + " " + v).join("、") + "）");
        currentProfileUid = "";
        document.getElementById("profileEditor").hidden = true;
          loadProfiles();
      } catch (error) {
        toast("删除失败：" + (error.message || error), true);
      }
    });


    document.getElementById("btnProfileSearch").addEventListener("click", () => renderProfiles(profilesCache));
    document.getElementById("profileSearch").addEventListener("input", () => renderProfiles(profilesCache));

    let configItems = [];

    const OPTION_LABELS = {
      reply_style: { soft: "温柔", cool: "清冷", lively: "活泼", mature: "成熟" },
      seg_delay_type: {
        dynamic: "按字数动态", smart: "智能（动态+标点）",
        per_char: "按字数", random: "区间随机", fixed: "固定间隔",
      },
    };

    function optionLabel(key, value) {
      const labels = OPTION_LABELS[key];
      return labels && labels[value] ? labels[value] : value;
    }

    const TEMPLATE_FALLBACKS = {
      hf_judge_rules: {
        templates: {
          rule: {
            name: "评分规则",
            items: {
              dimension_name: { description: "维度标识（英文）", type: "string", default: "", invisible: true },
              dimension_display: { description: "维度中文名", type: "string", default: "" },
              weight: { description: "权重 (0-1)", type: "float", default: 0.2 },
              low_desc: { description: "低分情况描述", type: "string", default: "" },
              high_desc: { description: "高分情况描述", type: "string", default: "" },
            },
          },
        },
      },
      hf_group_threshold_configs: {
        templates: {
          group_config: {
            name: "群聊阈值配置",
            items: {
              chat_id: { description: "群聊标识", type: "string", default: "" },
              reply_threshold: { description: "基础回复阈值", type: "float", default: 0.6 },
              dynamic_threshold_max: { description: "动态阈值上限", type: "float", default: 0.85 },
              dynamic_threshold_min: { description: "动态阈值下限", type: "float", default: 0.35 },
            },
          },
        },
      },
    };

    function templateDefs(item) {
      const own = item.templates || {};
      if (Object.keys(own).length) return own;
      const fallback = TEMPLATE_FALLBACKS[item.key];
      return fallback ? fallback.templates : {};
    }

    function templateFieldInput(field, value) {
      const key = "";
      const type = field && field.type ? field.type : "string";
      let input;
      if (type === "bool") {
        input = document.createElement("input");
        input.type = "checkbox";
        input.checked = Boolean(value);
      } else if (type === "int" || type === "float") {
        input = document.createElement("input");
        input.className = "input";
        input.type = "number";
        input.step = type === "float" ? "0.01" : "1";
        input.value = value === null || value === undefined ? "" : value;
      } else if (type === "text" || String(arguments[2] || "").includes("desc")) {
        input = document.createElement("textarea");
        input.className = "input";
        input.rows = 2;
        input.value = value === null || value === undefined ? "" : value;
      } else {
        input = document.createElement("input");
        input.className = "input";
        input.type = "text";
        input.value = value === null || value === undefined ? "" : value;
      }
      return input;
    }

    function appendTemplateCard(container, item, value) {
      const templates = templateDefs(item);
      const entries = Object.entries(templates);
      let templateKey = value && value.__template_key;
      if (!templateKey || !templates[templateKey]) {
        templateKey = entries.length ? entries[0][0] : "";
      }
      const meta = templates[templateKey] || {};
      const card = document.createElement("div");
      card.className = "template-card";
      const head = document.createElement("div");
      head.className = "template-head";
      const title = document.createElement("span");
      title.textContent = meta.name || templateKey || "一项";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "template-remove ghost";
      remove.textContent = "删除";
      remove.addEventListener("click", () => card.remove());
      head.appendChild(title);
      head.appendChild(remove);
      card.appendChild(head);

      const grid = document.createElement("div");
      grid.className = "template-grid";
      const keyInput = document.createElement("input");
      keyInput.type = "hidden";
      keyInput.dataset.templateField = "__template_key";
      keyInput.dataset.fieldType = "string";
      keyInput.value = templateKey;
      grid.appendChild(keyInput);

      Object.entries(meta.items || {}).forEach(([fieldKey, field]) => {
        const raw = value && value[fieldKey] !== undefined ? value[fieldKey] : field.default;
        const input = templateFieldInput(field, raw, fieldKey);
        input.dataset.templateField = fieldKey;
        input.dataset.fieldType = field.type || "string";
        if (field.invisible) {
          input.type = "hidden";
          grid.appendChild(input);
          return;
        }
        const label = document.createElement("label");
        label.className = "template-field";
        label.textContent = field.description || fieldKey;
        label.appendChild(input);
        if (field.hint) {
          const hint = document.createElement("span");
          hint.className = "sub";
          hint.textContent = field.hint;
          label.appendChild(hint);
        }
        grid.appendChild(label);
      });
      card.appendChild(grid);
      const add = container.querySelector(".template-add");
      if (add) container.insertBefore(card, add);
      else container.appendChild(card);
    }

    function buildTemplateEditor(container, item) {
      container.textContent = "";
      container.className = "template-list";
      const values = Array.isArray(item.value) ? item.value : [];
      values.forEach((value) => appendTemplateCard(container, item, value));
      const add = document.createElement("button");
      add.type = "button";
      add.className = "template-add";
      add.textContent = "添加一项";
      add.addEventListener("click", () => appendTemplateCard(container, item, {}));
      container.appendChild(add);
    }

    function readTemplateList(container) {
      return Array.from(container.querySelectorAll(".template-card")).map((card) => {
        const out = {};
        card.querySelectorAll("[data-template-field]").forEach((input) => {
          const key = input.dataset.templateField;
          const type = input.dataset.fieldType || "string";
          if (type === "bool") out[key] = Boolean(input.checked);
          else if (type === "int") out[key] = parseInt(input.value || "0", 10);
          else if (type === "float") out[key] = parseFloat(input.value || "0");
          else out[key] = input.value;
        });
        return out;
      });
    }

    function settingControl(item) {
      const wrap = document.createElement("div");
      wrap.className = "control";
      let input;
      if (item.type === "bool") {
        input = document.createElement("div");
        input.className = "toggle-switch";
        input.dataset.checked = Boolean(item.value) ? "1" : "0";
        [["off", "关"], ["on", "开"]].forEach(([value, label]) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = value + (input.dataset.checked === (value === "on" ? "1" : "0") ? " active" : "");
          button.textContent = label;
          button.addEventListener("click", () => {
            input.dataset.checked = value === "on" ? "1" : "0";
            input.querySelectorAll("button").forEach((node) => {
              node.classList.toggle("active", node === button);
            });
          });
          input.appendChild(button);
        });
      } else if (item.special === "select_provider") {
        input = document.createElement("select");
        input.className = "input";
        const auto = document.createElement("option");
        auto.value = "";
        auto.textContent = "自动选择（优先便宜小模型，避开主对话）";
        input.appendChild(auto);
        providersCache.forEach((provider) => {
          if (provider.enable === false) return;
          const node = document.createElement("option");
          node.value = String(provider.id || "");
          node.textContent = (provider.id || "?") + (provider.model ? " · " + provider.model : "") + (provider.judge ? "（当前判定）" : "") + (provider.using ? "（主对话）" : "");
          input.appendChild(node);
        });
        const current = String(item.value ?? "");
        if (current && !Array.from(input.options).some((node) => node.value === current)) {
          const missing = document.createElement("option");
          missing.value = current;
          missing.textContent = current + "（已配置但未检测到）";
          input.appendChild(missing);
        }
        input.value = current;
      } else if (item.special === "select_memory_plugin") {
        input = document.createElement("select");
        input.className = "input";
        const auto = document.createElement("option");
        auto.value = "auto";
        auto.textContent = "自动检测（优先可用的记忆插件）";
        input.appendChild(auto);
        memoryCandidates.forEach((plugin) => {
          const name = String(plugin.name || "");
          if (!name) return;
          const node = document.createElement("option");
          node.value = name;
          const label = plugin.display_name && plugin.display_name !== name
            ? plugin.display_name + "（" + name + "）"
            : name;
          node.textContent = label
            + (plugin.ready ? " · 可读记忆" : "")
            + (plugin.activated === false ? " · 未启用" : "")
            + (plugin.users ? " · " + plugin.users + " 人" : "");
          input.appendChild(node);
        });
        const current = String(item.value || "auto");
        if (current !== "auto" && !Array.from(input.options).some((node) => node.value === current)) {
          const missing = document.createElement("option");
          missing.value = current;
          missing.textContent = current + "（已配置但未安装）";
          input.appendChild(missing);
        }
        input.value = current;
      } else if (Array.isArray(item.options) && item.options.length) {
        input = document.createElement("select");
        input.className = "input";
        item.options.forEach((option) => {
          const node = document.createElement("option");
          node.value = String(option);
          node.textContent = optionLabel(item.key, String(option));
          input.appendChild(node);
        });
        input.value = String(item.value);
      } else if (item.type === "int" || item.type === "float") {
        input = document.createElement("input");
        input.className = "input";
        input.type = "number";
        input.step = item.type === "float" ? "0.01" : "1";
        input.value = item.value === null || item.value === undefined ? "" : item.value;
      } else if (item.type === "list") {
        const values = Array.isArray(item.value) ? item.value : [];
        const simple = values.every((value) => ["string", "number"].includes(typeof value));
        input = document.createElement("textarea");
        input.className = "input";
        input.rows = 3;
        if (simple) {
          input.value = values.join("\n");
          input.dataset.configType = "list_text";
        } else {
          input.value = values.join("\n");
          input.dataset.configType = "list_text";
        }
      } else if (item.type === "template_list") {
        input = document.createElement("div");
        input.dataset.configType = "template_list";
        input._configItem = item;
        buildTemplateEditor(input, item);
      } else if (item.type === "text") {
        input = document.createElement("textarea");
        input.className = "input";
        input.rows = 2;
        input.value = item.value ?? "";
      } else {
        input = document.createElement("input");
        input.className = "input";
        input.type = "text";
        input.value = item.value ?? "";
      }
      input.dataset.configKey = item.key;
      if (!input.dataset.configType) input.dataset.configType = item.type;
      input._configItem = item;
      wrap.appendChild(input);
      return wrap;
    }

    // ---- 天气城市：填中文，自动写入 LocationID ----
    let citySearchTimer = null;
    async function searchCityOptions(query) {
      const list = document.getElementById("amoCityOptions");
      if (!list) return;
      list.textContent = "";
      if (!query) return;
      try {
        const result = objectFrom(await call("city/search", { q: query })) || {};
        (Array.isArray(result.items) ? result.items : []).slice(0, 20).forEach((item) => {
          const option = document.createElement("option");
          option.value = item.id;
          option.label = (item.label || "") + " " + (item.name || "");
          list.appendChild(option);
        });
      } catch (error) {}
    }

    function weatherBriefText(weather) {
      if (!weather || !weather.city) return "天气：还没取到";
      const parts = [weather.city];
      if (weather.text) parts.push(weather.text);
      if (weather.temp !== undefined && weather.temp !== null && weather.temp !== "") {
        parts.push(weather.temp + "℃");
      }
      if (weather.humidity) parts.push("湿度 " + weather.humidity + "%");
      if (weather.wind) parts.push(weather.wind);
      let text = "天气：" + parts.join(" · ");
      if (weather.source === "simulate") {
        text += "（模拟数据，配好和风 Key 才是真实天气）";
      }
      return text;
    }

    function buildCityRow() {
      const row = document.createElement("div");
      row.className = "setting-row";
      const left = document.createElement("div");
      const name = document.createElement("div");
      name.className = "name";
      name.textContent = "天气城市（中文）";
      const current = configItems.find((item) => item.key === "weather_city") || {};
      const currentName = configItems.find((item) => item.key === "weather_city_name") || {};
      const hint = document.createElement("div");
      hint.className = "hint";
      hint.textContent = "填「东莞」点设置，自动写成 LocationID 并回填城市显示名；重名写「北京朝阳」。当前："
        + (current.value
          ? (currentName.value ? currentName.value + "（" + current.value + "）" : current.value)
          : "跟随日程城市");
      const weatherLine = document.createElement("div");
      weatherLine.className = "hint";
      weatherLine.textContent = "天气：读取中…";
      left.appendChild(name);
      left.appendChild(hint);
      left.appendChild(weatherLine);
      row.appendChild(left);

      const control = document.createElement("div");
      control.className = "control";
      const input = document.createElement("input");
      input.className = "input";
      input.style.flex = "1";
      input.style.minWidth = "0";
      input.placeholder = "东莞 / 北京朝阳 / 深圳南山";
      input.setAttribute("list", "amoCityOptions");
      const list = document.createElement("datalist");
      list.id = "amoCityOptions";
      const button = document.createElement("button");
      button.textContent = "设置";
      button.style.marginLeft = "6px";
      const apply = async () => {
        const value = (input.value || "").trim();
        if (!value) { toast("先填城市名", true); return; }
        try {
          const result = objectFrom(await call("city/set", { city: value })) || {};
          configItems.forEach((item) => {
            if (item.key === "weather_city") item.value = result.id;
            if (item.key === "weather_city_name") item.value = result.name;
          });
          input.value = "";
          let done = "已切到 " + (result.name || result.id) + "（" + result.id + "）";
          if (result.weather && result.weather.temp !== undefined && result.weather.temp !== null) {
            done += " · " + result.weather.temp + "℃" + (result.weather.text || "");
          }
          toast(done);
          renderSettings();
        } catch (error) {
          toast(error.message || "设置失败", true);
        }
      };
      input.addEventListener("input", () => {
        clearTimeout(citySearchTimer);
        const value = (input.value || "").trim();
        citySearchTimer = setTimeout(() => searchCityOptions(value), 220);
      });
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") { event.preventDefault(); apply(); }
      });
      button.addEventListener("click", apply);
      control.appendChild(input);
      control.appendChild(button);
      control.appendChild(list);
      row.appendChild(control);
      call("weather/brief")
        .then((result) => {
          const brief = objectFrom(result) || {};
          weatherLine.textContent = weatherBriefText(brief.weather);
        })
        .catch(() => { weatherLine.textContent = "天气：读取失败"; });
      return row;
    }

    function settingsCategory(item, group) {
      const text = [item.key, item.description, item.hint, item.group].join(" ").toLowerCase();
      if (group === "基础") {
        if (/diary|日记|连载|debounce/.test(text)) return "日记与连载";
        if (/weather|city|timezone|hefeng|weather_/.test(text)) return "城市、天气与时区";
        if (/bot_name|persona|reply|dialect|style|name|称呼/.test(text)) return "角色与说话";
        if (/theme|debug|log|enable|开关|面板/.test(text)) return "开关与显示";
        return "其他基础";
      }
      if (group === "状态") {
        if (/period|menstru|生理/.test(text)) return "生理期";
        if (/health|sick|健康|生病/.test(text)) return "健康";
        if (/mood|心情|情绪/.test(text)) return "心情";
        if (/schedule|日程/.test(text)) return "日程与作息";
        return "其他状态";
      }
      if (group === "记忆") {
        if (/provider|来源|source|plugin|data/.test(text)) return "来源与连接";
        if (/recall|召回|summary|摘要|context|记忆/.test(text)) return "召回与摘要";
        if (/milestone|digest|auto_learn|归档|学习/.test(text)) return "归档与学习";
        return "其他记忆";
      }
      if (group === "语音") {
        if (/enable|开关/.test(text)) return "按需语音";
        return "发送方式";
      }
      if (group === "私聊") {
        if (/debounce|防抖/.test(text)) return "防抖与合并";
        if (/proactive|主动/.test(text)) return "主动消息";
        if (/delay|segment|分段|回复/.test(text)) return "回复节奏";
        return "其他私聊";
      }
      if (group === "心流") {
        if (/enable|开关|debug|日志/.test(text)) return "开关与调试";
        if (/threshold|阈值|activity|活跃/.test(text)) return "阈值与活跃度";
        if (/judge|判断|provider|模型|context/.test(text)) return "判断模型";
        if (/whitelist|白名单|group|群/.test(text)) return "群聊与白名单";
        return "其他心流";
      }
      if (group === "分段") {
        if (/enable|开关|debug/.test(text)) return "开关与调试";
        if (/split|symbol|min|max|断句|分隔/.test(text)) return "断句规则";
        if (/delay|延迟|typing|打字/.test(text)) return "延迟节奏";
        return "其他分段";
      }
      return "其他";
    }

    function renderSettings() {
      const box = document.getElementById("settingsList");
      box.textContent = "";
      const term = (document.getElementById("settingsSearch").value || "").trim().toLowerCase();
      const activeGroup = settingsGroupFilter || "基础";
      const groups = {};
      configItems.forEach((item) => {
        const groupName = item.group || "基础";
        if (groupName !== activeGroup) return;
        if (term) {
          const haystack = (item.key + " " + (item.description || "") + " " + (item.hint || "")).toLowerCase();
          if (!haystack.includes(term)) return;
        }
        const category = settingsCategory(item, groupName);
        if (!groups[category]) groups[category] = [];
        groups[category].push(item);
      });
      const total = Object.values(groups).reduce((sum, rows) => sum + rows.length, 0);
      if (activeGroup === "基础") {
        const themeRow = document.createElement("div");
        themeRow.className = "setting-row";
        const left = document.createElement("div");
        const name = document.createElement("div");
        name.className = "name";
        name.textContent = "面板主题";
        const hint = document.createElement("div");
        hint.className = "hint";
        hint.textContent = "只影响当前浏览器，立即切换";
        left.appendChild(name);
        left.appendChild(hint);
        themeRow.appendChild(left);
        const control = document.createElement("div");
        control.className = "control";
        const select = document.createElement("select");
        select.id = "themeSelect";
        select.className = "input";
        [["light", "白色"], ["dark", "深色"]].forEach(([value, label]) => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = label;
          select.appendChild(option);
        });
        select.value = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
        select.addEventListener("change", () => applyTheme(select.value, true));
        control.appendChild(select);
        themeRow.appendChild(control);
        box.appendChild(themeRow);
      }
      if (activeGroup === "基础" && !term) box.appendChild(buildCityRow());
      if (!total) {
        if (activeGroup !== "基础") box.textContent = "这个分组没有匹配的配置";
        return;
      }
      Object.keys(groups).sort().forEach((category) => {
        const rows = groups[category] || [];
        const details = document.createElement("details");
        details.className = "settings-group";
        // 默认收起；只有用户主动搜索时才自动展开匹配项。
        details.open = Boolean(term);
        const summary = document.createElement("summary");
        const title = document.createElement("span");
        title.className = "settings-group-title";
        title.textContent = category;
        const count = document.createElement("span");
        count.className = "settings-count";
        count.textContent = rows.length + " 项";
        summary.appendChild(title);
        summary.appendChild(count);
        details.appendChild(summary);
        const body = document.createElement("div");
        body.className = "settings-group-body";
        rows.forEach((item) => {
          const row = document.createElement("div");
          row.className = "setting-row" + (item.type === "template_list" ? " wide" : "");
          const left = document.createElement("div");
          const name = document.createElement("div");
          name.className = "name";
          name.textContent = item.description || item.key;
          left.appendChild(name);
          if (item.hint || item.type === "list" || item.type === "template_list") {
            const hint = document.createElement("div");
            hint.className = "hint";
            hint.textContent = (item.hint || "") + (item.type === "list" ? "（一行一个，直接输入，不用加引号/JSON）" : (item.type === "template_list" ? "（在下面逐项填写）" : ""));
            left.appendChild(hint);
          }
          row.appendChild(left);
          row.appendChild(settingControl(item));
          body.appendChild(row);
        });
        details.appendChild(body);
        box.appendChild(details);
      });
    }

    let memoryPluginsLoaded = false;

    async function ensureMemoryPlugins(force) {
      if (memoryPluginsLoaded && !force) return memoryCandidates;
      try {
        const result = objectFrom(await call("memory_candidates")) || {};
        memoryCandidates = Array.isArray(result.items) ? result.items : [];
      } catch (error) {
        memoryCandidates = [];
      }
      memoryPluginsLoaded = true;
      return memoryCandidates;
    }

    let providersCache = [];

    async function ensureProviders(force) {
      if (providersCache.length && !force) return providersCache;
      try {
        const result = objectFrom(await call("providers")) || {};
        providersCache = Array.isArray(result.items) ? result.items : [];
      } catch (error) {
        providersCache = [];
      }
      return providersCache;
    }

    async function loadSettings() {
      try {
        const result = await call("config");
        const data = objectFrom(result);
        configItems = Array.isArray(data.items) ? data.items : [];
        if (configItems.some((item) => item.special === "select_provider")) await ensureProviders();
        if (configItems.some((item) => item.special === "select_memory_plugin")) await ensureMemoryPlugins();
        renderSettings();
      } catch (error) {
        document.getElementById("settingsList").textContent = "读取配置失败：" + (error.message || error);
      }
    }

    document.getElementById("btnReloadSettings").addEventListener("click", loadSettings);
    document.getElementById("settingsSearch").addEventListener("input", renderSettings);
    document.getElementById("btnSaveSettings").addEventListener("click", async () => {
      const changes = {};
      try {
        document.querySelectorAll("[data-config-key]").forEach((el) => {
          const type = el.dataset.configType;
          let value;
          if (type === "bool") value = el.classList.contains("toggle-switch") ? el.dataset.checked === "1" : el.checked;
          else if (type === "int") value = parseInt(el.value || "0", 10);
          else if (type === "float") value = parseFloat(el.value || "0");
          else if (type === "template_list") value = readTemplateList(el);
          else if (type === "list_text") {
            const lines = el.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
            const numeric = lines.length && lines.every((line) => /^-?\d+(?:\.\d+)?$/.test(line));
            value = numeric ? lines.map(Number) : lines;
          } else if (["template_list", "dict", "object"].includes(type)) {
            try { value = JSON.parse(el.value || (type === "dict" || type === "object" ? "{}" : "[]")); }
            catch (error) { throw new Error(el.dataset.configKey + " 的 JSON 格式错误"); }
          } else value = el.value;
          changes[el.dataset.configKey] = value;
        });
        const result = await call("config_save", { changes });
        if (result && result.code && result.code !== 0) throw new Error(result.msg || "保存失败");
        toast("设置已保存");
        await loadSettings();
        await load();
      } catch (error) {
        toast("保存失败：" + (error.message || error), true);
      }
    });

    const QQ_GROUP_URL = "http://qm.qq.com/cgi-bin/qm/qr?_wv=1027&k=AQEkXQn02RRkdb_xlr2c9DA8Ik63KhsT&authKey=xhuF9VxkYe5a73qvZQqCbLT1yy82LNcu7FPcvX2XK2LI0UlD0EwieiKRCmQGGzcJ&noverify=0&group_code=1055679275";
    async function copyGroupLink() {
      try {
        await navigator.clipboard.writeText(QQ_GROUP_URL);
        toast("群链接已复制，去 QQ 粘贴打开");
        return true;
      } catch (error) {
        try {
          const area = document.createElement("textarea");
          area.value = QQ_GROUP_URL;
          document.body.appendChild(area);
          area.select();
          document.execCommand("copy");
          area.remove();
          toast("群链接已复制，去 QQ 粘贴打开");
          return true;
        } catch (inner) {
          toast("请长按链接复制后到 QQ 打开", true);
          return false;
        }
      }
    }
    document.querySelectorAll(".qq-group-link").forEach((link) => {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        try {
          const opened = window.open(QQ_GROUP_URL, "_blank");
          if (opened) return;
        } catch (error) {}
        copyGroupLink();
      });
    });

    async function boot(attempt) {
      try {
        await load();
        // 默认落在「世界」，想看状态点时钟左边的脉搏图标
        showTab("world");
      } catch (error) {
        if (attempt < 2) {
          setTimeout(() => boot(attempt + 1), 1500);
          return;
        }
        document.getElementById("dominant").textContent = "读取失败";
        const raw = error.message || "请从插件详情→查看页面重新打开";
        const message = raw.indexOf("closed database") >= 0
          ? "检测到旧插件实例残留，请完全重启 AstrBot（不是重载）后再打开"
          : raw;
        toast("接口未连接：" + message, true);
      }
    }
    setTimeout(() => boot(0), 400);
    setInterval(() => { load().catch(() => {}); }, 60000);
    /* ---------- 关系图谱（神经元） ---------- */
    const NEURON_COLORS = {
      家人: "#d98c8c", 恋人: "#e06a9a", 朋友: "#7aa8e0", 同事: "#6fc0a8",
      同学: "#c9a24d", 熟人: "#9b8fd0", 陌生人: "#9aa0a6", 仇人: "#c0504d",
    };
    let neuronGraph = null;
    let neuronActive = "";
    let neuronHoverId = "";
    let neuronRaf = 0;
    const neuronCamera = { yaw: 0.35, pitch: -0.2, distance: 0, auto: true };

    function neuronColor(relation) {
      return NEURON_COLORS[relation] || "#9aa0a6";
    }

    const BRAIN_REL_RANK = { 家人: 5, 恋人: 6, 朋友: 4, 同事: 3, 同学: 3, 熟人: 2, 陌生人: 1, 仇人: 4 };

    function buildNeuronGraph(edges) {
      const nodes = new Map();
      const links = [];
      edges.forEach((edge) => {
        const a = String(edge.a_uid || "");
        const b = String(edge.b_uid || "");
        if (!a || !b) return;
        if (!nodes.has(a)) nodes.set(a, { id: a, name: edge.a_name || a, relation: "", degree: 0 });
        if (!nodes.has(b)) nodes.set(b, { id: b, name: edge.b_name || b, relation: "", degree: 0 });
        const relation = edge.relation || "朋友";
        links.push({ a, b, relation });
        const rank = BRAIN_REL_RANK[relation] || 3;
        [a, b].forEach((id) => {
          const node = nodes.get(id);
          node.degree += 1;
          if (rank > (BRAIN_REL_RANK[node.relation] || 0)) node.relation = relation;
        });
      });
      // 球面均匀撒点，再跑 3D 力导向，先长成“一团脑子”的基础形状
      const list = [...nodes.values()];
      const golden = Math.PI * (3 - Math.sqrt(5));
      const shell = 70 + Math.min(110, list.length * 1.1);
      list.forEach((node, i) => {
        const y = 1 - (i / Math.max(1, list.length - 1)) * 2;
        const ring = Math.sqrt(Math.max(0, 1 - y * y));
        const theta = golden * i;
        node.x = Math.cos(theta) * ring * shell;
        node.y = y * shell;
        node.z = Math.sin(theta) * ring * shell;
        node.vx = 0; node.vy = 0; node.vz = 0;
      });
      return { nodes: list, links, index: nodes, radius: shell };
    }

    function settleNeuronGraph(graph, iterations) {
      const nodes = graph.nodes;
      const n = nodes.length;
      for (let step = 0; step < iterations; step += 1) {
        for (let i = 0; i < n; i += 1) {
          const a = nodes[i];
          for (let j = i + 1; j < n; j += 1) {
            const b = nodes[j];
            let dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
            const dist2 = dx * dx + dy * dy + dz * dz || 0.01;
            const dist = Math.sqrt(dist2);
            const force = Math.min(700, 900 / dist2);
            dx /= dist; dy /= dist; dz /= dist;
            a.vx += dx * force; a.vy += dy * force; a.vz += dz * force;
            b.vx -= dx * force; b.vy -= dy * force; b.vz -= dz * force;
          }
        }
        graph.links.forEach((link) => {
          const a = graph.index.get(link.a), b = graph.index.get(link.b);
          if (!a || !b) return;
          let dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
          const dist = Math.sqrt(dx * dx + dy * dy + dz * dz) || 0.01;
          const force = (dist - 52) * 0.05;
          dx /= dist; dy /= dist; dz /= dist;
          a.vx += dx * force; a.vy += dy * force; a.vz += dz * force;
          b.vx -= dx * force; b.vy -= dy * force; b.vz -= dz * force;
        });
        for (let i = 0; i < n; i += 1) {
          const node = nodes[i];
          node.vx -= node.x * 0.02; node.vy -= node.y * 0.02; node.vz -= node.z * 0.02;
          node.vx *= 0.5; node.vy *= 0.5; node.vz *= 0.5;
          node.x += node.vx; node.y += node.vy; node.z += node.vz;
        }
      }
    }

    function normalizeNeuronGraph(graph, radius) {
      let maxR = 0;
      graph.nodes.forEach((node) => { maxR = Math.max(maxR, Math.hypot(node.x, node.y, node.z)); });
      if (maxR > 0.01) {
        const target = Math.max(40, Number(radius) || 120);
        const scale = target / maxR;
        graph.nodes.forEach((node) => { node.x *= scale; node.y *= scale; node.z *= scale; });
      }
    }

    function projectNeuronGraph(graph, camera, width, height) {
      const cosY = Math.cos(camera.yaw), sinY = Math.sin(camera.yaw);
      const cosP = Math.cos(camera.pitch), sinP = Math.sin(camera.pitch);
      const focus = 760;
      const center = focus + camera.distance;
      graph.nodes.forEach((node) => {
        const x1 = node.x * cosY - node.z * sinY;
        const z1 = node.x * sinY + node.z * cosY;
        const y1 = node.y * cosP - z1 * sinP;
        const z2 = node.y * sinP + z1 * cosP;
        const t = focus / Math.max(180, center + z2);
        node.px = width / 2 + x1 * t;
        node.py = height / 2 + y1 * t;
        node.pscale = t;
        node.pdepth = z2;
      });
    }

    
    // ---- 关系图星空底：深蓝夜空 + 缓慢闪烁的星点（2026-11-11） ----
    const SKY_STARS = (() => {
      const stars = [];
      for (let i = 0; i < 160; i += 1) {
        const a = Math.sin(i * 127.1 + 311.7) * 43758.5453;
        const b = Math.sin(i * 269.5 + 183.3) * 43758.5453;
        const c = Math.sin(i * 419.2 + 371.9) * 43758.5453;
        stars.push({
          x: a - Math.floor(a),
          y: b - Math.floor(b),
          r: 0.4 + (c - Math.floor(c)) * 1.3,
          phase: (b * 7) % 6.2832,
          speed: 0.35 + ((a * 3) % 1) * 1.1,
        });
      }
      return stars;
    })();

    function drawStarfield(ctx, width, height, now) {
      const sky = ctx.createLinearGradient(0, 0, width, height);
      sky.addColorStop(0, "#0a1726");
      sky.addColorStop(0.55, "#0d2438");
      sky.addColorStop(1, "#12314a");
      ctx.fillStyle = sky;
      ctx.fillRect(0, 0, width, height);
      const t = (now || 0) / 1000;
      for (let i = 0; i < SKY_STARS.length; i += 1) {
        const star = SKY_STARS[i];
        const alpha = 0.3 + 0.55 * Math.abs(Math.sin(t * star.speed + star.phase));
        ctx.globalAlpha = alpha;
        ctx.fillStyle = "#cfeeff";
        ctx.beginPath();
        ctx.arc(star.x * width, star.y * height, star.r, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }

function drawNeuronGraph(ctx, graph, width, height, camera, hoverId) {
      try { drawStarfield(ctx, width, height, performance.now()); } catch (err) { ctx.clearRect(0, 0, width, height); }
      const halo = ctx.createRadialGradient(width / 2, height / 2, 8, width / 2, height / 2, Math.max(width, height) * 0.55);
      halo.addColorStop(0, "rgba(56,189,248,0.10)");
      halo.addColorStop(1, "rgba(56,189,248,0)");
      ctx.fillStyle = halo;
      ctx.fillRect(0, 0, width, height);

      projectNeuronGraph(graph, camera, width, height);
      const activeId = hoverId || neuronActive;
      const ordered = graph.nodes.slice().sort((a, b) => b.pdepth - a.pdepth);

      // 远的线更淡，近的更亮，转起来才有深度
      graph.links.forEach((link) => {
        const a = graph.index.get(link.a), b = graph.index.get(link.b);
        if (!a || !b) return;
        const depth = (a.pdepth + b.pdepth) / 2;
        const near = activeId && (link.a === activeId || link.b === activeId);
        const base = 0.05 + 0.2 * (1 - Math.min(1, Math.max(0, (depth + 180) / 360)));
        ctx.strokeStyle = neuronColor(link.relation);
        ctx.globalAlpha = near ? 0.9 : base;
        ctx.lineWidth = near ? 1.8 : 0.8;
        ctx.beginPath(); ctx.moveTo(a.px, a.py); ctx.lineTo(b.px, b.py); ctx.stroke();
      });
      ctx.globalAlpha = 1;

      // 节点带一点辉光，像神经元
      ordered.forEach((node) => {
        const active = node.id === activeId;
        const scale = Math.max(0.45, node.pscale);
        const radius = (active ? 9 : node.id === "bot" ? 7.5 : 5.5) * scale;
        const color = node.id === "bot" ? "#38bdf8" : neuronColor(node.relation);
        const glow = ctx.createRadialGradient(node.px, node.py, 0, node.px, node.py, radius * 3.4);
        glow.addColorStop(0, color);
        glow.addColorStop(0.35, color);
        glow.addColorStop(1, "rgba(255,255,255,0)");
        ctx.globalAlpha = active ? 0.6 : 0.32;
        ctx.fillStyle = glow;
        ctx.beginPath(); ctx.arc(node.px, node.py, radius * 3.4, 0, Math.PI * 2); ctx.fill();
        ctx.globalAlpha = 1;
        ctx.beginPath();
        ctx.fillStyle = active ? "#38bdf8" : "#fff";
        ctx.strokeStyle = color;
        ctx.lineWidth = active ? 2.2 : 1.2;
        ctx.arc(node.px, node.py, radius, 0, Math.PI * 2);
        ctx.fill(); ctx.stroke();
      });

      // 标签只给关键的：bot、悬停的、以及关系最近/连接最多的十几个
      const labelNodes = ordered.filter((node) => node.id === "bot" || node.id === activeId)
        .concat(ordered.filter((node) => node.id !== "bot" && node.id !== activeId)
          .sort((a, b) => (BRAIN_REL_RANK[b.relation] || 0) - (BRAIN_REL_RANK[a.relation] || 0) || b.degree - a.degree)
          .slice(0, 12));
      ctx.font = "11px system-ui, sans-serif";
      ctx.textAlign = "center";
      labelNodes.forEach((node) => {
        const label = node.name.length > 8 ? node.name.slice(0, 8) + "…" : node.name;
        ctx.globalAlpha = node.id === activeId || node.id === "bot" ? 1 : 0.72;
        ctx.fillStyle = "#dff2ff";
        ctx.fillText(label, node.px, node.py + 18 * Math.max(0.6, node.pscale));
      });
      ctx.globalAlpha = 1;
    }

    function renderNeuronGraph(edges) {
      const canvas = document.getElementById("neuronCanvas");
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      const graph = buildNeuronGraph(edges);
      neuronGraph = graph;
      const legend = document.getElementById("neuronLegend");
      if (!graph.nodes.length) {
        if (neuronRaf) { cancelAnimationFrame(neuronRaf); neuronRaf = 0; }
        canvas.width = Math.max(1, canvas.clientWidth);
        canvas.height = Math.max(1, canvas.clientHeight);
        drawStarfield(ctx, canvas.width, canvas.height, performance.now());
        ctx.fillStyle = "#dff2ff";
        ctx.font = "12px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText("还没有关系，双击标题可以手动添加", canvas.clientWidth / 2, canvas.clientHeight / 2);
        if (legend) legend.textContent = "";
        return;
      }
      // 节点多的时候少跑几轮，打开面板别卡住
      const iterations = graph.nodes.length > 400 ? 40 : (graph.nodes.length > 220 ? 90 : 180);
      settleNeuronGraph(graph, iterations);
      // 按画布实际大小定球半径，小屏（手机）也不会顶出边框
      normalizeNeuronGraph(graph, Math.min(canvas.clientWidth || 560, canvas.clientHeight || 240) * 0.34);

      if (legend) {
        const kinds = [...new Set(graph.links.map((link) => link.relation))];
        legend.textContent = "";
        kinds.forEach((kind) => {
          const span = document.createElement("span");
          span.style.cssText = "display:inline-block;margin-right:10px";
          const dot = document.createElement("i");
          dot.style.cssText = "display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px;background:" +
            String(neuronColor(kind) || "transparent");
          span.appendChild(dot);
          span.appendChild(document.createTextNode(String(kind)));
          legend.appendChild(span);
        });
      }

      let width = 0, height = 0, dpr = 1;
      const fit = () => {
        const w = canvas.clientWidth || (canvas.parentElement.clientWidth - 28);
        const h = canvas.clientHeight || 240;
        dpr = window.devicePixelRatio || 1;
        if (w !== width || h !== height) {
          width = w; height = h;
          canvas.width = Math.max(1, Math.round(w * dpr));
          canvas.height = Math.max(1, Math.round(h * dpr));
        }
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      };

      if (neuronRaf) cancelAnimationFrame(neuronRaf);
      let lastDraw = 0;
      const frame = (now) => {
        if (!canvas.isConnected) return;
        fit();
        if (width <= 0) { neuronRaf = requestAnimationFrame(frame); return; }
        if (neuronCamera.auto && !canvas.__brainDragging) neuronCamera.yaw += 0.0016;
        lastDraw = now || 0;
        // 30fps 就够了，节点多的时候别把主线程拖死
        if (!lastDraw || lastDraw - (canvas.__brainLast || 0) >= 32) {
          canvas.__brainLast = lastDraw;
          drawNeuronGraph(ctx, graph, width, height, neuronCamera, neuronHoverId);
        }
        neuronRaf = requestAnimationFrame(frame);
      };

      // 拖动 = 旋转整团神经网；滚轮缩放；双击回正
      canvas.style.touchAction = "none";
      canvas.style.cursor = "grab";
      canvas.__brainDragging = false;
      let lastX = 0, lastY = 0;
      const pick = (event) => {
        const rect = canvas.getBoundingClientRect();
        const mx = (event.clientX - rect.left) * (width / Math.max(1, rect.width));
        const my = (event.clientY - rect.top) * (height / Math.max(1, rect.height));
        let best = null, bestDist = 20;
        graph.nodes.forEach((node) => {
          const dist = Math.hypot(node.px - mx, node.py - my);
          if (dist < bestDist) { bestDist = dist; best = node; }
        });
        return best;
      };
      canvas.onpointerdown = (event) => {
        event.preventDefault();
        if (canvas.__brainIdle) clearTimeout(canvas.__brainIdle);
        canvas.__brainDragging = true;
        canvas.style.cursor = "grabbing";
        neuronCamera.auto = false;
        lastX = event.clientX; lastY = event.clientY;
        try { canvas.setPointerCapture(event.pointerId); } catch (err) {}
      };
      canvas.onpointermove = (event) => {
        if (canvas.__brainDragging) {
          neuronCamera.yaw += (event.clientX - lastX) * 0.009;
          neuronCamera.pitch += (event.clientY - lastY) * 0.007;
          neuronCamera.pitch = Math.max(-1.25, Math.min(1.25, neuronCamera.pitch));
          lastX = event.clientX; lastY = event.clientY;
          return;
        }
        const hit = pick(event);
        neuronHoverId = hit ? hit.id : "";
        canvas.style.cursor = hit ? "pointer" : "grab";
      };
      const endDrag = () => {
        if (!canvas.__brainDragging) return;
        canvas.__brainDragging = false;
        canvas.style.cursor = "grab";
        canvas.__brainIdle = setTimeout(() => { neuronCamera.auto = document.getElementById("relAuto").checked; }, 2500);
      };
      canvas.onpointerup = endDrag;
      canvas.onpointercancel = endDrag;
      canvas.onpointerleave = () => { neuronHoverId = ""; };
      canvas.onwheel = (event) => {
        event.preventDefault();
        neuronCamera.distance = Math.max(-220, Math.min(500, neuronCamera.distance + event.deltaY * 0.4));
      };
      canvas.ondblclick = () => {
        neuronCamera.yaw = 0.35; neuronCamera.pitch = -0.2; neuronCamera.distance = 0;
        neuronCamera.auto = document.getElementById("relAuto").checked;
      };

      frame();
    }


    function fillNeuronSelects(profiles) {
      const items = profiles || [];
      ["nRelA", "nRelB"].forEach((id) => {
        const select = document.getElementById(id);
        if (!select) return;
        const keep = select.value;
        select.textContent = "";
        if (!items.length) {
          const empty = document.createElement("option");
          empty.value = "";
          empty.textContent = "还没有画像";
          select.appendChild(empty);
          return;
        }
        items.forEach((item) => {
          const uid = String(item.uid || "");
          const name = String(item.name || item.alias || uid);
          const option = document.createElement("option");
          option.value = uid;
          option.textContent = name + "（" + uid + "）";
          select.appendChild(option);
        });
        if (keep) select.value = keep;
      });
    }

    function renderNeuronList(edges) {
      const box = document.getElementById("neuronList");
      if (!box) return;
      box.textContent = "";
      if (!edges.length) {
        box.innerHTML = '<div class="hint">还没有关系边。</div>';
        return;
      }
      edges.forEach((edge) => {
        const row = document.createElement("div");
        row.className = "row";
        row.style.cssText = "display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px dashed #eee";
        const text = document.createElement("span");
        text.style.flex = "1";
        text.textContent = (edge.a_name || edge.a_uid) + " ↔ " + (edge.b_name || edge.b_uid) + " · " + edge.relation;
        const del = document.createElement("button");
        del.className = "ghost";
        del.textContent = "删";
        del.addEventListener("click", async () => {
          await call("relation_delete", { a_uid: edge.a_uid, b_uid: edge.b_uid });
          toast("已删除");
          openNeuron();
        });
        row.appendChild(text); row.appendChild(del);
        box.appendChild(row);
      });
    }

    function setNeuronMode(edit) {
      const view = document.getElementById("neuronView");
      const editBox = document.getElementById("neuronEdit");
      const title = document.getElementById("neuronTitle");
      const hint = document.getElementById("neuronHint");
      if (!view || !editBox) return;
      view.hidden = !!edit;
      editBox.hidden = !edit;
      if (title) title.textContent = edit ? "关系管理" : "关系图谱";
      if (hint) hint.textContent = edit ? "改完会同步到记忆终端" : "拖动旋转 · 双击标题管理";
    }

    async function openNeuron(keepEdit) {
      const modal = document.getElementById("neuronModal");
      modal.hidden = false;
      document.getElementById("btnNeuron").classList.add("on");
      if (!keepEdit) setNeuronMode(false);
      try {
        const data = objectFrom(await call("relations")) || {};
        const edges = Array.isArray(data.edges) ? data.edges : (Array.isArray(data.items) ? data.items : []);
        fillNeuronSelects(data.profiles || []);
        renderNeuronGraph(edges);
        renderNeuronList(edges);
      } catch (error) {
        toast("读取关系失败：" + error.message, true);
      }
    }

    document.getElementById("btnNeuron").addEventListener("click", () => {
      const modal = document.getElementById("neuronModal");
      if (!modal.hidden) {
        modal.hidden = true;
        document.getElementById("btnNeuron").classList.remove("on");
        return;
      }
      openNeuron();
    });
    document.getElementById("btnNeuronClose").addEventListener("click", () => {
      document.getElementById("neuronModal").hidden = true;
      document.getElementById("btnNeuron").classList.remove("on");
      setNeuronMode(false);
      // 关闭后 canvas 还在文档里但宽高为 0，帧循环会一直空转占 60fps
      if (neuronRaf) { cancelAnimationFrame(neuronRaf); neuronRaf = 0; }
    });

    document.getElementById("relAuto").addEventListener("change", (event) => {
      neuronCamera.auto = !!event.target.checked;
    });    document.getElementById("neuronTitle").addEventListener("dblclick", () => {
      const editing = document.getElementById("neuronEdit").hidden === false;
      setNeuronMode(!editing);
    });
    document.getElementById("btnNeuronBack").addEventListener("click", () => setNeuronMode(false));
    document.getElementById("btnNeuronAdd").addEventListener("click", async () => {
      const a_uid = document.getElementById("nRelA").value;
      const b_uid = document.getElementById("nRelB").value;
      const relation = document.getElementById("nRelType").value;
      const note = document.getElementById("nRelNote").value.trim();
      if (!a_uid || !b_uid || a_uid === b_uid) {
        toast("请选择两个不同的人", true);
        return;
      }
      try {
        await call("relation_save", { a_uid, b_uid, relation, note });
        document.getElementById("nRelNote").value = "";
        toast("已添加");
        openNeuron(true);
      } catch (error) {
        toast("添加失败：" + error.message, true);
      }
    });

