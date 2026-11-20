// 面板脚本加载冒烟：用最小 DOM stub 在 Node vm 里按 index.html 顺序执行外链脚本，
// 目的只有一个——拆分后是否还有 ReferenceError / 语法错（不验证渲染）。
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const dash = path.join(__dirname, "..", "views", "dashboard");

function fakeEl() {
  const target = {
    textContent: "", innerHTML: "", value: "", checked: false, hidden: false, disabled: false,
    style: {}, dataset: {}, children: [], options: [], selectedIndex: 0, width: 0, height: 0,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, removeChild() {}, addEventListener() {}, removeEventListener() {},
    querySelectorAll() { return []; }, querySelector() { return null; },
    setAttribute() {}, getAttribute() { return null; }, focus() {}, click() {}, remove() {},
    getContext() { return new Proxy({}, { get() { return () => {}; } }); },
    getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0 }; },
  };
  return new Proxy(target, {
    get(t, p) { return p in t ? t[p] : undefined; },
    set(t, p, v) { t[p] = v; return true; },
  });
}

const document = {
  documentElement: fakeEl(), body: fakeEl(), head: fakeEl(),
  getElementById() { return fakeEl(); },
  querySelector() { return null; }, querySelectorAll() { return []; },
  createElement() { return fakeEl(); }, createTextNode() { return fakeEl(); },
  addEventListener() {},
};
const sandbox = {
  document, console,
  setTimeout, clearTimeout, setInterval: () => 0, clearInterval: () => {},
  requestAnimationFrame: () => 0, cancelAnimationFrame: () => {},
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  getComputedStyle: () => ({}),
  fetch: () => Promise.resolve({ status: 200, text: () => Promise.resolve("{}"), json: () => Promise.resolve({}) }),
  navigator: {}, location: { href: "", search: "", protocol: "http:" },
  addEventListener() {}, removeEventListener() {},
  innerWidth: 390, innerHeight: 844, devicePixelRatio: 2,
};
sandbox.window = sandbox;
sandbox.parent = sandbox;
sandbox.top = sandbox;
sandbox.self = sandbox;
const ctx = vm.createContext(sandbox);

const scripts = ["assets/api-client.js", "assets/panel.js", "assets/panel-views.js"];
for (const rel of scripts) {
  const code = fs.readFileSync(path.join(dash, rel), "utf8");
  try {
    vm.runInContext(code, ctx, { filename: rel });
    console.log("LOAD OK", rel);
  } catch (error) {
    console.log("LOAD FAIL", rel, String(error && error.stack || error).split("\n").slice(0, 4).join("\n"));
    process.exitCode = 1;
  }
}
if (typeof sandbox.PanelApi !== "object" || typeof sandbox.call !== "function") {
  console.log("PANELAPI FAIL", typeof sandbox.PanelApi, typeof sandbox.call);
  process.exitCode = 1;
} else {
  console.log("PANELAPI OK", Object.keys(sandbox.PanelApi).join(","));
}
process.exit(process.exitCode || 0);
