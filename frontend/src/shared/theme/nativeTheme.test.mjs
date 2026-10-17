// The native window follows the theme you chose (dark audit #2): ThemeProvider tells Tauri to pin macOS's appearance
// for a fixed theme and to hand it back for Match macOS.
import assert from "node:assert/strict";
import { test, after } from "node:test";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost" });
for (const key of ["window", "document", "HTMLElement", "MutationObserver", "Node", "CustomEvent", "Event"]) globalThis[key] = dom.window[key];
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
globalThis.localStorage = dom.window.localStorage;
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });

// A stand-in for Tauri's IPC: every command the page sends is recorded here.
const calls = [];
dom.window.__TAURI_INTERNALS__ = {
  metadata: { currentWindow: { label: "main" }, currentWebview: { label: "main", windowLabel: "main" } },
  invoke: async (cmd, args) => { calls.push({ cmd, args }); return cmd === "plugin:event|listen" ? 1 : undefined; },
  transformCallback: (cb) => cb,
};
dom.window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} };
globalThis.isTauri = true;
dom.window.isTauri = true;

const React = await import("react");
const { render, cleanup, act } = await import("@testing-library/react");
const { ThemeProvider, useTheme, nativeThemeFor } = await import("./ThemeProvider.tsx");

after(() => cleanup());
const themeCalls = () => calls.filter((c) => c.cmd === "plugin:window|set_theme").map((c) => c.args?.value ?? null);
const settle = () => new Promise((r) => setTimeout(r, 40));

test("nativeThemeFor: a fixed theme names its scheme, Match macOS says null", () => {
  assert.equal(nativeThemeFor(false, true), "dark");
  assert.equal(nativeThemeFor(false, false), "light");
  // Negative control: following the system never pins either scheme.
  assert.equal(nativeThemeFor(true, true), null);
  assert.equal(nativeThemeFor(true, false), null);
});

test("ThemeProvider tells the window its theme, and changes it when you choose another", async () => {
  let api;
  function Probe() { api = useTheme(); return null; }
  localStorage.clear();
  calls.length = 0;
  render(React.createElement(ThemeProvider, null, React.createElement(Probe)));
  await settle();
  // A fresh profile follows macOS: the window is handed back to the system.
  assert.deepEqual(themeCalls().slice(-1), [null]);

  await act(async () => { api.applyAppearance({ theme: "buzz-dark", accent: api.accentColor, followSystem: false }); });
  await settle();
  assert.equal(themeCalls().at(-1), "dark", "Dark fixed pins the window dark");

  await act(async () => { api.applyAppearance({ theme: "buzz", accent: api.accentColor, followSystem: false }); });
  await settle();
  assert.equal(themeCalls().at(-1), "light", "Light fixed pins the window light");

  await act(async () => { api.setFollowSystem(true); });
  await settle();
  assert.equal(themeCalls().at(-1), null, "Match macOS hands the appearance back");
});

test("negative control: outside Tauri the window is never told anything", async () => {
  globalThis.isTauri = false;
  dom.window.isTauri = false;
  calls.length = 0;
  cleanup();
  render(React.createElement(ThemeProvider, null));
  await settle();
  assert.deepEqual(themeCalls(), []);
  globalThis.isTauri = true;
  dom.window.isTauri = true;
});
