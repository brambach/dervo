import * as React from "react";

import type { MainTimelineEntry } from "@/features/messages/lib/threadPanel";
import { THREAD_REPLY_ROW_MARGIN_INLINE_REM } from "@/features/messages/lib/threadTreeLayout";
import type { buildVideoReviewContextForMessage } from "@/features/messages/lib/videoReviewContext";
import type { TimelineMessage } from "@/features/messages/types";
import type { UserProfileLookup } from "@/features/profile/lib/identity";
import { cn } from "@/shared/lib/cn";
import { DvMessageRow } from "@/local/DvMessageRow";
import { MessageThreadSummaryRow } from "./MessageThreadSummaryRow";

type ToggleReaction = (
  message: TimelineMessage,
  emoji: string,
  remove: boolean,
) => Promise<void>;

type SystemRowProps = {
  currentPubkey?: string;
  entries?: MainTimelineEntry[];
  entry?: MainTimelineEntry;
  footer: React.ReactNode;
  onToggleReaction?: ToggleReaction;
  profiles?: UserProfileLookup;
  ownerProfiles?: UserProfileLookup;
};

/** A system line (joined, left, created) as one quiet caption per entry. Dervo's host doesn't write these today; the
 * inherited row, with its reactions, emoji picker and profile cards, went on 9 Oct. */
export function SystemRow({ entries, entry, footer }: SystemRowProps) {
  const systemEntries = entries ?? (entry ? [entry] : []);
  if (systemEntries.length === 0) return null;

  return (
    <div className="flex flex-col gap-1 pb-2.5">
      {systemEntries.map((systemEntry) => (
        <p
          className="dv-caption"
          data-message-id={systemEntry.message.id}
          data-testid="system-message-row"
          key={systemEntry.message.id}
        >
          {systemEntry.message.body}
        </p>
      ))}
      {footer}
    </div>
  );
}

type MessageRowItemProps = {
  channelId?: string | null;
  currentPubkey?: string;
  entry: MainTimelineEntry;
  followThreadById?: (rootId: string) => void;
  footer: React.ReactNode;
  highlightedMessageId?: string | null;
  huddleMemberPubkeys?: readonly string[];
  huddleMemberPubkeysPending?: boolean;
  hideAgentAccessBadges?: boolean;
  isContinuation?: boolean;
  isFollowedByContinuation?: boolean;
  isFollowingThreadById?: (rootId: string) => boolean;
  isUnread?: boolean;
  playEntrance?: boolean;
  onEntranceComplete?: (messageId: string) => void;
  onDelete?: (message: TimelineMessage) => void;
  onEdit?: (message: TimelineMessage) => void;
  onMarkUnread?: (message: TimelineMessage) => void;
  onMarkRead?: (message: TimelineMessage) => void;
  onReply?: (message: TimelineMessage) => void;
  onOpenThread?: (message: TimelineMessage) => void;
  onToggleReaction?: ToggleReaction;
  profiles?: UserProfileLookup;
  searchActiveMessageId?: string | null;
  searchMatchingMessageIds?: Set<string>;
  searchQuery?: string;
  threadUnreadCounts?: ReadonlyMap<string, number>;
  unfollowThreadById?: (rootId: string) => void;
  videoReviewContext: ReturnType<typeof buildVideoReviewContextForMessage>;
};

export function MessageRowItem({
  entry,
  footer,
  highlightedMessageId,
  isContinuation = false,
  isFollowedByContinuation = false,
  playEntrance = false,
  onEntranceComplete,
  onOpenThread,
  searchActiveMessageId,
  searchMatchingMessageIds,
  searchQuery,
  threadUnreadCounts,
}: MessageRowItemProps) {
  const { message, summary } = entry;

  if (summary && onOpenThread) {
    const isHighlighted = message.id === highlightedMessageId;
    return (
      <div
        className={cn(
          "group/message relative mx-1 mb-1 flex flex-col gap-0 rounded-2xl px-0 py-1 transition-colors hover:bg-muted/50 focus-within:bg-muted/50",
          isHighlighted &&
            "-mx-4 px-4 before:absolute before:-inset-y-1.5 before:inset-x-0 before:animate-[route-target-highlight-fade_2s_ease-out_forwards] before:bg-primary/10 before:content-[''] motion-reduce:before:animate-none sm:-mx-6 sm:px-6",
        )}
      >
        <DvMessageRow
          continuation={isContinuation}
          message={message}
          onEntranceComplete={onEntranceComplete}
          playEntrance={playEntrance}
        />
        <MessageThreadSummaryRow
          depth={message.depth}
          message={message}
          onOpenThread={onOpenThread}
          showDepthGuides={false}
          summary={summary}
          summaryIndentOffsetRem={-THREAD_REPLY_ROW_MARGIN_INLINE_REM}
          unreadCount={threadUnreadCounts?.get(message.id)}
        />
        {footer}
      </div>
    );
  }

  const isSearchMatch = searchMatchingMessageIds?.has(message.id) ?? false;
  const isSearchActive = message.id === searchActiveMessageId;

  return (
    <div
      className={cn(
        "flex flex-col gap-1",
        isFollowedByContinuation ? "pb-0" : "pb-2.5",
      )}
    >
      <DvMessageRow
        continuation={isContinuation}
        highlighted={message.id === highlightedMessageId || isSearchActive}
        message={message}
        onEntranceComplete={onEntranceComplete}
        playEntrance={playEntrance}
        searchQuery={isSearchMatch ? searchQuery : undefined}
      />
      {footer}
    </div>
  );
}
