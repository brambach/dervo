import {pebbleSvg} from './pebble';
/** Message-row avatars for the local timeline: your account photo, a mark per provider and Dervo's own mark.
 * Marks are Dervo's own drawings as inline SVG data URLs, never a provider's trademarked logo. */
export type AvatarSources={photo?:string;provider?:string|null};

/** A mark as the design system draws it (app.css, .byline .who): a pine square with a white glyph, or in dark mode the
 * light pine with dark ink, so the byline reads the same in both. The theme is read when the row is built; the shell
 * rebuilds the rows when the theme changes. */
function mark(glyph:string,dark:boolean):string {
  const back=dark?'#e4ecdc':'#22382b',ink=dark?'#1b1b19':'#f8f8f5';
  return `data:image/svg+xml;utf8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 12"><rect width="12" height="12" fill="${back}"/><g fill="none" stroke="${ink}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${glyph}</g></svg>`)}`;
}
const isDark=()=>typeof document!=='undefined'&&document.documentElement.classList.contains('dark');
/** A four-point asterisk: Dervo's mark for Claude Code. */
const CLAUDE_GLYPH='<path d="M6 1.5v9M1.5 6h9M2.8 2.8l6.4 6.4M9.2 2.8L2.8 9.2"/>';
/** A prompt chevron and cursor: Dervo's mark for Codex. */
const CODEX_GLYPH='<path d="M2.5 3.5L5 6 2.5 8.5M6.5 8.5h3"/>';
/** A dot for a provider Dervo doesn't know by name. */
const OTHER_GLYPH='<circle cx="6" cy="6" r="1.6" fill="currentColor" stroke="none"/>';
/** Dervo's own rows carry the forest pebble at the avatar's 20px, with the pixel-grid face, in the theme's colours. */
const dervoMarkFor=(dark:boolean)=>`data:image/svg+xml;utf8,${encodeURIComponent(pebbleSvg(20,dark))}`;

export function providerMark(provider?:string|null):string {
  const dark=isDark();
  if(provider==='claude')return mark(CLAUDE_GLYPH,dark);
  if(provider==='codex')return mark(CODEX_GLYPH,dark);
  return mark(OTHER_GLYPH.replace('currentColor',dark?'#1b1b19':'#f8f8f5'),dark);
}
export function dervoMark():string {return dervoMarkFor(isDark());}
/** The avatar for a timeline row by its pubkey. Your own rows get the account photo when one is loaded; without one the
 * row falls back to initials as before. Provider and helper rows get the provider's mark; Dervo's rows get the pebble. */
export function messageAvatar(pubkey:string,sources:AvatarSources):string|null {
  if(pubkey==='dervo-coordinator')return dervoMark();
  if(pubkey==='provider')return providerMark(sources.provider);
  if(pubkey.startsWith('helper:'))return providerMark(pubkey.slice(7));
  if(pubkey.startsWith('agent:'))return dervoMark();  // an agent you made in this project (agents.ts)
  return sources.photo??null;
}
