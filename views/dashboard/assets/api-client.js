/* 面板 API 客户端：从 index.html 内联脚本抽出（桥接 / HTTP 兜底 / 错误文案 / 通知）。 */
    const PLUGIN_NAME = "Astrbot_amoupup_forever";
    let toastTimer = null;

    function officialBridge() {
      if (!window.parent || window.parent === window) return null;
      const candidates = [];
      try { candidates.push(window.AstrBotPluginView, window.AstrBotPluginPage); } catch (error) {}
      try { candidates.push(window.parent.AstrBotPluginView, window.parent.AstrBotPluginPage); } catch (error) {}
      try { candidates.push(window.top.AstrBotPluginView, window.top.AstrBotPluginPage); } catch (error) {}
      return candidates.find((item) => item && typeof item.apiGet === "function") || null;
    }

    function createInlineBridge() {
      if (!window.parent || window.parent === window) return null;
      const pending = new Map();
      let counter = 0;
      let readyResolver = null;
      const readyPromise = new Promise((resolve) => { readyResolver = resolve; });
      window.addEventListener("message", (event) => {
        if (event.source !== window.parent) return;
        const message = event.data;
        if (!message || message.channel !== "astrbot-plugin-page") return;
        if (message.kind === "context") {
          if (readyResolver) { readyResolver(message.context || {}); readyResolver = null; }
          return;
        }
        if (message.kind !== "response") return;
        const item = pending.get(message.requestId);
        if (!item) return;
        pending.delete(message.requestId);
        if (message.ok) item.resolve(message.data);
        else item.reject(new Error(message.error || "插件桥接请求失败"));
      });
      function request(action, payload) {
        return new Promise((resolve, reject) => {
          const requestId = "plugin_req_" + (++counter);
          const endpoint = (payload && payload.endpoint) || "";
          const slowRequest = /schedule(?:\/|_)(?:regen|detail)|worldview(?:\/|_)regen|config_save|profiles_sync/.test(endpoint);
          const timer = setTimeout(() => {
            if (pending.delete(requestId)) reject(new Error("插件桥接超时"));
          }, slowRequest ? 120000 : 20000);
          pending.set(requestId, {
            resolve: (value) => { clearTimeout(timer); resolve(value); },
            reject: (error) => { clearTimeout(timer); reject(error); },
          });
          try {
            window.parent.postMessage({
              channel: "astrbot-plugin-page",
              kind: "request",
              requestId,
              action,
              ...(payload || {}),
            }, "*");
          } catch (error) {
            clearTimeout(timer);
            pending.delete(requestId);
            reject(error);
          }
        });
      }
      try { window.parent.postMessage({ channel: "astrbot-plugin-page", kind: "ready" }, "*"); } catch (error) {}
      return {
        apiGet: (endpoint, params) => request("api:get", { endpoint, params }),
        apiPost: (endpoint, body) => request("api:post", { endpoint, body }),
        ready: () => readyPromise,
      };
    }

    const inlineBridge = createInlineBridge();

    function bridge() {
      return officialBridge() || inlineBridge;
    }

    function actionTimeout(action) {
      const slow = ["schedule_regen", "schedule_detail", "worldview_regen", "config_save", "profiles_sync", "schedule"];
      if (slow.includes(action)) return 120000;
      if (["weather", "weather/refresh", "status"].includes(action)) return 30000;
      return 20000;
    }

    function withTimeout(promise, timeout) {
      return Promise.race([
        promise,
        new Promise((resolve, reject) => {
          setTimeout(() => reject(new Error("桥接请求超时")), timeout || 3000);
        }),
      ]);
    }

    function routeCandidates(action) {
      const list = [action];
      if (action === "status") list.push("get_status");
      if (action === "profiles") list.push("get_profile");
      if (action === "schedule_regen") list.push("schedule/regen");
      if (action === "schedule_detail") list.push("schedule/detail");      if (action === "providers") list.push("providers_list");
      if (action === "kb_regen") list.push("kb/regen");
      if (action === "scenic_kb_regen") list.push("scenic_kb/regen");
      if (action === "worldview_regen") list.push("worldview/regen");
      if (action === "profile_save") list.push("profile/save");
      if (action === "memory_add") list.push("memory/add");
      if (action === "memory_delete") list.push("memory/delete");
      return Array.from(new Set(list));
    }

    async function bridgeCall(action, payload, forceGet) {
      const b = bridge();
      if (!b) return null;
      const errors = [];
      for (const route of routeCandidates(action)) {
        try {
          const attempt = (!forceGet && payload && typeof b.apiPost === "function")
            ? b.apiPost(route, payload)
            : b.apiGet(route, payload || {});
          let result = await withTimeout(attempt, actionTimeout(action));
          if (result && typeof result.json === "function") result = await result.json();
          if (typeof result === "string") {
            try { result = JSON.parse(result); } catch (error) {}
          }
          if (result && typeof result === "object") {
            if (result.status === "error" || result.success === false || result.code === 1) {
              errors.push(action + " 接口返回错误：" + (result.message || result.msg || "未知错误"));
              continue;
            }
            // 403/405 这类说明桥接没走 POST（老版本 AstrBot 可能只有 apiGet）：
            // 当作失败继续往下试，最终会退回 httpCall 用 fetch 发 POST。
            if (typeof result.code === "number" && result.code >= 400) {
              errors.push(result.msg || ("HTTP " + result.code));
              continue;
            }
            return result;
          }
          if (result !== undefined && result !== null) {
            errors.push(action + " 接口返回格式异常：" + (typeof result === "string" ? result.slice(0, 120) : "无法解析"));
          }
        } catch (error) {
          errors.push(action + " 接口请求失败：" + (error.message || String(error)));
          if (String(error.message || "").indexOf("超时") >= 0) break;
        }
      }
      return { __error: errors.find((item) => item.indexOf("404") < 0) || errors[0] || "桥接调用失败" };
    }

    async function httpCall(action, payload, forceGet) {
      // 以前在 iframe 里直接抛错；现在 AstrBot 面板本身和插件页同源，
      // 桥接没有 apiPost（老版本）时仍然可以用 fetch 直接发 POST 兜底。
      const inFrame = window.parent && window.parent !== window;
      const errors = [];
      const headers = {};
      if (payload) headers["Content-Type"] = "application/json";
      try {
        for (const key of ["token", "astrbot_token", "access_token", "dashboard_token"]) {
          const value = window.localStorage.getItem(key);
          if (value) { headers["Authorization"] = "Bearer " + value; break; }
        }
      } catch (error) {}
      const bases = [
        "/api/v1/plugins/extensions/" + PLUGIN_NAME,
        "/api/v1/plugins/extensions/astrbot_plugin_Astrbot_amoupup_forever",
      "/api/v1/plugins/extensions/astrbot_plugin_Astrbot_Amo_forever",
        "/api/plug/" + PLUGIN_NAME,
        "/api/plug/astrbot_plugin_Astrbot_amoupup_forever",
      "/api/plug/astrbot_plugin_Astrbot_Amo_forever",
      ];
      for (const base of bases) {
        for (const route of routeCandidates(action)) {
          let url = base + "/" + route.replace(/^\/+/, "");
          let body = payload ? JSON.stringify(payload) : undefined;
          let method = payload ? "POST" : "GET";
          if (forceGet && payload) {
            const query = Object.keys(payload).map(function (key) {
              return encodeURIComponent(key) + "=" + encodeURIComponent(payload[key]);
            }).join("&");
            url += (url.indexOf("?") >= 0 ? "&" : "?") + query;
            body = undefined;
            method = "GET";
          }
          try {
            const response = await fetch(url, {
              method: method,
              headers: body ? headers : {},
              credentials: "include",
              cache: "no-store",
              body: body,
            });
            const text = await response.text();
            let data = null;
            try { data = text ? JSON.parse(text) : null; } catch (error) { data = null; }
            if (data && (data.code === 0 || data.data !== undefined || data.status === "ok")) return data;
            const detail = safeDetail((data && (data.message || data.msg)) || "");
            errors.push({
              status: response.status,
              text: action + " 接口 " + response.status + (detail ? "：" + detail : ""),
            });
          } catch (error) {
            errors.push({
              status: 0,
              text: action + " 接口请求失败：" + safeDetail(error.message || String(error)),
            });
          }
        }
      }
      // 多个 base 会依次试；优先报真正出错的那个，而不是路由不存在造成的 404
      const meaningful = errors.find((item) => item.status !== 404) || errors[0];
      throw new Error(safeDetail(meaningful ? meaningful.text : "无法连接插件接口") || "她迷糊了一下，这次没接上，再点一次试试。");
    }

    // 写操作在后端只接受 POST：这里强制带上 body，避免面板按钮变成 GET 被拒
    const WRITE_ACTIONS = new Set([
      "schedule_regen", "schedule/block/update", "schedule/block/delete", "schedule/block/restore",
      "kb_regen", "scenic_kb_regen", "worldview_regen",
      "mood_update", "config_save", "profile_save", "profile_delete",
      "relation_save", "relation_delete", "memory_add", "memory_delete",
      "say", "city_set", "profiles_sync", "diary/open", "diary/write", "photo/test",
      "wardrobe/add", "wardrobe/delete", "wardrobe/reroll", "wardrobe/today", "wardrobe/night",
      "weather/refresh", "gift/wish/approve", "gift/wish/reject", "photo/sheet_ref/save",
    ]);

    async function call(action, payload, opts) {
      const forceGet = !!(opts && opts.get);
      const body = (payload === undefined || payload === null)
        ? (WRITE_ACTIONS.has(action) && !forceGet ? {} : payload)
        : payload;
      const bridgeResult = await bridgeCall(action, body, forceGet);
      if (bridgeResult && !bridgeResult.__error) return bridgeResult;
      if (window.parent && window.parent !== window) {
        throw new Error(safeDetail((bridgeResult && bridgeResult.__error) || "页面桥接未就绪，请从插件详情→查看页面重新打开"));
      }
      return await httpCall(action, body, forceGet);
    }


    function safeDetail(text) {
      const raw = String(text || "");
      if (!raw) return "";
      if (/Traceback|KeyError|TypeError|ValueError|AttributeError|Exception|NoneType|object has no attribute|python|Failed to fetch|NetworkError|接口请求失败|接口\s*\d{3}/i.test(raw)) {
        return "她迷糊了，这次没接上，再试一次。";
      }
      return raw;
    }

    function friendlyError(error, fallback) {
      const raw = String((error && error.message) || error || "");
      const clean = safeDetail(raw);
      if (clean !== raw) return clean;
      return raw || fallback || "她迷糊了一下，这次没接上，再点一次试试。";
    }

    function toast(message, isError) {
      const el = document.getElementById("toast");
      el.textContent = message;
      el.className = "toast show" + (isError ? " error" : "");
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => { el.className = "toast"; }, 2600);
    }

    window.addEventListener("unhandledrejection", (event) => {
      event.preventDefault();
      toast(friendlyError(event.reason, "她迷糊了一下，这次没接上，再点一次试试。"), true);
    });

    function normalize(data) {
      if (!data) return {};
      if (typeof data.mood === "string") {
        return {
          time: data.time || "",
          mood: {},
          dominant: String(data.mood).split("（")[0] || "—",
          mood_score: 0,
          period: { phase: data.period || "—", day: 0, tone: "" },
          health: { status: data.health || "—", note: "" },
          weather_text: data.weather || "—",
          weather: {},
          activity: { activity: data.schedule || "—", place: "" },
          threshold: data.threshold || { value: 0, base: 0 },
          activity_rate: 0,
          schedule: [],
          counts: data.counts || {},
        };
      }
      return data;
    }


/* ---- 对外出口：新代码用 window.PanelApi，旧全局名保持不变（避免改调用点） ---- */
window.PanelApi = {
  PLUGIN_NAME: PLUGIN_NAME,
  officialBridge: officialBridge,
  createInlineBridge: createInlineBridge,
  inlineBridge: inlineBridge,
  bridge: bridge,
  actionTimeout: actionTimeout,
  withTimeout: withTimeout,
  routeCandidates: routeCandidates,
  bridgeCall: bridgeCall,
  httpCall: httpCall,
  call: call,
  safeDetail: safeDetail,
  friendlyError: friendlyError,
  toast: toast,
  normalize: normalize,
  WRITE_ACTIONS: WRITE_ACTIONS,
};
window.PLUGIN_NAME = PLUGIN_NAME;
window.WRITE_ACTIONS = WRITE_ACTIONS;
window.call = call;
window.httpCall = httpCall;
window.bridgeCall = bridgeCall;
window.routeCandidates = routeCandidates;
window.withTimeout = withTimeout;
window.actionTimeout = actionTimeout;
window.safeDetail = safeDetail;
window.friendlyError = friendlyError;
window.toast = toast;
window.normalize = normalize;
