import { Extension } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import type { EditorView } from "@tiptap/pm/view";

/**
 * WebKit draws the caret on the second and later lines of a paragraph from the
 * line above's text bottom down to this line's text bottom, so with the
 * composer's 1.62 line height it's a line box tall (24px against 18px on the
 * first line) and touches the line above. Shift+Enter keeps you in one
 * paragraph, so every line it starts gets that caret (16.2; reproduced in
 * Playwright's WebKit 2336, where a plain contenteditable's soft-wrapped
 * second line does the same, so no editor CSS causes it or can change it).
 *
 * On those lines only, in WebKit only, this hides the native caret and draws
 * one at the text's own height where ProseMirror places the cursor. The first
 * line, selections, composition and every other engine keep the native caret.
 */

const BLINK_MS = 530;

export function isWebKitOnly(userAgent: string): boolean {
  return (
    /AppleWebKit/.test(userAgent) &&
    !/Chrome|Chromium|CriOS|Edg|jsdom/.test(userAgent)
  );
}

type Rect = { top: number; bottom: number; left: number };

/**
 * Where to draw the caret, or null for the native one: an empty selection in a
 * focused editor, not composing, on a line below its textblock's first line.
 */
export function laterLineCaret(view: EditorView): Rect | null {
  const { selection } = view.state;
  if (!selection.empty || view.composing || !view.hasFocus()) return null;
  const $head = selection.$head;
  if (!$head.parent.isTextblock || $head.parentOffset === 0) return null;
  try {
    const here = view.coordsAtPos($head.pos);
    const first = view.coordsAtPos($head.start(), 1);
    // Half a line apart at least: the same line's rects differ by a pixel or so.
    const lineHeight = here.bottom - here.top;
    if (lineHeight <= 0 || here.top - first.top < lineHeight / 2) return null;
    return { top: here.top, bottom: here.bottom, left: here.left };
  } catch {
    return null;
  }
}

/**
 * The caret is drawn inside the editor's parent, which it measures against.
 * That parent is looked up on every update, never kept (16.4 item 6): Tiptap
 * builds the view inside its own detached element and EditorContent then moves
 * it into the composer. A parent kept from construction measured as 0,0, so on
 * a thread the caret was drawn a whole composer's offset away (off-screen at
 * 1512 x 946) while the native caret stayed transparent, and its stray box
 * stretched the composer's scroll area.
 */
export class LineCaretView {
  private readonly caret: HTMLElement;
  private host: HTMLElement | null = null;
  private blink: number | undefined;
  private drawn = false;
  private readonly refresh = () => this.update();
  private readonly resize: ResizeObserver | undefined;
  private readonly view: EditorView;

  constructor(view: EditorView) {
    this.view = view;
    this.caret = document.createElement("div");
    this.caret.className = "dv-line-caret";
    this.caret.setAttribute("aria-hidden", "true");
    Object.assign(this.caret.style, {
      position: "absolute",
      width: "2px",
      marginLeft: "-1px",
      borderRadius: "1px",
      background: "currentColor",
      pointerEvents: "none",
      display: "none",
    });
    this.attach();
    view.dom.addEventListener("focus", this.refresh);
    view.dom.addEventListener("blur", this.refresh);
    view.dom.addEventListener("compositionstart", this.refresh);
    view.dom.addEventListener("compositionend", this.refresh);
    window.addEventListener("resize", this.refresh);
    this.resize =
      typeof ResizeObserver === "undefined"
        ? undefined
        : new ResizeObserver(this.refresh);
    this.resize?.observe(view.dom);
  }

  /** The editor's parent now, made the caret's containing block. */
  private attach(): HTMLElement | null {
    const host = this.view.dom.parentElement;
    if (!host) return null;
    if (host !== this.host || this.caret.parentElement !== host) {
      const position = getComputedStyle(host).position;
      if (!position || position === "static") {
        host.style.position = "relative";
      }
      host.appendChild(this.caret);
      this.host = host;
    }
    return host;
  }

  update() {
    const host = this.attach();
    const rect = host ? laterLineCaret(this.view) : null;
    if (!rect || !host) {
      this.hide();
      return;
    }
    const box = host.getBoundingClientRect();
    Object.assign(this.caret.style, {
      display: "block",
      top: `${rect.top - box.top + host.scrollTop}px`,
      left: `${rect.left - box.left + host.scrollLeft}px`,
      height: `${rect.bottom - rect.top}px`,
      // The native caret's colour: nothing in the app sets caret-color, so it's the text colour.
      color: getComputedStyle(this.view.dom).color,
      visibility: "visible",
    });
    if (!this.drawn) this.view.dom.style.caretColor = "transparent";
    this.drawn = true;
    // A moved caret shows at once and starts blinking again, like the native one.
    window.clearInterval(this.blink);
    this.blink = window.setInterval(() => {
      this.caret.style.visibility =
        this.caret.style.visibility === "hidden" ? "visible" : "hidden";
    }, BLINK_MS);
  }

  private hide() {
    window.clearInterval(this.blink);
    this.caret.style.display = "none";
    if (this.drawn) this.view.dom.style.caretColor = "";
    this.drawn = false;
  }

  destroy() {
    this.hide();
    this.caret.remove();
    this.view.dom.removeEventListener("focus", this.refresh);
    this.view.dom.removeEventListener("blur", this.refresh);
    this.view.dom.removeEventListener("compositionstart", this.refresh);
    this.view.dom.removeEventListener("compositionend", this.refresh);
    window.removeEventListener("resize", this.refresh);
    this.resize?.disconnect();
  }
}

export const WebKitLineCaret = Extension.create({
  name: "webkitLineCaret",
  addProseMirrorPlugins() {
    if (typeof navigator === "undefined" || !isWebKitOnly(navigator.userAgent)) {
      return [];
    }
    return [
      new Plugin({
        key: new PluginKey("webkitLineCaret"),
        view: (view) => new LineCaretView(view),
      }),
    ];
  },
});
