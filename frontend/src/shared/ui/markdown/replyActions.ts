import {createContext} from 'react';

/** What a reply can do next, without copying and pasting (beta.15, group 1): run a command in the project terminal, open
 * a path in Preview, Finder or its default app, and open a local page in Preview. Only Dervo's own screens provide it, so
 * the same Markdown elsewhere stays plain. Every path and address is checked again on the native side. */
export type PreviewTarget={kind:'file';path:string}|{kind:'page';url:string};
export type ReplyActions={
 /** Types the command into the project terminal, already in the project folder, and shows the terminal. */
 run:(command:string,label:string)=>Promise<void>|void;
 /** Whether agent commands are on in Access: then Run doesn't ask first, even for one that changes files. */
 commandsAllowed:boolean;
 preview:(target:PreviewTarget)=>void;
 /** Whether ``preview`` shows it in this thread's Preview column. Without a task to hang Preview on it opens in the
  * browser or the default app instead, and the buttons say so (design review, 23 Sep). Absent means Preview. */
 previewHere?:boolean;
 reveal:(path:string)=>Promise<unknown>;
 openDefault:(path:string)=>Promise<unknown>;
};
export const ReplyActionsContext=createContext<ReplyActions|null>(null);

const SHELLS=new Set(['bash','sh','shell','zsh','console','terminal','shellscript','shell-session','fish']);
/** Words a command line starts with, for a code block with no language. Anything else isn't offered Run. */
const COMMAND_WORDS=/^(\$\s+)?(cd|ls|pwd|cat|echo|open|npm|npx|pnpm|yarn|bun|node|deno|python3?|pip3?|uv|pytest|cargo|rustup|go|make|brew|git|gh|claude|codex|curl|wget|docker|kubectl|swift|xcodebuild|xcrun|sips|screencapture|tsc|vite|playwright|bash|sh|zsh|chmod|mkdir|touch|cp|mv|rm|sed|grep|rg|find|export|source|\.\/[\w.-]+|bin\/[\w.-]+|scripts\/[\w./-]+)\b/;

/** Characters that make what runs differ from what you read: terminal controls (escape, Ctrl-C, Ctrl-U and the rest),
 * and bidi overrides and zero-width marks that reorder or hide text. A block holding one isn't offered Run. */
export const HIDDEN_CHARACTERS=/[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]/;

/** The command a code block holds, when it's one to run: a shell language, or no language and every line a command.
 * `$ ` prompts are dropped. Null for code, output or anything empty. */
export function runnableCommand(code:string,language:string):string|null{
 const lines=code.replace(/\r\n?/g,'\n').split('\n').map(line=>line.trimEnd()).filter(line=>line.trim()&&!/^\s*#/.test(line));
 if(!lines.length||code.length>4000||lines.length>20||HIDDEN_CHARACTERS.test(code.replace(/\r\n?/g,'\n')))return null;
 const lang=language.toLowerCase();
 if(lang&&!SHELLS.has(lang))return null;
 if(!lang&&!lines.every(line=>COMMAND_WORDS.test(line.trim())))return null;
 // A transcript with output after a prompt isn't one command to run.
 if(lines.some(line=>/^\$\s/.test(line.trim()))&&!lines.every(line=>/^\$\s/.test(line.trim())))return null;
 return lines.map(line=>line.trim().replace(/^\$\s+/,'')).join('\n');
}

/** Commands that only look: listing, reading, searching, status and versions. Everything else asks once before Run. */
const READS=/^(ls|pwd|cat|head|tail|less|wc|tree|which|whoami|date|echo|printf|file|stat|du|df|rg|grep|diff)\b|^git\s+(status|log|diff|show|branch|remote -v)\b|^(node|npm|pnpm|yarn|python3?|pip3?|cargo|rustc|go|claude|codex|gh|brew|git)\s+(-v|-V|--version|version)\s*$|^find\b(?!.*(-delete|-exec|-ok))/;
const WRITES_OUT=/(^|[^<>&2])>{1,2}\s*[^&\s]/;

/** What running a command might do, in plain words, or null when it only looks. Checked per line and per `&&` part. */
export function commandRisk(command:string):string|null{
 const parts=command.split(/\n|&&|\|\||;|\|/).map(part=>part.trim()).filter(Boolean);
 const said=new Set<string>();
 for(const part of parts){
  if(/^sudo\b/.test(part))said.add('It asks for your Mac password and runs as the administrator.');
  else if(/\b(install|add|i|ci|update|upgrade)\b/.test(part)&&/^(npm|pnpm|yarn|bun|pip3?|uv|brew|cargo|gem|go|apt(-get)?|claude|codex|rustup)\b/.test(part))said.add('It installs or updates software.');
  else if(/^(curl|wget)\b/.test(part)&&/\|\s*(ba|z)?sh\b/.test(command))said.add('It downloads a script and runs it.');
  else if(/^(rm|rmdir|unlink)\b/.test(part)||/^git\s+(clean|reset|checkout|restore|stash)\b/.test(part)||/\s-delete\b/.test(part))said.add('It deletes or discards files.');
  else if(/^git\s+(commit|push|merge|rebase|tag|branch\s+-[dD]|cherry-pick|revert|am|switch)\b/.test(part))said.add('It changes your git history or remote.');
  else if(/^(mv|cp|mkdir|touch|chmod|chown|ln|tee|sed\s+-i|perl\s+-pi)\b/.test(part)||WRITES_OUT.test(part))said.add('It changes files in the project.');
  else if(!READS.test(part))said.add('It runs a program that can change files.');
 }
 return said.size?[...said].slice(0,2).join(' '):null;
}

/** A local page in text or a link: `http://localhost:<port>` or `http://127.0.0.1:<port>` with an optional path. The same
 * rule as Preview's typed address and preview.rs `local_page`. */
export function localPageIn(text:string):string|null{
 const match=/\bhttps?:\/\/(localhost|127\.0\.0\.1):(\d{1,5})(\/[^\s)<>"'`]*)?/.exec(text);
 if(!match||match[0].startsWith('https'))return null;
 const port=Number(match[2]);
 if(port<1||port>65535)return null;
 return `http://${match[1]}:${port}${(match[3]??'/').replace(/[.,;:!?]+$/,'')||'/'}`;
}

const EXTENSIONS='html|htm|css|js|mjs|cjs|jsx|ts|tsx|json|md|markdown|txt|pdf|png|jpe?g|gif|webp|avif|svg|py|rs|toml|ya?ml|sh|swift|go|rb|java|kt|c|h|cpp|sql|csv|log|lock|env|plist|xml|mov|mp4';
const LINE='(:\\d+(:\\d+)?)?$';
const BARE=new RegExp(`^[\\w@+-][\\w@.+-]*\\.(${EXTENSIONS})${LINE}`,'i');
// dd #15 (beta.15): with a folder in it, any extension that starts with a letter counts (a game's .gd, .scss, .vue), and
// so do a few files that have none. Only bare names still need the list above, so versions and commit IDs stay text.
const SEGMENT='\\.?[\\w@+-][\\w@.+ -]*';
const NAMED='Makefile|Dockerfile|LICENSE|README|Procfile|Gemfile|Rakefile|Justfile|Brewfile|CODEOWNERS';
const NESTED=new RegExp(`^(?:(?:~\\/|\\/|\\.{1,2}\\/)(?:${SEGMENT}\\/)*|(?:${SEGMENT}\\/)+)(?:${SEGMENT}\\.[a-z][a-z0-9]{0,9}|${NAMED})${LINE}`,'i');
const FOLDER=/^(~\/|\/|\.{1,2}\/)?[\w@.+-]+(\/[\w@.+-]+)+\/$/;
const DOMAIN=/^[\w-]+(\.[\w-]+)*\.(com|org|net|io|dev|app|ai|co|au|uk|gg|sh|me|so)\//i;

/** A file or folder path written in a reply, without a line suffix: `frontend/src/App.tsx:45`, `./serve.sh`,
 * `/Users/me/project/notes.md`, `evidence/design-tests/`, `backend/Makefile`. Null for commands, URLs, branch names,
 * versions and prose. */
export function pathIn(text:string):string|null{
 const value=text.trim();
 if(!value||value.length>1024||/^[a-z][a-z0-9+.-]*:\/\//i.test(value)||/\s{2}|\t|\n/.test(value))return null;
 // A bare name needs one of the extensions above and no space ("see notes.md" is prose).
 if(!value.includes('/'))return BARE.test(value)?value.replace(/:\d+(?::\d+)?$/,''):null;
 if(DOMAIN.test(value)||!(NESTED.test(value)||FOLDER.test(value)))return null;
 return value.replace(/:\d+(?::\d+)?$/,'');
}

/** What Preview can show for a path: a page, an image, Markdown or a PDF. Null for anything else. */
export function previewKind(path:string):'page'|'image'|'markdown'|'pdf'|null{
 return /\.html?$/i.test(path)?'page':/\.(png|jpe?g|gif|webp|avif|svg)$/i.test(path)?'image':/\.(md|markdown)$/i.test(path)?'markdown':/\.pdf$/i.test(path)?'pdf':null;
}
