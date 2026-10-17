import assert from "node:assert/strict";
import test from "node:test";

import { isWebKitOnly, laterLineCaret, LineCaretView } from "./webkitLineCaret.ts";

test("only WebKit without Chrome draws its own later-line caret", () => {
  // The Mac app's WKWebView and Playwright's WebKit.
  assert.equal(isWebKitOnly("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)"), true);
  assert.equal(isWebKitOnly("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15"), true);
  assert.equal(isWebKitOnly("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"), false);
  assert.equal(isWebKitOnly("Mozilla/5.0 (darwin) AppleWebKit/537.36 (KHTML, like Gecko) jsdom/26.1.0"), false);
});

function view({ empty = true, composing = false, focused = true, parentOffset = 8, here, first }) {
  return {
    composing,
    hasFocus: () => focused,
    state: { selection: { empty, $head: { pos: 20, parentOffset, parent: { isTextblock: true }, start: () => 12 } } },
    coordsAtPos: (pos) => (pos === 20 ? here : first),
  };
}
const line1 = { top: 104, bottom: 122.3, left: 60 };
const line2 = { top: 128.3, bottom: 146.6, left: 42 };

test("the drawn caret is for a line below the paragraph's first, at the text's own height", () => {
  assert.deepEqual(laterLineCaret(view({ here: line2, first: line1 })), line2);
  assert.equal(laterLineCaret(view({ here: { ...line1, left: 150 }, first: line1 })), null, "first line: native");
  assert.equal(laterLineCaret(view({ here: line2, first: line1, empty: false })), null, "a selection: native");
  assert.equal(laterLineCaret(view({ here: line2, first: line1, composing: true })), null, "composing: native");
  assert.equal(laterLineCaret(view({ here: line2, first: line1, focused: false })), null, "blurred: none drawn");
  assert.equal(laterLineCaret(view({ here: line2, first: line1, parentOffset: 0 })), null, "start of a paragraph");
  assert.equal(laterLineCaret({ ...view({ here: line2, first: line1 }), coordsAtPos: () => { throw new Error("no layout"); } }), null);
});

test("the caret follows the editor into the composer Tiptap moves it to, and measures against that (16.4 item 6)", async () => {
  const { JSDOM } = await import("jsdom");
  const dom = new JSDOM("<!doctype html><body></body>");
  const saved = {};
  for (const key of ["document", "window", "getComputedStyle"]) { saved[key] = globalThis[key]; }
  globalThis.document = dom.window.document;
  globalThis.window = dom.window;
  globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
  try {
    // Tiptap builds the view in its own detached element; EditorContent then moves it into the composer.
    const detached = document.createElement("div");
    const pm = document.createElement("div");
    detached.append(pm);
    const fake = { ...view({ here: line2, first: line1 }), dom: pm };
    const caretView = new LineCaretView(fake);
    const composer = document.createElement("div");
    document.body.append(composer);
    composer.append(...detached.childNodes);
    composer.getBoundingClientRect = () => ({ top: 100, left: 40, bottom: 160, right: 700, width: 660, height: 60 });
    caretView.update();
    const caret = composer.querySelector(".dv-line-caret");
    assert.ok(caret, "the caret is drawn in the composer the editor is in now");
    assert.equal(composer.style.position, "relative", "and that composer is its containing block");
    assert.equal(caret.style.display, "block");
    assert.equal(caret.style.top, `${line2.top - 100}px`, "at the line's height inside the composer, not the page's");
    assert.equal(caret.style.left, `${line2.left - 40}px`);
    assert.equal(detached.querySelector(".dv-line-caret"), null);
    caretView.destroy();
    assert.equal(composer.querySelector(".dv-line-caret"), null);
    assert.equal(pm.style.caretColor, "", "the native caret is back once the drawn one is gone");
  } finally {
    for (const [key, value] of Object.entries(saved)) globalThis[key] = value;
    dom.window.close();
  }
});
