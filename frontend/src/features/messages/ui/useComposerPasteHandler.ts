import * as React from "react";
import type { Editor } from "@tiptap/react";
import { handleAgentSnapshotPaste } from "@/features/messages/lib/agentSnapshotClipboard";
import type { BlobDescriptor } from "@/shared/api/tauri";
import { hasMentionClipboardHtml } from "@/features/messages/lib/normalizeMentionClipboard";
import { handleMentionClipboardPaste } from "@/features/messages/lib/mentionClipboardPaste";
import type { BindPastedMentionIdentities } from "@/features/messages/lib/mentionPasteBinding";
import { getBuzzCodeBlockClipboardText } from "@/shared/lib/codeBlockClipboard";
import type { ComposerTextPaste, InsertComposerTextPaste } from './composerTextPaste';

export function useComposerPasteHandler(options: {
  editor: Editor | null;
  onTextPaste?: (paste: ComposerTextPaste) => boolean;
  onPasteTextReady?: (insert: InsertComposerTextPaste | null) => void;
  /**
   * Teaches the composer each `name → pubkey` pair a Buzz copy carried, once
   * trusted state vouches for it. Without it, a paste binds nothing.
   */
  bindMentionIdentities?: BindPastedMentionIdentities;
  scrollToBottom: () => void;
  setPendingImeta: (
    update: (current: BlobDescriptor[]) => BlobDescriptor[],
  ) => void;
  uploadFile: (file: File) => Promise<unknown>;
}) {
  const uploadFileRef = React.useRef(options.uploadFile);
  uploadFileRef.current = options.uploadFile;
  const bindMentionIdentitiesRef = React.useRef(options.bindMentionIdentities);
  bindMentionIdentitiesRef.current = options.bindMentionIdentities;
  const textPasteRef = React.useRef(options.onTextPaste);
  textPasteRef.current = options.onTextPaste;
  const insertingText = React.useRef(false);
  React.useEffect(() => {
    const editor = options.editor;
    if (!editor || !options.onPasteTextReady) return;
    options.onPasteTextReady(paste => {
      if (editor.isDestroyed) throw Error('The message editor is no longer available.');
      const same = JSON.stringify(editor.state.doc.toJSON()) === paste.document;
      const end = Math.max(1, editor.state.doc.content.size - 1);
      const range = same ? {from:paste.from, to:paste.to} : {from:end, to:end};
      insertingText.current = true;
      try {
        if (paste.replace) editor.commands.selectAll(); else editor.commands.setTextSelection(range);
        if (!editor.view.pasteText(paste.text)) throw Error('The text couldn’t be inserted.');
        try {editor.view.focus();} catch { /* Insertion succeeded even if focus changed. */ }
      } finally {insertingText.current = false;}
      return same;
    });
    return () => options.onPasteTextReady?.(null);
  }, [options.editor, options.onPasteTextReady]);
  React.useEffect(() => {
    const editor = options.editor;
    if (!editor) return;
    editor.setOptions({
      editorProps: {
        ...editor.options.editorProps,
        handlePaste: (view, event) => {
          if (insertingText.current) return false;
          const mediaItem = Array.from(event.clipboardData?.items ?? []).find(
            (item) => item.kind === "file",
          );
          if (mediaItem) {
            const file = mediaItem.getAsFile();
            if (file) void uploadFileRef.current(file);
            return true;
          }
          const codeBlockText = getBuzzCodeBlockClipboardText(
            event.clipboardData,
          );
          const text = event.clipboardData?.getData('text/plain') ?? '';
          // Rich clipboard formats keep their existing code, mention and snapshot behavior.
          if (text && codeBlockText === null && !event.clipboardData?.getData('text/html') && textPasteRef.current?.({text, from:view.state.selection.from, to:view.state.selection.to, document:JSON.stringify(view.state.doc.toJSON())})) {
            event.preventDefault();
            return true;
          }
          if (codeBlockText !== null) {
            event.preventDefault();
            editor
              .chain()
              .focus()
              .insertContent([
                {
                  type: "codeBlock",
                  content:
                    codeBlockText.length > 0
                      ? [{ type: "text", text: codeBlockText }]
                      : [],
                },
                { type: "paragraph" },
              ])
              .run();
            options.scrollToBottom();
            return true;
          }
          if (handleAgentSnapshotPaste(event, options.setPendingImeta))
            return true;
          const clipboardData = event.clipboardData;
          const html = clipboardData?.getData("text/html");
          if (clipboardData && html && hasMentionClipboardHtml(html)) {
            if (clipboardData.getData("text/plain").includes("\n")) {
              options.scrollToBottom();
            }
            return handleMentionClipboardPaste({
              bindMentionIdentities: bindMentionIdentitiesRef.current,
              clipboardData,
              preventDefault: () => event.preventDefault(),
              view,
            });
          }
          if ((clipboardData?.getData("text/plain") ?? "").includes("\n"))
            options.scrollToBottom();
          return false;
        },
      },
    });
  }, [options.editor, options.scrollToBottom, options.setPendingImeta]);
}
