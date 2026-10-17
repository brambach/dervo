import { getCurrentWindow } from "@tauri-apps/api/window";
import * as React from "react";

/**
 * Returns whether the current Tauri window is in fullscreen mode.
 *
 * Initial value resolves from `isFullscreen()`; subsequent updates come from
 * `onResized` (macOS native fullscreen, Windows F11, and Linux fullscreen all
 * fire a resize on transition). Each consumer owns one listener cleaned up on
 * unmount.
 */
export function useIsFullscreen(): boolean {
  const [isFullscreen, setIsFullscreen] = React.useState(false);

  React.useEffect(() => {
    let unlisten: (() => void) | undefined;
    let cancelled = false;

    // Browser IPC tests don't own a native window.
    if (!(window as unknown as {__TAURI_INTERNALS__?: {metadata?: unknown}}).__TAURI_INTERNALS__?.metadata) return;
    const appWindow = getCurrentWindow();

    void appWindow.isFullscreen().then((value) => {
      if (!cancelled) {
        setIsFullscreen(value);
      }
    }).catch(() => {});

    void appWindow
      .onResized(() => {
        void appWindow.isFullscreen().then((value) => {
          if (!cancelled) {
            setIsFullscreen(value);
          }
        }).catch(() => {});
      })
      .then((fn) => {
        if (cancelled) {
          fn();
        } else {
          unlisten = fn;
        }
      }).catch(() => {});

    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  return isFullscreen;
}
