import * as React from "react";
import { Toaster as Sonner } from "sonner";

import { useTheme } from "@/shared/theme/ThemeProvider";

type ToasterProps = React.ComponentProps<typeof Sonner>;

function subscribeToDervo(onChange: () => void) {
  if (typeof MutationObserver === "undefined") return () => {};
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-dervo-local"],
  });
  return () => observer.disconnect();
}
const dervoActive = () =>
  document.documentElement.hasAttribute("data-dervo-local");

/** Dervo's local app places toasts over the sidebar's foot, the width of a row, so they never cover the composer or the
 * page (surfaces/kit.html). They stay 6 seconds and pause while hovered. Elsewhere the toaster is unchanged. */
const DERVO_TOASTS: Partial<ToasterProps> = {
  position: "bottom-left",
  duration: 6000,
  offset: { left: 12, bottom: 64 },
  style: { "--width": "var(--dv-toast-width, 356px)" } as React.CSSProperties,
};

const Toaster = ({ ...props }: ToasterProps) => {
  const { isDark } = useTheme();
  const dervo = React.useSyncExternalStore(
    subscribeToDervo,
    dervoActive,
    () => false,
  );

  return (
    <Sonner
      theme={isDark ? "dark" : "light"}
      className="toaster group"
      toastOptions={{
        classNames: {
          toast:
            "group toast group-[.toaster]:bg-background group-[.toaster]:text-foreground group-[.toaster]:border-border group-[.toaster]:shadow-lg",
          description: "group-[.toast]:text-muted-foreground",
          actionButton:
            "group-[.toast]:bg-primary group-[.toast]:text-primary-foreground",
          cancelButton:
            "group-[.toast]:bg-muted group-[.toast]:text-muted-foreground",
        },
      }}
      {...(dervo ? DERVO_TOASTS : {})}
      {...props}
    />
  );
};

export { Toaster };
