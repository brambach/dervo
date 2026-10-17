import * as React from "react";

import type { Channel } from "@/shared/api/types";

type ChannelNavigationContextValue = {
  channels: Channel[];
  /** Names of non-DM channels, memoised once so render-hot consumers (message
   *  rows) don't each re-filter the full channel list on every render. */
  nonDmChannelNames: string[];
  /** Dervo's local shell opens a conversation through its own state rather than a router route. When set, markdown
   *  channel references use this and render as local conversation pills. */
  onOpenChannel?: (channelId: string) => void;
  onOpenTask?: (channelId: string, taskId: string) => void;
  /** Also mark plausible #names that match no conversation, so a typo reads as one instead of as plain text. */
  matchUnknown?: boolean;
};

const ChannelNavigationContext =
  React.createContext<ChannelNavigationContextValue>({
    channels: [],
    nonDmChannelNames: [],
  });

export function ChannelNavigationProvider({
  channels,
  children,
  onOpenChannel,
  onOpenTask,
  matchUnknown,
}: {
  channels: Channel[];
  children: React.ReactNode;
  onOpenChannel?: (channelId: string) => void;
  onOpenTask?: (channelId: string, taskId: string) => void;
  matchUnknown?: boolean;
}) {
  const value = React.useMemo(
    () => ({
      channels,
      nonDmChannelNames: channels
        .filter((c) => c.channelType !== "dm")
        .map((c) => c.name),
      onOpenChannel,
      onOpenTask,
      matchUnknown,
    }),
    [channels, onOpenChannel, onOpenTask, matchUnknown],
  );

  return (
    <ChannelNavigationContext.Provider value={value}>
      {children}
    </ChannelNavigationContext.Provider>
  );
}

export function useChannelNavigation() {
  return React.useContext(ChannelNavigationContext);
}
