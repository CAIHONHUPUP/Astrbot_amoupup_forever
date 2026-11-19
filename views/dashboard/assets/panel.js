/* 面板渲染与交互：从 index.html 内联脚本抽出；通信层在 assets/api-client.js（window.PanelApi）。 */
    const MOOD_KEYS = ["开心", "平静", "郁闷", "生气", "暴躁", "委屈", "疲惫", "焦虑", "兴奋", "依恋"];
    const MOOD_COLORS = {
      "开心": "#0ea5e9", "平静": "#0ea5e9", "郁闷": "#0ea5e9", "生气": "#0ea5e9",
      "暴躁": "#0ea5e9", "委屈": "#0ea5e9", "疲惫": "#0ea5e9", "焦虑": "#0ea5e9",
      "兴奋": "#0ea5e9", "依恋": "#0ea5e9"
    };
    let lastData = null;
    const THEME_KEY = "amo_panel_theme";

    function applyTheme(theme, persist) {
      const value = theme === "dark" ? "dark" : "light";
      document.documentElement.dataset.theme = value;
      const select = document.getElementById("themeSelect");
      if (select) select.value = value;
      if (persist) {
        try { localStorage.setItem(THEME_KEY, value); } catch (error) {}
      }
      if (lastData) {
        try { render(lastData); } catch (error) {}
      }
    }
    try { applyTheme(localStorage.getItem(THEME_KEY) || "light", false); }
    catch (error) { applyTheme("light", false); }

    async function load() {
      const res = await call("status");
      let payload = res;
      for (let i = 0; i < 3; i += 1) {
        if (payload && typeof payload === "object" && (payload.code === 0 || payload.status === "ok") && payload.data !== undefined) {
          payload = payload.data;
        } else {
          break;
        }
      }
      const data = normalize(payload);
      lastData = data;
      render(data);
    }

    function render(data) {
      const time = data.time || "";
      document.getElementById("clock").textContent = time;
      const dialectSelect = document.getElementById("worldDialect");
      const statusConfig = data.config || {};
      const dialectOff = statusConfig.dialect_enabled === false;
      const selectedDialect = dialectOff ? "普通话"
        : (data.dialect_style || statusConfig.dialect_style || "贵州话");
      if (dialectSelect) dialectSelect.value = selectedDialect;
      const dialectHint = document.getElementById("dialectHint");
      if (dialectHint) dialectHint.textContent = "当前语种词条：" + (data.dialect_count || statusConfig.dialect_count || 0) + " 条";
      document.getElementById("dominant").textContent = data.dominant || "—";
      document.getElementById("moodScore").textContent = data.mood_score ? Math.round(data.mood_score) : "--";

      const mood = data.mood || {};
      const activityNow = data.activity || {};
      const placeNow = activityNow.place || (data.worldview && data.worldview.city) || "家";
      const actNow = activityNow.activity || "待着";
      document.getElementById("moodContext").textContent = placeNow + " · 正在" + actNow;
      document.getElementById("moodNarration").textContent = data.mood_narration || "今天的心情有点说不清。";
      document.getElementById("moodAttribution").textContent =
        data.mood_attribution ? "【近期情绪归因】" + data.mood_attribution : "";
      const coreKeys = pickCoreKeys(mood);
      const bars = document.getElementById("moodBars");
      bars.textContent = "";
      coreKeys.forEach((key) => {
        const value = Math.max(0, Math.min(100, Number(mood[key] || 0)));
        const row = document.createElement("div");
        row.className = "bar-row";
        const name = document.createElement("span");
        name.textContent = key;
        const track = document.createElement("div");
        track.className = "track";
        const fill = document.createElement("span");
        fill.className = "fill";
        fill.style.width = value + "%";
        fill.style.background = barColor(key, value);
        track.appendChild(fill);
        const num = document.createElement("span");
        num.className = "num";
        num.textContent = String(Math.round(value));
        const emotion = document.createElement("span");
        emotion.className = "emotion";
        emotion.textContent = emotionText(key, value);
        row.appendChild(name);
        row.appendChild(track);
        row.appendChild(num);
        row.appendChild(emotion);
        bars.appendChild(row);
      });
      drawRadar(mood, coreKeys);
      drawTrend(data.mood_history || []);
      applyMoodTheme(mood);

      const period = data.period || {};
      const periodEmoji = period.phase && period.phase !== "非生理期" ? " 🩸" : "";
      document.getElementById("period").textContent = period.phase
        ? period.phase + (period.day ? " · 第" + period.day + "天" : "") + periodEmoji
        : "—";
      const cycle = Number(period.cycle_days || 28);
      const periodDay = Number(period.day || 0);
      const periodPct = cycle ? Math.max(0, Math.min(100, periodDay / cycle * 100)) : 0;
      document.getElementById("periodBar").style.width = periodPct + "%";
      const phases = ["月经期", "卵泡期", "排卵期", "黄体期"];
      const phaseBox = document.getElementById("periodPhases");
      phaseBox.textContent = "";
      phases.forEach((phaseName) => {
        const chip = document.createElement("span");
        chip.className = "phase-chip" + (period.phase === phaseName ? " active" : "");
        chip.textContent = phaseName.replace("期", "");
        phaseBox.appendChild(chip);
      });
      document.getElementById("periodSub").textContent = [
        period.tone || (period.pain !== undefined ? "疼痛 " + period.pain + "/10" : ""),
        "周期第 " + periodDay + "/" + cycle + " 天",
        period.phase_day ? "阶段第 " + period.phase_day + " 天" : ""
      ].filter(Boolean).join(" · ");

      const health = data.health || {};
      document.getElementById("health").textContent = (health.status || "—") + (health.sick ? " 🤒" : "");
      document.getElementById("healthBar").style.width = (health.sick ? 35 : 100) + "%";
      document.getElementById("healthSub").textContent = [
        health.note || "",
        health.sick ? "需要休息" : "状态良好"
      ].filter(Boolean).join(" · ");

      document.getElementById("weather").textContent = data.weather_text || "—";
      const weather = data.weather || {};
      document.getElementById("weatherBar").style.width = Math.max(0, Math.min(100, Number(weather.humidity || 0))) + "%";
      document.getElementById("weatherSub").textContent = [weather.city, weather.humidity ? "湿度 " + weather.humidity + "%" : "", weather.wind || ""]
        .filter(Boolean).join(" · ");

      const activity = data.activity || {};
      const city = weather.city || (data.worldview && data.worldview.city) || "";
      const place = activity.place || "";
      const act = activity.activity || "—";
      document.getElementById("activity").textContent = [city, place, act].filter(Boolean).join(" · ");
      const windowText = activity.start && activity.end ? activity.start + "-" + activity.end : "";
      document.getElementById("activitySub").textContent =
        [windowText ? "日程 " + windowText : "", place ? "地点 " + place : ""].filter(Boolean).join(" · ");
      const toMin = (value) => {
        const parts = String(value || "").split(":");
        return parts.length === 2 ? Number(parts[0]) * 60 + Number(parts[1]) : NaN;
      };
      let activityPct = 0;
      const startMin = toMin(activity.start);
      let endMin = toMin(activity.end);
      if (Number.isFinite(startMin) && Number.isFinite(endMin)) {
        if (endMin <= startMin) endMin += 1440;
        const now = new Date();
        let nowMin = now.getHours() * 60 + now.getMinutes();
        if (nowMin < startMin && endMin > 1440) nowMin += 1440;
        activityPct = Math.max(0, Math.min(100, (nowMin - startMin) / Math.max(1, endMin - startMin) * 100));
      }
      document.getElementById("activityBar").style.width = activityPct + "%";

      const counts = data.counts || {};
      document.getElementById("counts").textContent =
        "档案 " + (counts.profiles || 0) + " · 记忆 " + (counts.memories || 0) + " · 消息 " + (counts.messages || 0);
      const settle = data.trip_settle || {};
      const tripSettle = document.getElementById("tripSettle");
      if (tripSettle) tripSettle.textContent = settle.line || "—";
      const tripSettleSub = document.getElementById("tripSettleSub");
      if (tripSettleSub) {
        tripSettleSub.textContent = settle.day
          ? ("最近结算 " + settle.day + (settle.settled ? " · 共 " + settle.settled + " 笔" : " · 本次 0 笔"))
          : "没有结算记录";
      }
    }

    let currentScheduleDay = "";
    let scheduleDetailSeq = 0;
    let lastDetailRow = null;

    function eventTagText(kind) {
      if (kind === "overtime") return "小插曲 · 临时加班";
      if (kind === "friend_dinner") return "小插曲 · 朋友聚餐";
      if (kind === "business_trip") return "小插曲 · 临时外派";
      if (kind === "date") return "小插曲 · 约会";
      if (kind === "sick") return "小插曲 · 不舒服";
      if (kind === "hospital") return "小插曲 · 去医院";
      return kind ? "小插曲" : "";
    }

    async function toggleHourDiary(row, hour) {
      if (row.classList.contains("open") && row.dataset.loaded === "1") {
        row.classList.remove("open");
        return;
      }
      // 手风琴：展开这一小时就收起上一条；上一条的内容保留，重新点开直接出缓存，
      // 以前这里是清空上一条的 diary 区域，导致它虽然收不起来却变成一块空白。
      document.querySelectorAll("#scheduleList .schedule-hour.open").forEach((other) => {
        if (other !== row) {
          other.classList.remove("open");
          delete other.dataset.loading;
        }
      });
      if (lastDetailRow && lastDetailRow !== row) {
        lastDetailRow.classList.remove("open");
        delete lastDetailRow.dataset.loading;
      }
      row.classList.add("open");
      const box = row.querySelector(".schedule-diary");
      if (!box || box.dataset.loaded === "1" || row.dataset.loading === "1") return;
      const clickNow = Date.now();
      if (clickNow - Number(row.dataset.lastClick || 0) < 1000) return;
      row.dataset.lastClick = String(clickNow);
      const eventId = String(hour.id || (hour.start + "-" + hour.end));
      row.dataset.eventId = eventId;
      const requestSeq = ++scheduleDetailSeq;
      lastDetailRow = row;
      row.dataset.loading = "1";
      box.textContent = "";
      const loading = document.createElement("div");
      loading.className = "diary-loading";
      loading.textContent = "这一小时的小日记正在生成";
      box.appendChild(loading);
      try {
        const response = await Promise.race([
          call("schedule_detail", {
            date: currentScheduleDay,
            id: eventId,
            start: hour.start,
            end: hour.end,
          }),
          new Promise((_, reject) => setTimeout(() => reject(new Error("网络开小差了")), 15000)),
        ]);
        const data = objectFrom(response) || {};
        // 只按“请求序号 + 这一行当前挂着的事件”判失效；接口回显的 id 可能是日程行 id，
        // 以前把它当硬条件会把正常返回直接丢掉，卡片就空着。
        if (requestSeq !== scheduleDetailSeq || String(row.dataset.eventId || "") !== eventId) {
          delete row.dataset.loading;
          if (!String(box.textContent || "").trim()) {
            const stale = document.createElement("div");
            stale.className = "diary-meta";
            stale.textContent = "这一小时刚刷新过，点开再看一次吧。";
            box.appendChild(stale);
          }
          return;
        }
        // 后台正在生成时走专属文案；只有状态为空/出错才回落到 empty_message
        if (data && data.status === "generating") {
          box.textContent = "";
          const gen = document.createElement("div");
          gen.className = "diary-meta";
          gen.textContent = "（她正咬着笔头写日记呢，过两秒再刷新看看吧~）";
          box.appendChild(gen);
          row.dataset.loaded = "1";
          delete row.dataset.loading;
          return;
        }
        if (data.empty_message && !data.diary) {
          box.textContent = "";
          const empty = document.createElement("div");
          empty.className = "diary-meta";
          empty.textContent = String(data.empty_message);
          box.appendChild(empty);
          row.dataset.loaded = "1";
          delete row.dataset.loading;
          return;
        }
        if (data.pending) {
          box.textContent = "";
          const pending = document.createElement("div");
          pending.className = "diary-meta";
          pending.textContent = "这一小时还没过完呢，等她忙完再看吧~";
          box.appendChild(pending);
          row.dataset.loaded = "1";
          delete row.dataset.loading;
          return;
        }
        const diary = String(data.diary || "").trim();
        if (!diary) throw new Error("模型没有返回内容");
        box.textContent = "";
        const meta = document.createElement("div");
        meta.className = "diary-meta";
        // 详情弹层显示「来源 + 版本」：tree 行已经带上 source/reason/version。
        const detailSource = typeof window.amoScheduleSourceLabel === "function"
          ? window.amoScheduleSourceLabel(hour.source) : String(hour.source || "");
        const sourceVersion = detailSource
          ? "来源 " + detailSource + (hour.version ? " · v" + hour.version : "")
          : "";
        meta.textContent = [
          (data.start || hour.start) + "-" + (data.end || hour.end),
          data.place || hour.place || "",
          data.mood ? "心情 " + data.mood : "",
          sourceVersion,
          data.cached ? "已生成 · 直接读取" : "",
        ].filter(Boolean).join(" · ");
        const text = document.createElement("div");
        text.className = "diary-text";
        text.textContent = diary;
        box.appendChild(meta);
        if (Array.isArray(data.spending)) {
          const spend = document.createElement("div");
          spend.className = "diary-meta";
          spend.textContent = data.spending.length
            ? "本时段消费：" + data.spending.map(function (item) {
                return String(item.reason || item.category || "消费") + " ¥" + Number(item.amount_yuan).toFixed(2);
              }).join(" · ") + "（合计 ¥" + Number(data.spending_total_yuan || 0).toFixed(2) + "）"
            : "本时段没有消费";
          box.appendChild(spend);
        }
        box.appendChild(text);
        box.dataset.loaded = "1";
        // 收起判断看的是 row.dataset.loaded：只写 box 的话卡片点开就再也收不回去
        row.dataset.loaded = "1";
        delete row.dataset.loading;
      } catch (error) {
        delete row.dataset.loading;
        box.textContent = "";
        const err = document.createElement("div");
        err.className = "diary-error";
        err.textContent = friendlyError(error, "她迷糊了，这段小记没接上，再点一次试试。");
        box.appendChild(err);
        delete box.dataset.loaded;
      }
    }

    function schedulePeriodWord(hh) {
      if (hh >= 5 && hh < 11) return "早上";
      if (hh >= 11 && hh < 13) return "中午";
      if (hh >= 13 && hh < 17) return "下午";
      if (hh >= 17 && hh < 18) return "傍晚";
      if (hh >= 18 && hh < 23) return "晚上";
      return "夜里";
    }
    function dateHourLabel(dayStr, start, activity) {
      var text = String(activity || "");
      if (!/^和老公/.test(text)) return text;
      var sel = String(dayStr || "").slice(0, 10);
      var now = new Date();
      var pad = function (n) { return (n < 10 ? "0" : "") + n; };
      var today = now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate());
      var tm = new Date(now.getTime() + 86400000);
      var tomorrow = tm.getFullYear() + "-" + pad(tm.getMonth() + 1) + "-" + pad(tm.getDate());
      var hh = parseInt(String(start || "0").split(":")[0], 10) || 0;
      if (sel === today) return "今天 " + String(start || "") + " " + text;
      if (sel === tomorrow) return "明天" + schedulePeriodWord(hh) + " " + text;
      return sel.slice(5) + " " + schedulePeriodWord(hh) + " " + text;
    }
    function scheduleBlockPayload(hour) {
      return {
        date: currentScheduleDay,
        id: String(hour.id || hour.event_id || ""),
        start: String(hour.start || ""),
        end: String(hour.end || ""),
        activity: String(hour.activity || ""),
        place: String(hour.place || ""),
      };
    }

    async function deleteScheduleBlock(hour) {
      if (!window.confirm("删掉这一块日程？")) return;
      try {
        const response = await call("schedule/block/delete", scheduleBlockPayload(hour));
        if (!response || (typeof response.code === "number" && response.code !== 0)) {
          throw new Error((response && (response.msg || response.message)) || "没删掉");
        }
        if (typeof toast === "function") toast("日程已删除");
        loadSchedule(currentScheduleDay);
      } catch (error) {
        if (typeof toast === "function") toast(friendlyError(error, "没删掉，刷新后再试一次"));
      }
    }

    function openScheduleBlockEditor(hour) {
      const wrap = document.createElement("div");
      wrap.className = "sched-modal";
      const card = document.createElement("div");
      card.className = "sched-modal-card";
      const title = document.createElement("div");
      title.className = "label";
      title.textContent = "改这一块 · " + (currentScheduleDay || "");
      card.appendChild(title);
      const addField = (text, value) => {
        const label = document.createElement("label");
        label.className = "sched-field";
        const span = document.createElement("span");
        span.textContent = text;
        const input = document.createElement("input");
        input.value = value == null ? "" : String(value);
        label.appendChild(span);
        label.appendChild(input);
        card.appendChild(label);
        return input;
      };
      const startInput = addField("开始", hour.start || "");
      const endInput = addField("结束", hour.end || "");
      const activityInput = addField("活动", hour.activity || "");
      const placeInput = addField("地点", hour.place || "");
      const actions = document.createElement("div");
      actions.className = "sched-modal-actions";
      const saveBtn = document.createElement("button");
      saveBtn.type = "button";
      saveBtn.className = "primary";
      saveBtn.textContent = "保存";
      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "danger";
      delBtn.textContent = "删除这一块";
      const cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.className = "ghost";
      cancelBtn.textContent = "取消";
      actions.appendChild(saveBtn);
      actions.appendChild(delBtn);
      actions.appendChild(cancelBtn);
      card.appendChild(actions);
      wrap.appendChild(card);
      document.body.appendChild(wrap);
      const close = () => wrap.remove();
      wrap.addEventListener("click", (event) => { if (event.target === wrap) close(); });
      cancelBtn.addEventListener("click", close);
      saveBtn.addEventListener("click", async () => {
        saveBtn.disabled = true;
        try {
          const payload = scheduleBlockPayload(hour);
          payload.start = startInput.value.trim();
          payload.end = endInput.value.trim();
          payload.activity = activityInput.value.trim();
          payload.place = placeInput.value.trim();
          const response = await call("schedule/block/update", payload);
          if (!response || (typeof response.code === "number" && response.code !== 0)) {
            throw new Error((response && (response.msg || response.message)) || "没改成");
          }
          close();
          if (typeof toast === "function") toast("日程已更新");
          loadSchedule(currentScheduleDay);
        } catch (error) {
          saveBtn.disabled = false;
          if (typeof toast === "function") toast(friendlyError(error, "没改成，刷新后再试一次"));
        }
      });
      delBtn.addEventListener("click", async () => {
        close();
        await deleteScheduleBlock(hour);
      });
      startInput.focus();
    }

    function renderSchedule(data) {
      const box = document.getElementById("scheduleList");
      const day = document.getElementById("scheduleDay");
      box.textContent = "";
      const meta = data.meta || {};
      currentScheduleDay = data.day || "";
      const context = data.context || {};
      const event = context.event || {};
      const eventText = event.kind && event.kind !== "none" ? " · " + (event.title || "小插曲") : "";
      day.textContent = data.day
        ? "日期：" + data.day + (meta.label ? " · " + meta.label : "") + (meta.rest ? " · 休息日" : "") + eventText + " · 点开每小时卡片生成小日记"
        : "";
      const items = Array.isArray(data.items) ? data.items : [];
      const nowKey = (() => {
        const now = new Date();
        return now.getFullYear() + "-" + pad2(now.getMonth() + 1) + "-" + pad2(now.getDate());
      })();
      // 面板只提供「今天」的改/删；历史与未来只在别处维护（后端同样会拒绝）。
      const canEditToday = !data.day || data.day === nowKey;
      if (data.day && !canEditToday && day) {
        day.textContent += " · 历史/未来只读（只有今天能改）";
      }
      const tree = Array.isArray(data.tree) ? data.tree : (Array.isArray(data.schedule_tree) ? data.schedule_tree : []);
      const blind = String(data.blind || "");
      if (!tree.length) {
        const empty = document.createElement("div");
        empty.className = "sub";
        empty.textContent = blind || "暂无日程，点“刷新”或“重生日程”。";
        box.appendChild(empty);
        return;
      }
      tree.forEach((block) => {
        const details = document.createElement("details");
        details.className = "schedule-group";
        details.open = Boolean(block.current);
        const summary = document.createElement("summary");
        const time = document.createElement("span");
        time.className = "time";
        time.textContent = (block.start || "") + "-" + (block.end || "");
        const label = document.createElement("span");
        label.className = "title";
        const groupSummary = String(block.summary || "").trim();
        label.textContent = groupSummary || block.title || "";
        const place = document.createElement("span");
        place.className = "place";
        place.textContent = "";
        summary.appendChild(time);
        summary.appendChild(label);
        summary.appendChild(place);
        details.appendChild(summary);
        (block.children || []).forEach((hour) => {
          const hourBox = document.createElement("div");
          hourBox.className = "schedule-hour" + (hour.current ? " current" : "") + (hour.doing ? " doing" : "");
          hourBox.dataset.eventId = hour.id || (hour.start + "-" + hour.end);
          hourBox.dataset.start = hour.start || "";
          hourBox.tabIndex = 0;
          hourBox.setAttribute("role", "button");
          hourBox.setAttribute("aria-label", (hour.start || "") + "-" + (hour.end || "") + " " + (hour.activity || "") + "，点击生成小日记");
          const line = document.createElement("div");
          line.className = "schedule-hour-row";
          const hourTime = document.createElement("span");
          hourTime.className = "time";
          hourTime.textContent = (hour.start || "") + "-" + (hour.end || "");
          const activity = document.createElement("span");
          activity.className = "a";
          activity.textContent = dateHourLabel(data.day, hour.start, hour.activity || "");
          if (hour.event) {
            const tag = document.createElement("span");
            tag.className = "schedule-event-tag";
            tag.textContent = eventTagText(hour.event);
            activity.appendChild(tag);
          }
          if (hour.doing) {
            const doingTag = document.createElement("span");
            doingTag.className = "schedule-doing-tag";
            doingTag.textContent = "进行中";
            activity.appendChild(doingTag);
          }
          // 弱点1 第 6 条：行尾小灰字标出来源（自动/聊天/过渡/面板/财务…）。
          let sourceTag = null;
          const sourceLabel = typeof window.amoScheduleSourceLabel === "function"
            ? window.amoScheduleSourceLabel(hour.source) : "";
          if (sourceLabel) {
            sourceTag = document.createElement("span");
            sourceTag.className = "schedule-source-tag";
            sourceTag.style.cssText = "color:var(--muted);font-size:11px;margin-left:6px";
            sourceTag.textContent = sourceLabel;
            activity.appendChild(sourceTag);
          }
          const metaText = [hour.place || "", hour.vehicle || "", hour.current ? "现在" : ""].filter(Boolean).join(" · ");
          const metaLine = document.createElement("span");
          metaLine.className = "meta";
          metaLine.textContent = metaText;
          const chev = document.createElement("span");
          chev.className = "chev";
          chev.textContent = "›";
          // 2026-11-17：日程行不再提供"改/删"按钮（用户不希望被人工操控日程）；
          // 编辑/删除接口保留给后台审计与命令行，面板只读。
          line.appendChild(hourTime);
          line.appendChild(activity);
          line.appendChild(metaLine);
          line.appendChild(chev);
          const diary = document.createElement("div");
          diary.className = "schedule-diary";
          diary.addEventListener("click", (event) => event.stopPropagation());
          hourBox.appendChild(line);
          hourBox.appendChild(diary);
          const open = () => toggleHourDiary(hourBox, hour);
          hourBox.addEventListener("click", open);
          hourBox.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              open();
            }
          });
          details.appendChild(hourBox);
        });
        box.appendChild(details);
      });
      if (blind) {
        const hint = document.createElement("div");
        hint.className = "schedule-blind";
        hint.textContent = blind;
        box.appendChild(hint);
      }
    }

    function shiftScheduleDay(offset) {
      // 0=今天 1=明天 2=后天（相对今天算，不依赖当前看的日期）
      const base = new Date();
      base.setDate(base.getDate() + Number(offset || 0));
      const key = base.getFullYear() + "-" + pad2(base.getMonth() + 1) + "-" + pad2(base.getDate());
      const label = document.getElementById("scheduleDay");
      const word = Number(offset) === 1 ? "明天" : (Number(offset) === 2 ? "后天" : "今天");
      document.querySelectorAll("[data-sched]").forEach((button) => {
        button.classList.toggle("active", Number(button.dataset.sched || 0) === Number(offset || 0));
      });
      if (label) label.textContent = "正在看" + word + " " + key + " …";
      const list = document.getElementById("scheduleList");
      if (list) list.innerHTML = '<div class="sub">加载中…</div>';
      if (typeof toast === "function") toast("已切到" + word + "（" + key + "）");
      loadSchedule(key);
    }
    window.__schedJump = shiftScheduleDay;

    function pad2(n) {
      return (n < 10 ? "0" : "") + n;
    }

    function scheduleTodayKey() {
      const now = new Date();
      return now.getFullYear() + "-" + pad2(now.getMonth() + 1) + "-" + pad2(now.getDate());
    }

    function dayKey(offset) {
      const d = new Date();
      d.setDate(d.getDate() + Number(offset || 0));
      return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
    }

    const TOMORROW_EMPTY_TEXT = "明天暂时没有特别的安排，又是充满未知的一天哦~";

    async function loadTodo() {
      const box = document.getElementById("scheduleTodo");
      const button = document.getElementById("btnTodoToggle");
      if (!box) return;
      if (!box.hidden) {
        box.hidden = true;
        if (button) button.textContent = "点击查看明日待办";
        return;
      }
      box.hidden = false;
      box.textContent = "加载中…";
      if (button) button.textContent = "收起明日待办";
      const day = dayKey(1);
      let items = [];
      try {
        const result = objectFrom(await call("todo", { date: day }, { get: true }));
        items = Array.isArray(result.items) ? result.items : [];
      } catch (error) {
        box.textContent = friendlyError(error, "明天的待办暂时没读到。");
        return;
      }
      box.textContent = "";
      const card = document.createElement("div");
      card.className = "schedule-todo-card";
      const head = document.createElement("div");
      head.className = "label";
      head.textContent = "明天 " + day + " 的待办";
      card.appendChild(head);
      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "sub";
        empty.textContent = TOMORROW_EMPTY_TEXT;
        card.appendChild(empty);
      } else {
        items.forEach((item) => {
          const row = document.createElement("div");
          row.className = "schedule-todo-row";
          const time = document.createElement("span");
          time.className = "time";
          time.textContent = String(item.start || "") || "晚上";
          const text = document.createElement("span");
          text.className = "a";
          const place = String(item.place || "");
          text.textContent = String(item.activity || "安排") + (place ? "（" + place + "）" : "");
          row.appendChild(time);
          row.appendChild(text);
          card.appendChild(row);
        });
      }
      box.appendChild(card);
    }

    const todoToggleButton = document.getElementById("btnTodoToggle");
    if (todoToggleButton) todoToggleButton.addEventListener("click", () => loadTodo());

    async function loadSchedule(day) {
      try {
        const result = day
          ? await call("schedule", { date: day }, { get: true })
          : await call("schedule");
        const data = objectFrom(result);
        if (data && data.status === "generating") {
          document.getElementById("scheduleList").textContent =
            "（她正咬着笔头在写日记呢，过几秒再刷新看看）";
          return;
        }
        renderSchedule(data);
      } catch (error) {
        document.getElementById("scheduleList").textContent = friendlyError(error, "她迷糊了，日程暂时没接上，点刷新再试试。");
      }
    }

    function pickCoreKeys(mood) {
      return MOOD_KEYS.slice().sort((a, b) => {
        const dv = Number(mood[b] || 0) - Number(mood[a] || 0);
        return dv || MOOD_KEYS.indexOf(a) - MOOD_KEYS.indexOf(b);
      }).slice(0, 6);
    }

    function emotionText(key, value) {
      const v = Number(value || 0);
      if (key === "开心") return v >= 80 ? "满脸笑意，想转圈圈" : (v >= 40 ? "心情不错" : "有点提不起劲");
      if (key === "委屈") return v >= 60 ? "呜呜呜想哭" : (v >= 20 ? "眼眶有点红，想求抱抱" : "还算平静");
      if (key === "生气") return v >= 60 ? "气鼓鼓，想摔手机" : (v >= 20 ? "嘟着嘴，不想理人" : "没在生气");
      if (key === "依恋") return v >= 60 ? "像块小年糕一样黏人" : (v >= 30 ? "有点想他" : "各自忙");
      if (key === "疲惫") return v >= 60 ? "累到不想说话" : (v >= 30 ? "有点乏" : "精神还不错");
      if (key === "平静") return v >= 60 ? "心里还算安稳" : (v >= 30 ? "有点起伏" : "静不下来");
      if (key === "兴奋") return v >= 60 ? "眼睛发亮" : "没什么波动";
      return v >= 60 ? "情绪很满" : (v >= 30 ? "有一点" : "淡淡的");
    }

    function barColor(key, value) {
      const v = Number(value || 0);
      if (key === "开心" || key === "兴奋" || key === "平静" || key === "依恋") {
        return v >= 60 ? "linear-gradient(90deg,#fb7185,#fbbf24)" : "linear-gradient(90deg,#38bdf8,#f9a8d4)";
      }
      if (key === "生气" || key === "暴躁") return "linear-gradient(90deg,#ef4444,#f87171)";
      return "linear-gradient(90deg,#60a5fa,#818cf8)";
    }

    function applyMoodTheme(mood) {
      const shell = document.querySelector(".status-shell");
      if (!shell) return;
      shell.classList.remove("mood-warm", "mood-cold", "mood-anger", "mood-extreme");
      const happy = Math.max(Number(mood["开心"] || 0), Number(mood["兴奋"] || 0), Number(mood["平静"] || 0));
      const sad = Math.max(Number(mood["疲惫"] || 0), Number(mood["委屈"] || 0), Number(mood["郁闷"] || 0));
      const anger = Math.max(Number(mood["生气"] || 0), Number(mood["暴躁"] || 0));
      shell.classList.add(happy >= sad ? "mood-warm" : "mood-cold");
      if (anger >= 70) shell.classList.add("mood-anger");
      if (sad > 80 || anger > 80 || happy > 90) shell.classList.add("mood-extreme");
    }

    function drawTrend(history) {
      const canvas = document.getElementById("moodTrend");
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      const dpr = window.devicePixelRatio || 1;
      const size = 120;
      canvas.style.width = "120px";
      canvas.style.height = "100px";
      canvas.width = size * dpr;
      canvas.height = size * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, size, 100);
      const points = (history || []).slice(-7).map((row) => Math.max(0, Math.min(100, Number(row.score || 0))));
      if (points.length < 2) {
        ctx.fillStyle = "rgba(120,120,140,.6)";
        ctx.font = "11px sans-serif";
        ctx.fillText("趋势积累中", 18, 54);
        return;
      }
      const pad = 10;
      const step = (size - pad * 2) / (points.length - 1);
      const y = (v) => 90 - (v / 100) * 70;
      ctx.strokeStyle = "rgba(120,150,200,.25)";
      ctx.beginPath();
      ctx.moveTo(pad, y(50));
      ctx.lineTo(size - pad, y(50));
      ctx.stroke();
      ctx.beginPath();
      points.forEach((v, i) => { const x = pad + i * step; if (i === 0) ctx.moveTo(x, y(v)); else ctx.lineTo(x, y(v)); });
      ctx.strokeStyle = "#fb7185";
      ctx.lineWidth = 2;
      ctx.stroke();
      points.forEach((v, i) => {
        ctx.beginPath();
        ctx.arc(pad + i * step, y(v), 2.5, 0, Math.PI * 2);
        ctx.fillStyle = "#fb7185";
        ctx.fill();
      });
      ctx.fillStyle = "rgba(120,120,140,.7)";
      ctx.font = "10px sans-serif";
      ctx.fillText("7日情绪", pad, 12);
    }

    function drawRadar(mood, keys = MOOD_KEYS) {
      const canvas = document.getElementById("radar");
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      const dpr = window.devicePixelRatio || 1;
      const size = Math.min(240, Math.max(190, canvas.parentElement.clientWidth - 10));
      canvas.style.width = size + "px";
      canvas.style.height = size + "px";
      canvas.width = size * dpr;
      canvas.height = size * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const cx = size / 2;
      const cy = size / 2;
      const radius = size * 0.29;
      const count = keys.length;
      const angle = (i) => -Math.PI / 2 + (i * Math.PI * 2) / count;
      ctx.clearRect(0, 0, size, size);

      const cssVars = getComputedStyle(document.documentElement);
      const gridColor = cssVars.getPropertyValue("--canvas-grid").trim() || "rgba(44,39,56,.12)";
      const axisColor = cssVars.getPropertyValue("--canvas-axis").trim() || "rgba(44,39,56,.10)";
      const labelColor = cssVars.getPropertyValue("--canvas-label").trim() || "rgba(44,39,56,.68)";
      const fillColor = cssVars.getPropertyValue("--canvas-fill").trim() || "rgba(232,72,154,.16)";
      const strokeColor = cssVars.getPropertyValue("--pink").trim() || "#0ea5e9";
      ctx.strokeStyle = gridColor;
      ctx.lineWidth = 1;
      for (let ring = 1; ring <= 4; ring += 1) {
        ctx.beginPath();
        for (let i = 0; i < count; i += 1) {
          const r = (radius * ring) / 4;
          const x = cx + Math.cos(angle(i)) * r;
          const y = cy + Math.sin(angle(i)) * r;
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.closePath();
        ctx.stroke();
      }

      ctx.strokeStyle = axisColor;
      for (let i = 0; i < count; i += 1) {
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.cos(angle(i)) * radius, cy + Math.sin(angle(i)) * radius);
        ctx.stroke();
      }

      ctx.font = "12px -apple-system, BlinkMacSystemFont, 'PingFang SC', sans-serif";
      ctx.fillStyle = labelColor;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (let i = 0; i < count; i += 1) {
        const x = cx + Math.cos(angle(i)) * (radius + 20);
        const y = cy + Math.sin(angle(i)) * (radius + 20);
        ctx.fillText(keys[i], x, y);
      }

      const points = keys.map((key, i) => {
        const value = Math.max(0, Math.min(100, Number(mood[key] || 0)));
        const r = (radius * value) / 100;
        return [cx + Math.cos(angle(i)) * r, cy + Math.sin(angle(i)) * r];
      });
      if (points.length) {
        ctx.beginPath();
        points.forEach(([x, y], i) => { if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
        ctx.closePath();
        ctx.fillStyle = fillColor;
        ctx.fill();
        ctx.strokeStyle = strokeColor;
        ctx.lineWidth = 2;
        ctx.stroke();
        points.forEach(([x, y]) => {
          ctx.beginPath();
          ctx.arc(x, y, 3, 0, Math.PI * 2);
          ctx.fillStyle = strokeColor;
          ctx.fill();
        });
      }
    }

    async function action(name, button) {
      const old = button.textContent;
      button.disabled = true;
      button.textContent = "处理中…";
      try {
        const result = await call(name);
        if (result && result.code && result.code !== 0) throw new Error(result.msg || "操作失败");
        toast("完成");
        await load();
      } catch (error) {
        toast(error.message || "操作失败", true);
      } finally {
        button.disabled = false;
        button.textContent = old;
      }
    }

    document.getElementById("btnRefresh").addEventListener("click", async (event) => {
      const btn = event.currentTarget;
      btn.disabled = true;
      try { await load(); toast("已刷新"); } catch (e) { toast(e.message || "刷新失败", true); }
      finally { btn.disabled = false; }
    });
    // 刷新天气是副作用（请求和风 + 可能调心情），走写路由 /weather/refresh（只收 POST）；
    // 读缓存用 /weather 或 /weather/brief，都是 GET。
    document.getElementById("btnWeather").addEventListener("click", (e) => action("weather/refresh", e.currentTarget));
    document.getElementById("btnSchedule").addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      try {
        await call("schedule_regen");
        await loadSchedule();
        showTab("schedule");
        toast("日程已重建");
      } catch (error) {
        toast("重建失败：" + (error && error.message ? error.message : error), true);
      } finally {
        btn.disabled = false;
      }
    });
    document.getElementById("btnWorld").addEventListener("click", async (e) => {
      await action("worldview_regen", e.currentTarget);
      await loadWorld();
    });
    const worldExpandButton = document.getElementById("worldExpandAll");
    if (worldExpandButton) worldExpandButton.addEventListener("click", () => {
      const sections = Array.from(document.querySelectorAll("#worldDoc details.world-section"));
      const allOpen = sections.length > 0 && sections.every((section) => section.open);
      sections.forEach((section) => { section.open = !allOpen; });
      worldExpandButton.textContent = allOpen ? "展开全部" : "收起全部";
    });
    document.getElementById("worldDialect").addEventListener("change", async (event) => {
      const select = event.currentTarget;
      const value = select.value;
      const plain = value === "普通话";
      select.disabled = true;
      try {
        const changes = plain
          ? { dialect_style: "普通话", dialect_enabled: false, create_dialect_kb: false }
          : { dialect_style: value, dialect_enabled: true, create_dialect_kb: true };
        const result = await call("config_save", { changes });
        if (result && result.code && result.code !== 0) throw new Error(result.msg || "切换失败");
        toast(plain ? "已切到普通话：方言已关闭（不再查方言库）" : "语种已切换：" + value);
      } catch (error) {
        toast("切换失败：" + (error.message || error), true);
      } finally {
        select.disabled = false;
      }
    });
    document.getElementById("btnKb").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      const select = document.getElementById("worldDialect");
      const style = select ? select.value : "贵州话";
      if (style === "普通话") {
        toast("普通话不需要方言库：已保持关闭");
        return;
      }
      const old = button.textContent;
      button.disabled = true;
      button.textContent = "重建中…";
      try {
        const saved = await call("config_save", { changes: { dialect_style: style, dialect_enabled: true } });
        if (saved && saved.code && saved.code !== 0) throw new Error(saved.msg || "保存语种失败");
        const result = await call("kb_regen", { dialect_style: style });
        if (result && result.code && result.code !== 0) throw new Error(result.msg || "重建失败");
        toast("语种库已重建：" + style);
        await load();
      } catch (error) {
        toast("重建失败：" + (error.message || error), true);
      } finally {
        button.disabled = false;
        button.textContent = old;
      }
    });
    document.getElementById("btnScenicKb").addEventListener("click", (e) => action("scenic_kb_regen", e.currentTarget));

    function worldText(value) {
      if (Array.isArray(value)) return value.join("、");
      return value === undefined || value === null ? "" : String(value);
    }

    function worldPreview(text, limit) {
      const clean = String(text || "").replace(/\s+/g, " ").trim();
      const max = limit || 46;
      return clean.length > max ? clean.slice(0, max) + "…" : clean;
    }

    function worldFact(label, value, wide) {
      const text = worldText(value).trim();
      if (!text) return null;
      const item = document.createElement("div");
      item.className = "world-fact" + (wide ? " wide" : "");
      const key = document.createElement("span");
      key.textContent = label;
      const val = document.createElement("strong");
      val.textContent = text;
      item.appendChild(key);
      item.appendChild(val);
      return item;
    }

    function worldSection(label, value) {
      const text = worldText(value).trim();
      if (!text) return null;
      const details = document.createElement("details");
      details.className = "world-section";
      const summary = document.createElement("summary");
      const title = document.createElement("span");
      title.className = "world-section-title";
      title.textContent = label;
      const preview = document.createElement("span");
      preview.className = "world-preview";
      preview.textContent = worldPreview(text);
      summary.appendChild(title);
      summary.appendChild(preview);
      details.appendChild(summary);
      const body = document.createElement("div");
      body.className = "world-section-body";
      body.textContent = text;
      details.appendChild(body);
      return details;
    }

    function renderWorld(world) {
      const box = document.getElementById("worldDoc");
      box.textContent = "";
      const name = worldText(world.name).trim();
      const age = worldText(world.age).trim();
      const city = worldText(world.city).trim();
      const occupation = worldText(world.occupation).trim();
      const workplace = worldText(world.workplace).trim();
      const personality = worldText(world.personality).trim();
      const hobbies = worldText(world.hobbies).trim();
      const daily = worldText(world.daily_life).trim();
      const rules = worldText(world.world_rules).trim();
      const summaryText = worldText(world.summary).trim();
      const hasAny = [name, age, city, occupation, workplace, personality, hobbies, daily, rules, summaryText].some(Boolean);
      if (!hasAny) {
        box.textContent = "还没有生成世界文档。";
        const emptyExpand = document.getElementById("worldExpandAll");
        if (emptyExpand) emptyExpand.hidden = true;
        return;
      }

      const hero = document.createElement("div");
      hero.className = "world-hero";
      const avatar = document.createElement("div");
      avatar.className = "world-avatar";
      avatar.textContent = (name || "她").slice(0, 1);
      const heroText = document.createElement("div");
      const heroName = document.createElement("div");
      heroName.className = "world-name";
      heroName.textContent = name || "未命名";
      const heroSub = document.createElement("div");
      heroSub.className = "world-sub";
      heroSub.textContent = [age ? age + " 岁" : "", city, occupation].filter(Boolean).join(" · ");
      heroText.appendChild(heroName);
      if (heroSub.textContent) heroText.appendChild(heroSub);
      hero.appendChild(avatar);
      hero.appendChild(heroText);
      box.appendChild(hero);

      const facts = document.createElement("div");
      facts.className = "world-facts";
      [
        ["年龄", age ? age + " 岁" : ""],
        ["城市", city],
        ["职业", occupation],
        ["任职", workplace, true],
      ].forEach(([label, value, wide]) => {
        const item = worldFact(label, value, wide);
        if (item) facts.appendChild(item);
      });
      if (facts.children.length) box.appendChild(facts);

      const tags = document.createElement("div");
      tags.className = "world-tags";
      [["性格", personality], ["爱好", hobbies]].forEach(([label, value]) => {
        if (!value) return;
        const tag = document.createElement("span");
        tag.className = "world-tag";
        const key = document.createElement("b");
        key.textContent = label + "：";
        tag.appendChild(key);
        tag.appendChild(document.createTextNode(value));
        tags.appendChild(tag);
      });
      if (tags.children.length) box.appendChild(tags);

      [["简介", summaryText], ["日常", daily], ["世界", rules]].forEach(([label, value]) => {
        const section = worldSection(label, value);
        if (section) box.appendChild(section);
      });

      const expand = document.getElementById("worldExpandAll");
      if (expand) {
        expand.textContent = "展开全部";
        expand.hidden = !box.querySelector("details.world-section");
      }
    }

    async function loadWorld() {
      try {
        const result = await call("worldview");
        const data = objectFrom(result);
        renderWorld(data.worldview || data);
      } catch (error) {
        document.getElementById("worldDoc").textContent = "读取失败：" + (error.message || error);
      }
    }

    async function openSettingsFor(term) {
      const group = "状态";
      const button = tabButtons.find((item) => item.dataset.tab === "settings" && item.dataset.group === group);
      showTab("settings", group, button);
      if (!configItems.length) await loadSettings();
      const input = document.getElementById("settingsSearch");
      input.value = term;
      input.dispatchEvent(new Event("input"));
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    document.getElementById("periodCard").addEventListener("click", () => openSettingsFor("period"));
    document.getElementById("healthCard").addEventListener("click", () => openSettingsFor("sick"));

    function openMoodEditor() {
      const mood = (lastData && lastData.mood) || {};
      const box = document.getElementById("moodEditor");
      box.textContent = "";
      MOOD_KEYS.forEach((key) => {
        const row = document.createElement("div");
        row.className = "mood-edit-row";
        const name = document.createElement("span");
        name.textContent = key;
        const input = document.createElement("input");
        input.type = "range";
        input.min = "0"; input.max = "100"; input.step = "1";
        input.value = String(Math.round(Number(mood[key] || 0)));
        input.dataset.mood = key;
        const num = document.createElement("span");
        num.className = "num";
        num.textContent = input.value;
        input.addEventListener("input", () => { num.textContent = input.value; });
        row.appendChild(name); row.appendChild(input); row.appendChild(num);
        box.appendChild(row);
      });
      document.getElementById("moodModal").hidden = false;
    }

    document.getElementById("radar").addEventListener("click", openMoodEditor);
    document.getElementById("btnMoodClose").addEventListener("click", () => {
      document.getElementById("moodModal").hidden = true;
    });
    document.getElementById("moodModal").addEventListener("click", (event) => {
      if (event.target === event.currentTarget) event.currentTarget.hidden = true;
    });
    document.getElementById("btnMoodSave").addEventListener("click", async () => {
      const mood = {};
      document.querySelectorAll("[data-mood]").forEach((input) => {
        mood[input.dataset.mood] = Number(input.value);
      });
      try {
        await call("mood_update", { mood });
        toast("心情已更新");
        document.getElementById("moodModal").hidden = true;
        await load();
      } catch (error) {
        toast("保存失败：" + (error.message || error), true);
      }
    });

    window.addEventListener("resize", () => { if (lastData) drawRadar(lastData.mood || {}); });

    function listFrom(res) {
      if (Array.isArray(res)) return res;
      if (res && Array.isArray(res.data)) return res.data;
      if (res && Array.isArray(res.items)) return res.items;
      return [];
    }

    let usageLoaded = false;

    function usageNum(value) {
      const number = Number(value || 0);
      if (number >= 1000000) return (number / 1000000).toFixed(2) + "M";
      if (number >= 1000) return (number / 1000).toFixed(1) + "k";
      return String(number);
    }

    function renderProviderUsage(providers) {
      const box = document.getElementById("providerList");
      const sub = document.getElementById("providerSub");
      if (!box) return;
      box.textContent = "";
      if (!Array.isArray(providers) || !providers.length) {
        sub.textContent = "没有检测到已配置的模型供应商";
        return;
      }
      sub.textContent = "共 " + providers.length + " 个供应商 · 密钥来自 AstrBot 模型配置（不回显）";
      providers.forEach((provider) => {
        const row = document.createElement("div");
        row.className = "provider-row";
        const left = document.createElement("div");
        left.style.minWidth = "0";
        const name = document.createElement("div");
        const tags = [];
        if (provider.using) tags.push("主对话");
        if (provider.judge) tags.push("心流判定");
        if (!provider.enable) tags.push("已停用");
        name.textContent = (provider.id || "?") + (provider.model ? " · " + provider.model : "") + (tags.length ? "（" + tags.join(" / ") + "）" : "");
        const detail = document.createElement("div");
        detail.className = "hint";
        const balance = provider.balance || {};
        let money = "余额 —";
        if (balance.ok) {
          money = "余额 " + (balance.currency === "USD" ? "$" : "¥") + Number(balance.amount || 0).toFixed(2);
          if (balance.used !== undefined) money += "（本月已用 $" + Number(balance.used).toFixed(2) + "）";
        } else {
          money = "余额 " + (balance.error || "不支持");
        }
        detail.textContent = "今日 " + usageNum(provider.today_tokens || 0) + " tokens · " + (provider.today_calls || 0) + " 次 · " + money;
        left.appendChild(name);
        left.appendChild(detail);
        row.appendChild(left);
        box.appendChild(row);
      });
    }

    function diaryDayString(offset) {
      const d = new Date();
      d.setDate(d.getDate() + Number(offset || 0));
      const pad = (n) => String(n).padStart(2, "0");
      return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
    }

    async function loadDiary() {
      const statsEl = document.getElementById("diaryStats");
      statsEl.textContent = "读取中…";
      loadDiaryLive();
      try {
        const tree = objectFrom(await call("diary/tree")) || {};
        const total = Number(tree.total || 0);
        statsEl.textContent = "已写 " + total + " 篇" + (tree.latest ? " · 最近 " + tree.latest : "");
        document.getElementById("diaryRoot").textContent = tree.root ? "保存位置：" + tree.root + "（不参与插件备份）" : "";
        renderDiaryTree(tree);
      } catch (error) {
        statsEl.textContent = "读取失败：" + (error && error.message ? error.message : "接口未就绪");
      }
    }

    // 「今天 · 还在写」：读 /diary/read 的 live 段（连载小记），第一人称逐段展示。
    async function loadDiaryLive() {
      const box = document.getElementById("diaryLiveText");
      const sub = document.getElementById("diaryLiveSub");
      if (!box) return;
      box.textContent = "";
      const loading = document.createElement("div");
      loading.className = "sub";
      loading.textContent = "读取中…";
      box.appendChild(loading);
      const DEFAULT_HINT = "她今天的连载小记，块结束一会儿后自动接一段，第二天最终版写好后收束。";
      try {
        const data = objectFrom(await call("diary/read", { date: diaryDayString(0) })) || {};
        const entries = Array.isArray(data.live) ? data.live : [];
        if (sub) {
          sub.textContent = data.live_pending
            ? "刚过完一块，几分钟后会自动接上一段。"
            : DEFAULT_HINT;
        }
        box.textContent = "";
        if (!entries.length) {
          const empty = document.createElement("div");
          empty.className = "sub";
          empty.textContent = data.locked
            ? "今天的连载已经收束进最终版日记了。"
            : "今天还没开始写，块过完五到十分钟后再来看看。";
          box.appendChild(empty);
          return;
        }
        entries.forEach((entry) => {
          const para = document.createElement("p");
          para.className = "diary-live-para";
          para.style.cssText = "white-space:pre-wrap;line-height:1.9;margin:0 0 8px";
          para.textContent = String(entry.text || "");
          box.appendChild(para);
          const meta = document.createElement("div");
          meta.className = "diary-meta";
          const stamp = Number(entry.ts || 0);
          let hhmm = "";
          if (stamp > 0) {
            const clock = new Date(stamp * 1000);
            hhmm = String(clock.getHours()).padStart(2, "0") + ":" + String(clock.getMinutes()).padStart(2, "0");
          }
          meta.textContent = [hhmm, entry.mood || ""].filter(Boolean).join(" · ");
          box.appendChild(meta);
        });
      } catch (error) {
        box.textContent = "";
        const fail = document.createElement("div");
        fail.className = "sub";
        fail.textContent = "连载读取失败：" + (error && error.message ? error.message : "接口未就绪");
        box.appendChild(fail);
      }
    }

    function renderDiaryTree(tree) {
      const box = document.getElementById("diaryTree");
      box.textContent = "";
      const years = Array.isArray(tree.years) ? tree.years : [];
      if (!years.length) {
        box.innerHTML = '<div class="sub">还没有日记，点「写昨天」生成第一篇。</div>';
        return;
      }
      years.forEach((year) => {
        const yearBox = document.createElement("details");
        yearBox.open = false;
        const yearTitle = document.createElement("summary");
        const count = (year.months || []).reduce((n, m) => n + ((m.days || []).length), 0);
        yearTitle.textContent = year.year + " 年（" + count + " 篇）";
        yearBox.appendChild(yearTitle);
        (year.months || []).forEach((month) => {
          const monthBox = document.createElement("details");
          monthBox.open = false;
          const monthTitle = document.createElement("summary");
          monthTitle.textContent = month.month + " 月（" + ((month.days || []).length) + " 篇）";
          monthBox.appendChild(monthTitle);
          (month.days || []).forEach((day) => {
            const btn = document.createElement("button");
            btn.className = "ghost";
            btn.type = "button";
            btn.style.display = "block";
            btn.textContent = day.date + "（" + (day.chars || 0) + " 字）";
            btn.addEventListener("click", () => readDiary(day.date));
            monthBox.appendChild(btn);
          });
          yearBox.appendChild(monthBox);
        });
        box.appendChild(yearBox);
      });
    }

    async function readDiary(date) {
      const textEl = document.getElementById("diaryText");
      document.getElementById("diaryTitle").textContent = date;
      document.getElementById("diaryClose").hidden = false;
      textEl.textContent = "读取中…";
      try {
        const data = objectFrom(await call("diary/read", { date })) || {};
        textEl.textContent = data.text || "（这篇是空的）";
      } catch (error) {
        textEl.textContent = "读取失败：" + (error && error.message ? error.message : "接口未就绪");
      }
    }

    function closeDiary() {
      document.getElementById("diaryClose").hidden = true;
      document.getElementById("diaryTitle").textContent = "选择一篇日记";
      document.getElementById("diaryText").textContent = "从上面的年 / 月 / 日里点一天，就能读那天的日记。";
    }

    // 日记质检：只读 GET /diary/audit（复用既有审计入口，不新增写路由）
    function auditDiaryDay() {
      const title = String(document.getElementById("diaryTitle").textContent || "");
      return /^\d{4}-\d{2}-\d{2}$/.test(title) ? title : diaryDayString(0);
    }

    async function loadDiaryAudit() {
      const el = document.getElementById("diaryAuditText");
      if (!el) return;
      el.hidden = false;
      el.textContent = "读取中…";
      try {
        const data = objectFrom(await window.PanelApi.call("diary/audit", { day: auditDiaryDay() }, { get: true })) || {};
        const report = data.report || null;
        const lines = [];
        if (report) {
          const counts = report.counts || {};
          lines.push("HARD " + (counts.hard || 0) + " 条 · WARN " + (counts.warn || 0) + " 条 · 已审 " + (counts.blocks || 0) + " 块");
        }
        lines.push(data.text || "还没有日记质检记录");
        el.textContent = lines.join("\n");
      } catch (error) {
        el.textContent = "质检读取失败：" + window.PanelApi.friendlyError(error, "接口未就绪");
      }
    }

    document.getElementById("diaryClose").addEventListener("click", closeDiary);
    document.getElementById("diaryImport").addEventListener("click", async () => {
      const path = window.prompt("把要导入的日记文件夹路径粘贴进来（里面要有 年/月/日 结构，或「我的日记YYYY年MM月DD日.txt」）：", "");
      if (!path) return;
      try {
        const data = objectFrom(await call("diary/import", { path: path.trim() })) || {};
        toast("已导入 " + (data.copied || 0) + " 篇（跳过 " + (data.skipped || 0) + "，失败 " + (data.failed || 0) + "）");
        loadDiary();
      } catch (error) {
        toast("导入失败：" + (error && error.message ? error.message : "接口未就绪"), true);
      }
    });
    document.getElementById("diaryRefresh").addEventListener("click", () => loadDiary());
    document.getElementById("diaryLiveRefresh").addEventListener("click", () => loadDiaryLive());
    document.getElementById("diaryOpen").addEventListener("click", async () => {
      try {
        const data = objectFrom(await call("diary/open")) || {};
        toast(data.opened ? "已打开日记文件夹" : "服务器没有打开文件夹（路径：" + (data.root || "") + "）", !data.opened);
      } catch (error) { toast("打开失败：" + error.message, true); }
    });
    document.getElementById("diaryAudit").addEventListener("click", () => loadDiaryAudit());
    document.getElementById("diaryWrite").addEventListener("click", async () => {
      const date = diaryDayString(-1);
      const button = document.getElementById("diaryWrite");
      button.disabled = true;
      button.textContent = "写中…";
      try {
        const data = objectFrom(await call("diary/write", { date })) || {};
        toast("已写：" + (data.date || date));
        await loadDiary();
        await readDiary(data.date || date);
      } catch (error) {
        toast("写日记失败：" + (error && error.message ? error.message : "接口未就绪"), true);
      } finally {
        button.disabled = false;
        button.textContent = "写昨天";
      }
    });

    async function loadUsage(force) {
      const setText = (id, text) => {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
      };
      if (usageLoaded && !force) return;
      setText("usageIn", "读取中…");
      try {
        const data = objectFrom(await call("usage")) || {};
        if (data.enabled === false) {
          setText("usageIn", "已关闭");
          setText("usageInSub", "在设置里打开 usage_stats_enabled");
          return;
        }
        usageLoaded = true;
        const today = data.today || {};
        if (today.error) {
          setText("usageInSub", String(today.error));
        }
        const week = data.week || {};
        const total = data.total || {};
        const money = (value) => "¥" + Number(value || 0).toFixed(3);
        setText("usageIn", usageNum(today.input_miss) + " / " + usageNum(today.input_cached));
        setText("usageInSub", "未命中 / 命中缓存");
        setText("usageOut", usageNum(today.output));
        setText("usageCalls", (today.calls || 0) + " 次调用");
        const rate = Number(today.cache_rate || 0);
        setText("usageCache", (rate * 100).toFixed(1) + "%");
        const bar = document.getElementById("usageCacheBar");
        if (bar) bar.style.width = Math.max(2, Math.min(100, rate * 100)) + "%";
        setText("usageWeek", "近 7 天 " + ((Number(week.cache_rate || 0)) * 100).toFixed(1) + "%");
        setText("usageCost", money(today.cost));
        setText("usageCostSub", "近 7 天 " + money(week.cost) + " · 累计 " + money(total.cost));
        const balance = data.balance || {};
        if (balance.ok) {
          setText("balanceValue", (balance.currency === "USD" ? "$" : "¥") + Number(balance.amount || 0).toFixed(2));
          setText("balanceSub", (balance.available === false ? "余额不足 · " : "") +
            "充值 " + Number(balance.topped_up || 0).toFixed(2) + " · 赠金 " + Number(balance.granted || 0).toFixed(2));
        } else {
          setText("balanceValue", "—");
          setText("balanceSub", balance.error || "查询失败");
        }
        const models = Array.isArray(today.by_model) ? today.by_model : [];
        setText("usageModels", models.length
          ? "今日按模型：" + models.map((item) => (item.model || "?") + " " +
              usageNum((item.input_miss || 0) + (item.input_cached || 0) + (item.output || 0))).join(" · ")
          : "");
        renderProviderUsage(data.providers || []);
      } catch (error) {
        setText("usageIn", "读取失败");
        setText("usageInSub", error && error.message ? error.message : "接口未就绪");
      }
    }

    function giftTime(ts) {
      const value = Number(ts);
      if (!value) return "";
      const date = new Date(value * 1000);
      const now = new Date();
      const pad = (n) => String(n).padStart(2, "0");
      if (date.toDateString() === now.toDateString()) return pad(date.getHours()) + ":" + pad(date.getMinutes());
      return (date.getMonth() + 1) + "-" + pad(date.getDate()) + " " + pad(date.getHours()) + ":" + pad(date.getMinutes());
    }

    function giftRow(item, meta) {
      const row = document.createElement("div");
      row.className = "profile-item";
      const name = document.createElement("div");
      name.className = "name";
      name.textContent = item;
      const sub = document.createElement("div");
      sub.className = "meta";
      sub.textContent = meta;
      row.appendChild(name);
      row.appendChild(sub);
      return row;
    }

    async function decideWish(id, action) {
      if (!id) return;
      try {
        const res = await call("gift/wish/" + action, { id });
        toast((res && res.msg) || (action === "approve" ? "已确认" : "已删除"));
        await loadGift();
      } catch (error) {
        toast((error && error.message) || "操作失败", true);
      }
    }

    async function loadGift() {
      const wishesBox = document.getElementById("giftWishes");
      const giftsBox = document.getElementById("giftList");
      if (!wishesBox || !giftsBox) return;
      wishesBox.innerHTML = '<div class="sub">加载中…</div>';
      giftsBox.innerHTML = '<div class="sub">加载中…</div>';
      try {
        const data = objectFrom(await call("gift"));
        const wishes = Array.isArray(data.wishes) ? data.wishes : [];
        const gifts = Array.isArray(data.gifts) ? data.gifts : [];
        wishesBox.innerHTML = "";
        giftsBox.innerHTML = "";
        if (data.enabled === false) {
          const tip = giftRow("礼物功能未开启", "在「基础」里打开 gift_enable");
          wishesBox.appendChild(tip);
          return;
        }
        const pending = Array.isArray(data.pending) ? data.pending : [];
        const pendingWrap = document.getElementById("giftPendingWrap");
        const pendingBox = document.getElementById("giftPending");
        if (pendingWrap && pendingBox) {
          pendingBox.innerHTML = "";
          pendingWrap.hidden = pending.length === 0;
          pending.forEach((wish) => {
            const row = giftRow(wish.item || "", "她随口提的 · 确认后才进心愿墙");
            const actions = document.createElement("div");
            actions.className = "row";
            actions.style.gap = "8px";
            const ok = document.createElement("button");
            ok.className = "tab";
            ok.type = "button";
            ok.textContent = "确认";
            ok.addEventListener("click", () => decideWish(wish.id, "approve"));
            const no = document.createElement("button");
            no.className = "ghost";
            no.type = "button";
            no.textContent = "删除";
            no.addEventListener("click", () => decideWish(wish.id, "reject"));
            actions.appendChild(ok);
            actions.appendChild(no);
            row.appendChild(actions);
            pendingBox.appendChild(row);
          });
        }
        if (!wishes.length) {
          wishesBox.appendChild(giftRow("还没有心愿", "她逛街看到喜欢的小东西时会记下来"));
        } else {
          wishes.forEach((wish) => {
            const meta = [giftTime(wish.ts), wish.place || ""].filter(Boolean).join(" · ");
            wishesBox.appendChild(giftRow(wish.item || "", meta));
          });
        }
        if (!gifts.length) {
          giftsBox.appendChild(giftRow("还没有收到的礼物", "说一句「送围巾」试试"));
        } else {
          gifts.forEach((gift) => {
            const kind = { small: "小礼物", valuable: "贵重小件", vehicle: "车", home: "房", luxury: "贵重" }[gift.kind] || "礼物";
            const meta = [kind, giftTime(gift.ts), gift.where || ""].filter(Boolean).join(" · ");
            giftsBox.appendChild(giftRow(gift.item || "", meta));
          });
        }
        const sub = document.getElementById("giftSub");
        if (sub) sub.textContent = "心愿 " + wishes.length + " 件 · 礼物 " + gifts.length + " 件";
      } catch (error) {
        wishesBox.textContent = "";
        const sub = document.createElement("div");
        sub.className = "sub";
        sub.textContent = "读取失败：" + (error && error.message ? error.message : "接口未就绪");
        wishesBox.appendChild(sub);
      }
    }

    // ---------- 穿搭 ----------
    const WARDROBE_CATS = [
      { key: "coat", label: "外套" },
      { key: "top", label: "上衣（衬衫）" },
      { key: "bottom", label: "裤子" },
      { key: "skirt", label: "裙子" },
      { key: "shoes", label: "鞋子" },
    ];

    function wardrobeRow(label, value) {
      const row = document.createElement("div");
      row.className = "profile-item wardrobe-line";
      const name = document.createElement("div");
      name.className = "wardrobe-line-label";
      name.textContent = label + "：";
      const text = document.createElement("div");
      text.className = "wardrobe-line-value";
      text.textContent = value || "—";
      row.appendChild(name);
      row.appendChild(text);
      return row;
    }

    let wardrobeData = {};
    let wardrobeSel = { top: "", bottom: "", shoes: "" };
    let wardrobeSwimSel = { top: "", bottom: "" };
    let wardrobeDirty = false;
    let wardrobeMode = "";

    function wardrobeNames(sel) {
      const names = { coat: "", top: "", bottom: "", skirt: "", shoes: "" };
      (wardrobeData.categories || []).forEach((cat) => {
        (cat.items || []).forEach((item) => {
          if (item.id && item.id === sel[cat.key]) names[cat.key] = item.name || "";
        });
      });
      return names;
    }

    function wardrobeChip(item, category) {
      const selectable = wardrobeMode === "day" || wardrobeMode === "swim";
      const sel = wardrobeMode === "swim" ? wardrobeSwimSel : wardrobeSel;
      const selected = selectable && !!(item.id && item.id === sel[category]);
      const chip = document.createElement("span");
      chip.className = "wardrobe-chip" + (item.source === "gift" ? " gift" : "") + (selected ? " selected" : "");
      chip.title = !selectable
        ? "点 × 删除"
        : (selected ? "已选中（点别的可换）" : (item.source === "gift" ? "他送的，点一下选它" : "点一下选它"));
      const name = document.createElement("span");
      name.textContent = (selected ? "✓ " : "") + (item.name || "");
      chip.appendChild(name);
      const x = document.createElement("button");
      x.type = "button";
      x.className = "x";
      x.title = "删除";
      x.setAttribute("aria-label", "删除 " + (item.name || ""));
      x.textContent = "×";
      x.addEventListener("click", (event) => {
        event.stopPropagation();
        confirmWardrobeDelete(item, chip);
      });
      chip.appendChild(x);
      chip.addEventListener("click", () => selectWardrobeItem(item, category));
      return chip;
    }

    function selectWardrobeItem(item, category) {
      if (!item || !item.id) return;
      if (wardrobeMode === "swim") {
        if (category !== "top" && category !== "bottom") return;
        wardrobeSwimSel[category] = item.id;
        wardrobeDirty = true;
        renderWardrobe(wardrobeData);
        return;
      }
      if (wardrobeMode !== "day") return;
      wardrobeSel[category] = item.id;
      if (category === "skirt" && /连衣裙|长裙|连身裙|睡裙/.test(item.name || "")) {
        wardrobeSel.top = "";
        wardrobeSel.bottom = "";
      }
      wardrobeDirty = true;
      renderWardrobe(wardrobeData);
    }

    function renderWardrobe(data) {
      data = data && typeof data === "object" ? data : {};
      wardrobeData = data;
      const today = data.today && typeof data.today === "object" ? data.today : {};
      const night = data.night && typeof data.night === "object" ? data.night : {};
      const ids = Array.isArray(today.items) ? today.items : [];
      if (!wardrobeDirty) {
        wardrobeSel = {
          coat: ids[0] || "", top: ids[1] || "", bottom: ids[2] || "",
          skirt: ids[3] || "", shoes: ids[4] || "",
        };
      }
      const categories = Array.isArray(data.categories) ? data.categories : [];
      const sleepCategories = Array.isArray(data.sleep_categories) ? data.sleep_categories : [];
      const hotelCategories = Array.isArray(data.hotel_categories) ? data.hotel_categories : [];
      const swimCategories = Array.isArray(data.swim_categories) ? data.swim_categories : [];
      if (!wardrobeDirty) {
        const swimIds = data.swim && Array.isArray(data.swim.items) ? data.swim.items : [];
        wardrobeSwimSel = { top: swimIds[1] || "", bottom: swimIds[2] || "" };
      }
      const exists = {};
      categories.concat(sleepCategories).concat(hotelCategories).concat(swimCategories).forEach((cat) => {
        const set = exists[cat.key] || new Set();
        (cat.items || []).forEach((item) => set.add(item.id));
        exists[cat.key] = set;
      });
      ["coat", "top", "bottom", "skirt", "shoes"].forEach((key) => {
        if (wardrobeSel[key] && exists[key] && !exists[key].has(wardrobeSel[key])) wardrobeSel[key] = "";
        if (wardrobeSwimSel[key] && exists[key] && !exists[key].has(wardrobeSwimSel[key])) {
          wardrobeSwimSel[key] = "";
        }
      });
      const current = data.current || {};
      const atHomeNow = Boolean(current.homewear || current.sleep || current.transition);
      const shown = atHomeNow ? current : wardrobeSel;
      const names = atHomeNow
        ? { coat: current.coat || "", top: current.top || "", bottom: current.bottom || "",
            skirt: current.skirt || "", shoes: current.shoes || "" }
        : wardrobeNames(shown);
      const dayState = String(data.day_state || "");
      const todayLabel = document.getElementById("wardrobeTodayLabel");
      if (todayLabel) todayLabel.textContent = atHomeNow
        ? (current.sleep ? "现在穿的（睡衣）" : "现在穿的（居家便服）")
        : "今日白天穿搭";
      const nightState = String(data.night_state || "");
      const todayBox = document.getElementById("wardrobeToday");
      if (todayBox) {
        todayBox.innerHTML = "";
        if (data.enabled === false) {
          todayBox.appendChild(wardrobeRow("穿搭衣柜没加载", "看日志有没有 wardrobe 初始化失败"));
        } else if (dayState === "swim") {
          todayBox.appendChild(wardrobeRow("正在使用泳装",
            (data.swim && (data.swim.set || data.swim.text)) || "泳装整套"));
        } else if (dayState === "pending8") {
          todayBox.appendChild(wardrobeRow("白天穿搭", "8 点自动生成"));
        } else {
          const skirtName = names.skirt || "";
          const skirtIsDress = /连衣裙|长裙|连身裙|睡裙/.test(skirtName);
          const upper = [names.coat, names.top].filter(Boolean).join("，");
          todayBox.appendChild(wardrobeRow("上衣（外套）",
            skirtIsDress ? (names.coat || "—") : (upper || "—")));
          todayBox.appendChild(wardrobeRow("裙子", skirtName || "—"));
          todayBox.appendChild(wardrobeRow("裤子", names.skirt ? "—" : (names.bottom || "—")));
          todayBox.appendChild(wardrobeRow("鞋子", names.shoes || "—"));
        }
      }
      const meta = document.getElementById("wardrobeTodayMeta");
      if (meta) {
        if (data.enabled === false) meta.textContent = "";
        else if (wardrobeDirty) meta.textContent = "还没保存 · 点「保存今日穿搭」才锁定";
        else if (dayState === "pending8") meta.textContent = "8 点自动生成";
        else if (dayState === "swim") meta.textContent = "正在使用泳装";
        else if (atHomeNow) {
          meta.textContent = current.sleep
            ? "现在在家 · 已换上睡衣"
            : "现在在家 · 居家便服" + (current.transition ? "（刚到家，脱了外套和鞋）" : "");
        }
        else {
          const dayCount = Number(data.outfit_count) || 0;
          const daySeed = Number(data.day_seed_count) || 0;
          meta.textContent = "搭配模板 " + dayCount + " 套"
            + (daySeed && daySeed < dayCount
              ? "（" + daySeed + " 套手工 + " + (dayCount - daySeed) + " 套组合）"
              : (daySeed ? "（手工）" : ""))
            + " · 白天已锁定（早上 8:00 生成，晚上不换）";
          if (data.current && data.current.sleep) meta.textContent += " · 现在在家已换睡衣";
          if (data.current && (data.current.wearing_swim || data.current.swim)) meta.textContent += " · 现在在玩水，已换泳装";
        }
      }
      const nightBox = document.getElementById("wardrobeNight");
      if (nightBox) {
        nightBox.innerHTML = "";
        if (data.enabled === false) {
          nightBox.appendChild(wardrobeRow("睡衣还没加载", ""));
        } else if (nightState === "swim") {
          nightBox.appendChild(wardrobeRow("正在使用泳装",
            (data.swim && (data.swim.set || data.swim.text)) || "泳装整套"));
        } else if (nightState === "outing") {
          nightBox.appendChild(wardrobeRow("晚上穿搭", "临时外出"));
        } else if (night.pending) {
          nightBox.appendChild(wardrobeRow("今晚睡衣", "20:00 后自动生成"));
        } else {
          nightBox.appendChild(wardrobeRow("上衣", night.top || "—"));
          nightBox.appendChild(wardrobeRow("裙子", night.skirt || "—"));
          nightBox.appendChild(wardrobeRow("裤子", night.skirt ? "—" : (night.bottom || "—")));
          nightBox.appendChild(wardrobeRow("鞋子", night.shoes || "—"));
        }
      }
      const nightMeta = document.getElementById("wardrobeNightMeta");
      if (nightMeta) {
        if (data.enabled === false) nightMeta.textContent = "";
        else if (nightState === "swim") nightMeta.textContent = "正在使用泳装";
        else if (nightState === "outing") nightMeta.textContent = "临时外出 · 回家后恢复晚上穿搭";
        else if (night.pending) nightMeta.textContent = "20:00 后自动生成";
        else {
          nightMeta.textContent = night.hotel
            ? "酒店睡衣 · 今晚已锁定"
            : ("睡衣 " + (Number(data.sleep_outfit_count) || 0) + " 套"
               + (data.sleep_seed_count ? "（" + data.sleep_seed_count + " 套手工 + "
                  + Math.max(0, (Number(data.sleep_outfit_count) || 0) - Number(data.sleep_seed_count))
                  + " 套组合）" : "")
               + " · 今晚已锁定");
          if (Number(data.hour) < 20) nightMeta.textContent += "（20:00 自动换上）";
          nightMeta.textContent += " · 想换就跟她说「睡衣不好看」";
        }
      }
      const swimBox = document.getElementById("wardrobeSwimBox");
      const swim = data.swim && typeof data.swim === "object" ? data.swim : {};
      if (swimBox) {
        swimBox.innerHTML = "";
        if (data.enabled === false) {
          swimBox.appendChild(wardrobeRow("泳装还没加载", ""));
        } else if (swim.pending) {
          swimBox.appendChild(wardrobeRow("泳装", swim.text || "游泳 / 海边 / 水上乐园 自动生成"));
        } else {
          if (swim.set) swimBox.appendChild(wardrobeRow("整套", swim.set));
          swimBox.appendChild(wardrobeRow("上衣（泳衣）", swim.top || "—"));
          swimBox.appendChild(wardrobeRow("裤子（泳裤）", swim.bottom || "—"));
          swimBox.appendChild(wardrobeRow("鞋子", "—"));
        }
      }
      const swimMeta = document.getElementById("wardrobeSwimMeta");
      if (swimMeta) {
        swimMeta.textContent = "泳装 " + (Number(data.swim_outfit_count) || 0) + " 套"
          + " · 只有去游泳/海边/水上乐园才穿"
          + (data.at_swim ? " · 现在正在玩水，已经换上了" : "");
      }
      const editor = document.getElementById("wardrobeEditor");
      const closeButton = document.getElementById("wardrobeEditorClose");
      const hint = document.getElementById("wardrobeEditorHint");
      if (editor) editor.hidden = !wardrobeMode;
      if (closeButton) closeButton.hidden = !wardrobeMode;
      if (hint) {
        if (wardrobeMode === "hotel") {
          hint.textContent = "酒店睡衣：只在出门旅游住店时穿，可添加/删除（" + (Number(data.hotel_seed_count) || 0) + " 套内置）";
        } else if (wardrobeMode === "night") {
          hint.textContent = "晚上搭配（睡衣）：点衣服选中，点 × 删除";
        } else if (wardrobeMode === "swim") {
          hint.textContent = "泳衣：只有上衣（泳衣）和裤子（泳裤），没有鞋子；去游泳/海边/水上乐园会自动换上";
        } else if (wardrobeMode === "day") {
          hint.textContent = "白天搭配：点衣服选中，点 × 删除";
        } else {
          hint.textContent = "先选白天、晚上、酒店睡衣还是泳衣，再展开上衣 / 裤子 / 鞋子";
        }
      }
      // 泳装模式：整套选，不显示单件分类
      const swimMode = wardrobeMode === "swim";
      WARDROBE_CATS.forEach((cat) => {
        const list = document.getElementById("wardrobeList-" + cat.key);
        const card = list ? list.closest("section") : null;
        if (card) card.hidden = swimMode;
      });
      const swimSetCard = document.getElementById("swimSetCard");
      if (swimSetCard) swimSetCard.hidden = !swimMode;
      if (swimMode) {
        const swimSetList = document.getElementById("swimSetList");
        if (swimSetList) {
          swimSetList.innerHTML = "";
          const sets = Array.isArray(data.swim_sets) ? data.swim_sets : [];
          if (!sets.length) {
            swimSetList.innerHTML = '<div class="sub">还没有整套泳装</div>';
          }
          sets.forEach((entry) => {
            const chip = document.createElement("span");
            const selected = !!(wardrobeSwimSel.top && wardrobeSwimSel.top === entry.top_id);
            chip.className = "wardrobe-chip" + (selected ? " selected" : "");
            chip.title = selected ? "已选中（点别的整套可换）" : "点一下选这套";
            chip.textContent = (selected ? "✓ " : "") + (entry.set || entry.top) + "（" + entry.top + " + " + entry.bottom + "）";
            chip.addEventListener("click", () => {
              wardrobeSwimSel = { top: entry.top_id || "", bottom: entry.bottom_id || "" };
              wardrobeDirty = true;
              renderWardrobe(wardrobeData);
            });
            swimSetList.appendChild(chip);
          });
        }
      }
      if (!wardrobeMode) return;
      const placeholders = swimMode
        ? { top: "例如：黑色比基尼上衣", bottom: "例如：黑色高腰泳裤" }
        : (wardrobeMode === "hotel"
          ? { top: "例如：白色酒店浴袍", bottom: "例如：酒店睡裤", shoes: "例如：一次性棉拖鞋" }
          : (wardrobeMode === "night"
            ? { top: "例如：真丝睡衣上衣", bottom: "例如：棉质睡裤", shoes: "例如：毛绒拖鞋" }
            : { top: "例如：白色雪纺衬衫", bottom: "例如：黑色包臀裙", shoes: "例如：淡蓝色运动鞋" }));
      WARDROBE_CATS.forEach((cat) => {
        const input = document.getElementById("wardrobeInput-" + cat.key);
        if (input) input.placeholder = placeholders[cat.key] || "";
      });
      const source = swimMode
        ? swimCategories
        : (wardrobeMode === "hotel"
          ? hotelCategories
          : (wardrobeMode === "night" ? sleepCategories : categories));
      WARDROBE_CATS.forEach((cat) => {
        const box = document.getElementById("wardrobeList-" + cat.key);
        if (!box) return;
        box.innerHTML = "";
        const entry = source.find((item) => item && item.key === cat.key);
        const items = entry && Array.isArray(entry.items) ? entry.items : [];
        if (!items.length) {
          const tip = document.createElement("div");
          tip.className = "sub";
          tip.textContent = "还没有" + cat.label + "，点右上角「＋ 添加」加一件";
          box.appendChild(tip);
          return;
        }
        items.forEach((item) => box.appendChild(wardrobeChip(item, cat.key)));
      });
    }

    function openWardrobeMode(mode) {
      wardrobeMode = mode;
      renderWardrobe(wardrobeData);
    }

    function closeWardrobeEditor() {
      wardrobeMode = "";
      renderWardrobe(wardrobeData);
    }

    async function loadWardrobe() {
      try {
        const data = objectFrom(await call("wardrobe"));
        renderWardrobe(data);
      } catch (error) {
        toast("穿搭读取失败：" + (error && error.message ? error.message : "接口未就绪"), true);
      }
    }

    // ---------- 自拍姿势库 ----------
    let poseData = { poses: [] };
    let poseDraft = null;
    let posePolling = false;

    function poseRowElement(pose) {
      const row = document.createElement("div");
      row.className = "row between";
      row.style.cssText = "padding:8px 0;border-bottom:1px solid rgba(0,0,0,.06);align-items:center;gap:8px";
      const left = document.createElement("div");
      const name = document.createElement("div");
      name.className = "label";
      name.textContent = pose.name + (pose.has_ref ? " · 有参考图" : "");
      left.appendChild(name);
      if (pose.note) {
        const note = document.createElement("div");
        note.className = "sub";
        note.textContent = pose.note;
        left.appendChild(note);
      }
      row.appendChild(left);
      if (poseDraft) {
        const del = document.createElement("button");
        del.className = "ghost";
        del.type = "button";
        del.textContent = "×";
        del.title = "删掉这个姿势";
        del.addEventListener("click", () => deletePoseDraft(pose.id));
        row.appendChild(del);
      }
      return row;
    }

    function renderPoses() {
      const box = document.getElementById("poseList");
      const meta = document.getElementById("poseMeta");
      if (!box) return;
      const poses = poseDraft || poseData.poses || [];
      box.innerHTML = "";
      if (!poses.length) {
        box.innerHTML = '<div class="sub">还没有姿势，在下面加一个。</div>';
      } else {
        poses.forEach((pose) => box.appendChild(poseRowElement(pose)));
      }
      if (meta) {
        const withRef = poses.filter((pose) => pose.has_ref).length;
        meta.textContent = "共 " + poses.length + " 个姿势 · " + withRef + " 个有参考图"
          + (poseDraft ? " · 改完点「保存」" : "");
      }
    }

    async function loadPoses() {
      try {
        const data = objectFrom(await call("poses"));
        poseData = data && data.poses ? data : { poses: [] };
        if (!poseDraft) renderPoses();
      } catch (error) {
        toast("姿势库读取失败：" + (error && error.message ? error.message : "接口未就绪"), true);
      }
    }

    function openPoseEditor() {
      poseDraft = (poseData.poses || []).map((pose) => ({ ...pose }));
      const editor = document.getElementById("poseEditor");
      if (editor) editor.hidden = false;
      const open = document.getElementById("poseOpen");
      const save = document.getElementById("poseSave");
      const close = document.getElementById("poseClose");
      if (open) open.hidden = true;
      if (save) save.hidden = false;
      if (close) close.hidden = false;
      renderPoses();
    }

    function closePoseEditor() {
      poseDraft = null;
      const editor = document.getElementById("poseEditor");
      if (editor) editor.hidden = true;
      const open = document.getElementById("poseOpen");
      const save = document.getElementById("poseSave");
      const close = document.getElementById("poseClose");
      if (open) open.hidden = false;
      if (save) save.hidden = true;
      if (close) close.hidden = true;
      renderPoses();
    }

    async function savePoses() {
      if (!poseDraft) return;
      try {
        const res = await call("poses/save", {
          poses: poseDraft.map((pose) => ({ name: pose.name, note: pose.note })),
        });
        toast((res && res.msg) || "已保存");
        poseDraft = null;
        await loadPoses();
        renderPoses();
      } catch (error) {
        toast("保存姿势库失败：" + (error && error.message ? error.message : error), true);
      }
    }

    function addPoseDraft() {
      if (!poseDraft) return;
      const nameInput = document.getElementById("poseName");
      const noteInput = document.getElementById("poseNote");
      const name = String((nameInput && nameInput.value) || "").trim();
      const note = String((noteInput && noteInput.value) || "").trim();
      if (!name) {
        toast("先写个姿势名字，比如：托腮笑", true);
        if (nameInput) nameInput.focus();
        return;
      }
      if (poseDraft.some((pose) => pose.name === name)) {
        toast("已经有「" + name + "」这个姿势了", true);
        return;
      }
      poseDraft.push({ id: "new-" + Date.now(), name: name, note: note, has_ref: false });
      if (nameInput) nameInput.value = "";
      if (noteInput) noteInput.value = "";
      renderPoses();
    }

    function deletePoseDraft(id) {
      if (!poseDraft) return;
      poseDraft = poseDraft.filter((pose) => pose.id !== id);
      renderPoses();
    }

    async function pollPoseRefs() {
      if (posePolling) return;
      posePolling = true;
      const status = document.getElementById("poseRefsStatus");
      try {
        for (let i = 0; i < 120; i += 1) {
          const st = objectFrom(await call("poses/refs/status")) || {};
          if (status) {
            status.textContent = st.running
              ? ("生成中 " + (Number(st.done) || 0) + "/" + (Number(st.total) || 0) + "：" + (st.current || ""))
              : ("参考图：" + (Number(st.done) || 0) + " 张完成");
          }
          if (!st.running) break;
          await new Promise((resolve) => setTimeout(resolve, 3000));
        }
      } catch (error) { /* 轮询失败不打扰 */ }
      posePolling = false;
      await loadPoses();
    }

    async function generatePoseRefs() {
      if (poseDraft) {
        toast("先把改动点「保存」，再生成参考图", true);
        return;
      }
      try {
        const res = await call("poses/refs", {});
        toast((res && res.msg) || "参考图任务已处理");
        if (res && res.data && res.data.running) pollPoseRefs();
      } catch (error) {
        toast("生成姿势参考图失败：" + (error && error.message ? error.message : error), true);
      }
    }

    function openWardrobeForm(category) {
      const form = document.getElementById("wardrobeForm-" + category);
      const input = document.getElementById("wardrobeInput-" + category);
      if (!form || !input) return;
      form.hidden = false;
      input.value = "";
      try { input.focus(); } catch (error) {}
      try { form.scrollIntoView({ block: "nearest", behavior: "smooth" }); } catch (error) {}
    }

    function closeWardrobeForm(category) {
      const form = document.getElementById("wardrobeForm-" + category);
      if (form) form.hidden = true;
    }

    async function submitWardrobeItem(category) {
      const input = document.getElementById("wardrobeInput-" + category);
      const name = input ? String(input.value || "").trim() : "";
      if (!name) {
        toast("先写个名字，比如：白色雪纺衬衫", true);
        if (input) input.focus();
        return;
      }
      const cat = WARDROBE_CATS.find((item) => item.key === category) || { label: "衣服" };
      const hotel = wardrobeMode === "hotel";
      const swim = wardrobeMode === "swim";
      const sleep = (wardrobeMode === "night" || hotel) && !swim;
      try {
        const res = await call("wardrobe/add", { category: category, name: name, sleep: sleep, hotel: hotel, swim: swim });
        toast((res && res.msg) || ("已添加" + cat.label));
        closeWardrobeForm(category);
        await loadWardrobe();
      } catch (error) {
        toast("添加失败：" + (error && error.message ? error.message : error), true);
      }
    }

    function confirmWardrobeDelete(item, chip) {
      if (!item || !item.id) return;
      chip.classList.add("confirm");
      chip.innerHTML = "";
      const ask = document.createElement("span");
      ask.textContent = "删除「" + (item.name || "") + "」？";
      const yes = document.createElement("button");
      yes.type = "button";
      yes.className = "mini";
      yes.textContent = "确定";
      yes.addEventListener("click", (event) => {
        event.stopPropagation();
        deleteWardrobeItem(item);
      });
      const no = document.createElement("button");
      no.type = "button";
      no.className = "mini";
      no.textContent = "取消";
      no.addEventListener("click", (event) => {
        event.stopPropagation();
        loadWardrobe();
      });
      chip.appendChild(ask);
      chip.appendChild(yes);
      chip.appendChild(no);
    }

    async function deleteWardrobeItem(item) {
      if (!item || !item.id) return;
      try {
        const res = await call("wardrobe/delete", { id: item.id });
        toast((res && res.msg) || "已删除");
        await loadWardrobe();
      } catch (error) {
        toast("删除失败：" + (error && error.message ? error.message : error), true);
      }
    }

    async function saveWardrobeToday() {
      if (wardrobeMode === "swim") {
        if (!wardrobeSwimSel.top) {
          toast("先选一件泳衣上衣", true);
          return;
        }
        if (!wardrobeSwimSel.bottom) {
          toast("先选一条泳裤", true);
          return;
        }
        try {
          const res = await call("wardrobe/today", {
            swim: true,
            top: wardrobeSwimSel.top,
            bottom: wardrobeSwimSel.bottom,
          });
          toast((res && res.msg) || "泳装已保存");
          wardrobeDirty = false;
          await loadWardrobe();
        } catch (error) {
          toast("保存泳装失败：" + (error && error.message ? error.message : error), true);
        }
        return;
      }
      const names = wardrobeNames(wardrobeSel);
      const skirtIsDress = /连衣裙|长裙|连身裙|睡裙/.test(names.skirt || "");
      if (!skirtIsDress) {
        if (!wardrobeSel.top && !wardrobeSel.skirt) {
          toast("先选上衣+裤子；长裙只选裙子就行", true);
          return;
        }
        if (wardrobeSel.skirt && !wardrobeSel.top) {
          toast("短裙要配一件上衣", true);
          return;
        }
        if (!wardrobeSel.skirt && !wardrobeSel.bottom) {
          toast("先选一条裤子", true);
          return;
        }
      }
      try {
        const res = await call("wardrobe/today", {
          coat: wardrobeSel.coat || "",
          top: wardrobeSel.top,
          bottom: wardrobeSel.bottom,
          skirt: wardrobeSel.skirt || "",
          shoes: wardrobeSel.shoes || "",
        });
        toast((res && res.msg) || "今日穿搭已保存");
        wardrobeDirty = false;
        await loadWardrobe();
      } catch (error) {
        toast("保存失败：" + (error && error.message ? error.message : error), true);
      }
    }

    async function rerollWardrobe() {
      try {
        const res = await call("wardrobe/reroll", {});
        toast((res && res.msg) || "换好了");
        wardrobeDirty = false;
        await loadWardrobe();
      } catch (error) {
        toast("换穿搭失败：" + (error && error.message ? error.message : error), true);
      }
    }

    // ---------- 照片 ----------
