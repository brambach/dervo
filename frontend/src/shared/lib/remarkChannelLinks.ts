/**
 * Remark plugin that detects #channel-name patterns in text nodes and wraps them
 * in custom HAST `channel-link` elements for styled rendering via react-markdown.
 *
 * Known channel names are matched longest-first to avoid partial matches. When
 * no known names are provided, falls back to `#\S+` so that channel links still
 * render while the channel list is loading asynchronously.
 *
 * With `matchUnknown`, plausible names that match nothing are also wrapped, so a
 * mistyped conversation reads as one. Plausible means a letter first, three to 64
 * word characters or hyphens, not after a word character (C#, a URL fragment or an
 * HTML entity), and not a colour like #fff or a line reference like #L42.
 */

import { createRemarkPrefixPlugin } from "./createRemarkPrefixPlugin";
import { buildPrefixPattern, escapeRegExp } from "./mentionPattern";

type RemarkChannelLinksOptions = {
  channelNames?: string[];
  matchUnknown?: boolean;
};

const HEX_COLOUR = /^(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
const LINE_REFERENCE = /^L\d+(?:-L?\d+)?$/i;

export function plausibleUnknownName(name: string): boolean {
  return /^[A-Za-z][\w-]{2,63}$/.test(name) && !HEX_COLOUR.test(name) && !LINE_REFERENCE.test(name);
}

function channelNode(matchText: string) {
  return {
    type: "channel-link",
    value: matchText,
    data: {
      hName: "channel-link",
      hChildren: [{ type: "text", value: matchText }],
      channelName: matchText.slice(1),
    },
  };
}

export default function remarkChannelLinks(
  options?: RemarkChannelLinksOptions,
) {
  const names = options?.channelNames ?? [];
  if (!options?.matchUnknown) {
    const channelPattern = buildPrefixPattern("#", names, {
      fallbackToGeneric: true,
    });
    return createRemarkPrefixPlugin(channelPattern, channelNode);
  }
  const known = [...new Set(names)]
    .filter((name) => name.trim().length > 0)
    .sort((a, b) => b.length - a.length)
    .map(escapeRegExp);
  // Known names first so "#api tests" beats "#api"; then any plausible slug.
  const alternatives = [...known, "[A-Za-z][\\w-]{2,63}"].join("|");
  const pattern = new RegExp(
    `(?<![\\w#&/])#(?:${alternatives})(?=[\\s,;.!?:)\\]}]|$)`,
    "gi",
  );
  const lower = new Set(names.map((name) => name.toLowerCase()));
  return createRemarkPrefixPlugin(pattern, (matchText) => {
    const name = matchText.slice(1);
    if (!lower.has(name.toLowerCase()) && !plausibleUnknownName(name)) {
      return { type: "text", value: matchText };
    }
    return channelNode(matchText);
  });
}
