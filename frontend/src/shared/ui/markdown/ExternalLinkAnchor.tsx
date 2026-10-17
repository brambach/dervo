import * as React from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { toast } from "sonner";

import { cn } from "@/shared/lib/cn";
import { copyTextToClipboard } from "@/shared/lib/clipboard";

import { MaskedLinkTooltip } from "./MaskedLinkTooltip";
import {LocalFileLinks,localFilePath} from './localFileLinks';
import {OpenLocalPage,PathChip} from './ReplyChips';
import {ReplyActionsContext,localPageIn} from './replyActions';
import {showInFileManager} from '@/local/platformWords';
import {
  MediaContextMenu,
  type MediaContextMenuPosition,
  useDismissMediaContextMenu,
} from "./MediaContextMenu";

/**
 * An external `[text](href)` link with a custom right-click menu.
 *
 * Buzz renders inside a native webview whose default context menu has no
 * useful link actions, so a plain right-click on a link is a no-op. This adds
 * an in-app menu with "Open link" (via the OS opener, matching the anchor's
 * left-click `target="_blank"` behavior) and "Copy link" (the real href, not
 * the masked display text).
 */
export function ExternalLinkAnchor({
  anchorProps,
  children,
  href,
  isLinearLink,
  label,
}: {
  anchorProps: React.ComponentPropsWithoutRef<"a">;
  children: React.ReactNode;
  href: string | undefined;
  isLinearLink: boolean;
  label: string;
}) {
  const [menu, setMenu] = React.useState<MediaContextMenuPosition | null>(null);
  const reveal=React.useContext(LocalFileLinks);
  const replyActions=React.useContext(ReplyActionsContext);
  // Beta.15: a local page in a Dervo reply opens in Preview, never by itself, and a file link gets the path menu.
  const page=replyActions&&href?localPageIn(href):null;
  const path=reveal||replyActions?localFilePath(href):null;
  function revealFile(){
    if(path&&reveal)void reveal(path).catch(error=>toast.error(error instanceof Error?error.message:String(error)));
  }
  const closeMenu = React.useCallback(() => setMenu(null), []);
  useDismissMediaContextMenu(Boolean(menu), closeMenu);

  const anchor = (
    <a
      {...anchorProps}
      className={cn(
        "font-medium underline underline-offset-4 transition-colors",
        isLinearLink ? "linear-link" : "text-primary hover:text-primary/80",
      )}
      href={href}
      title={path?showInFileManager():anchorProps.title}
      onClick={event=>{if(path){event.preventDefault();revealFile();}else anchorProps.onClick?.(event);}}
      onContextMenuCapture={(event) => {
        if (!href) return;
        event.preventDefault();
        setMenu({ x: event.clientX, y: event.clientY });
      }}
      rel="noreferrer"
      target="_blank"
    >
      {children}
    </a>
  );

  if(path&&replyActions&&!page)
    return <PathChip path={path}><span className="font-medium text-primary underline underline-offset-4">{children}</span></PathChip>;
  return (
    <>
      <MaskedLinkTooltip disabled={isLinearLink} href={href} label={label}>
        {page?React.cloneElement(anchor,{title:replyActions?.previewHere===false?'Open in your browser':'Open in Preview',onClick:(event:React.MouseEvent)=>{event.preventDefault();replyActions?.preview({kind:'page',url:page});}}):anchor}
      </MaskedLinkTooltip>
      {page&&<OpenLocalPage url={page}/>}
      {menu && href ? (
        <MediaContextMenu
          dataAttributes={["data-link-context-menu"]}
          items={[
            {
              label: path?showInFileManager():"Open link",
              onSelect: () => {
                closeMenu();
                if(path){revealFile();return;}
                void openUrl(href).catch(() => {
                  toast.error("Failed to open link");
                });
              },
            },
            {
              label: "Copy link",
              onSelect: () => {
                closeMenu();
                copyTextToClipboard(href, "Link copied to clipboard");
              },
            },
          ]}
          position={menu}
        />
      ) : null}
    </>
  );
}
