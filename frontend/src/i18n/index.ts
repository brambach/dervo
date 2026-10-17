import {Fragment,createElement,isValidElement,cloneElement,type ReactNode} from 'react';
import en from './en.json';
import ptBR from './pt-BR.json';

/** Dervo's own words, in the language you pick (beta.17, Bruno's report on beta.14; PLAN-beta17-20260928.md section 2).
 *
 * A key is the English text itself, the way a Mac string catalog keys on the development language, so the source still
 * reads as English and English looks exactly as it did. `en.json` lists every key with its English text, and each other
 * language's file gives the same keys in that language. A key can carry a context after a bar ("Open|state") when the
 * same English needs two translations; the text shown is what's before the bar.
 *
 * `{name}` in a text is filled from `vars`. A text whose forms differ by number is an object of Intl.PluralRules
 * categories ({"one":"{count} thread","other":"{count} threads"}), keyed by its "other" form and chosen by `count`.
 * Dates and numbers go through Intl in the same language.
 *
 * The language is fixed when the window loads: `Match macOS` (the default) takes the first of your Mac's preferred
 * languages Dervo has in full, else English. Choosing another one in Settings saves it and reloads the window, so text built
 * when a module loads is in the new language too. */

export type Language='en'|'pt-BR';
export type LanguageChoice='system'|Language;
type Forms=Partial<Record<Intl.LDMLPluralRule,string>>&{other:string};
type Catalog=Record<string,string|Forms>;
export type MessageKey=keyof typeof en;
type Value=string|number;
type Placeholders<S extends string>=S extends `${string}{${infer Name}}${infer Rest}`?Name|Placeholders<Rest>:never;
/** The vars a key needs, by the `{name}` holes in its English. A key with none takes no vars. */
export type VarsFor<K extends MessageKey,V=Value>=[Placeholders<K&string>] extends [never]?[vars?:Record<string,V>]:[vars:Record<Placeholders<K&string>,V>&Record<string,V>];

/** The languages you can pick, each named in itself, so you can find yours whatever the window is showing now. */
export const LANGUAGES:{id:Language;name:string}[]=[{id:'en',name:'English'},{id:'pt-BR',name:'Português (Brasil)'}];
const CATALOGS:Record<Language,Catalog>={'en':en as Catalog,'pt-BR':ptBR as Catalog};
const STORAGE='dervo-language';

const storage=():Storage|null=>{try{return typeof window==='undefined'?null:window.localStorage;}catch{return null;}};
const readChoice=():LanguageChoice=>{
  try{const saved=storage()?.getItem(STORAGE);return saved==='en'||saved==='pt-BR'?saved:'system';}
  catch{return 'system';}
};
const preferred=():readonly string[]=>{
  try{return navigator.languages?.length?navigator.languages:navigator.language?[navigator.language]:[];}
  catch{return [];}
};

/** Whether a language has every one of Dervo's words. Only a complete one is picked for you by Match macOS: a part-done
 * catalog shows English in the middle of Portuguese screens, so until it's complete it's yours to choose in Settings. */
export const completeLanguage=(id:Language):boolean=>id==='en'||Object.keys(CATALOGS.en).every(key=>key in CATALOGS[id]);

/** Your Mac's language, if Dervo has it complete: the first preferred language whose base matches one of Dervo's. Any
 * Portuguese gets Brazilian, the only one there is so far. */
export function systemLanguage(languages:readonly string[]=preferred(),complete:(id:Language)=>boolean=completeLanguage):Language{
  for(const tag of languages){
    const base=tag.toLowerCase().split(/[-_]/)[0];
    if(base==='en')return 'en';
    if(base==='pt'&&complete('pt-BR'))return 'pt-BR';
  }
  return 'en';
}
export const resolveLanguage=(choice:LanguageChoice,languages:readonly string[]=preferred()):Language=>choice==='system'?systemLanguage(languages):choice;

let choice:LanguageChoice=readChoice();
let current:Language=resolveLanguage(choice);
let plurals=new Intl.PluralRules(current);
// Read without Vite's ImportMeta types, so this file type-checks on its own (Dervo's confined TypeScript check).
const dev=(()=>{try{return !!(import.meta as {env?:{DEV?:boolean}}).env?.DEV;}catch{return false;}})();
// VoiceOver reads the window in its language, and line breaks and hyphens follow it.
try{if(typeof document!=='undefined')document.documentElement.lang=current;}catch{/* no document in tests */}

/** What's in effect in this window, and what you picked (which can be "system"). */
export const language=():Language=>current;
export const languageChoice=():LanguageChoice=>choice;

/** Save a pick. It takes effect when the window reloads: `reload` is the caller's to call. */
export function saveLanguageChoice(next:LanguageChoice){
  try{if(next==='system')storage()?.removeItem(STORAGE);else storage()?.setItem(STORAGE,next);}catch{/* kept for this window only */}
  choice=next;
}

/** Tests only: switch the language in place, without a reload. */
export function useLanguageForTests(next:Language){current=next;plurals=new Intl.PluralRules(next);}

/** The locale dates and numbers use: your Mac's own when it's in the same language (so English stays as your region
 * writes it, 24-hour clock and all), else the language itself. */
export function formatLocale():string{
  const base=current.split('-')[0];
  return preferred().find(tag=>tag.toLowerCase().split(/[-_]/)[0]===base)??current;
}

function lookup(key:string,count:Value|undefined):{text:string;missing:boolean}{
  const own=CATALOGS[current][key],english=CATALOGS.en[key];
  const entry=own??english;
  const missing=own===undefined&&current!=='en';
  if(entry===undefined)return {text:key.split('|')[0],missing:true};
  if(typeof entry==='string')return {text:entry,missing};
  const rules=own===undefined?new Intl.PluralRules('en'):plurals;
  const form=typeof count==='number'?rules.select(count):'other';
  return {text:entry[form]??entry.other,missing};
}

const HOLE=/\{(\w+)\}/g;

/** The text for `key` in the current language, with its `{name}` holes filled. A key the language doesn't have yet
 * shows its English; in a development build it's marked ⟦like this⟧ so a missing translation is easy to spot. */
export function t<K extends MessageKey>(key:K,...[vars]:VarsFor<K>):string{
  const count=vars?.count;
  const {text,missing}=lookup(key as string,count);
  const filled=vars?text.replace(HOLE,(hole,name:string)=>name in vars?(name==='count'&&typeof count==='number'?formatNumber(count):String(vars[name])):hole):text;
  return missing&&dev?`⟦${filled}⟧`:filled;
}

/** `t` for text with parts that aren't plain text, like a name in bold or a path in code: each `{name}` can be a React
 * node. `tr('Type {name} to confirm',{name:<b>{name}</b>})`. */
export function tr<K extends MessageKey>(key:K,...[vars]:VarsFor<K,ReactNode>):ReactNode{
  const count=vars?.count;
  const {text,missing}=lookup(key as string,typeof count==='number'?count:undefined);
  const parts:ReactNode[]=[];
  let last=0,index=0;
  for(const match of text.matchAll(HOLE)){
    const name=match[1];
    if(!vars||!(name in vars))continue;
    if(match.index>last)parts.push(text.slice(last,match.index));
    const value=vars[name];
    parts.push(isValidElement(value)?cloneElement(value,{key:`v${index++}`}):typeof value==='number'&&name==='count'?formatNumber(value):value);
    last=match.index+match[0].length;
  }
  if(last<text.length)parts.push(text.slice(last));
  const body=createElement(Fragment,null,...parts);
  return missing&&dev?createElement(Fragment,null,'⟦',body,'⟧'):body;
}

/** The plural category a count takes in the current language, for code that picks between two keys itself. */
export const pluralOf=(count:number):Intl.LDMLPluralRule=>plurals.select(count);

export const formatNumber=(value:number,options?:Intl.NumberFormatOptions)=>new Intl.NumberFormat(formatLocale(),options).format(value);
/** A date or time in the current language: `formatDate(when,{weekday:'short'})`. `english` keeps a locale code pinned
 * before the catalog ('en-US' for "9:12 AM" clocks) while the window is in English. */
export const formatDate=(when:Date|number,options?:Intl.DateTimeFormatOptions,english?:string)=>
  new Intl.DateTimeFormat(current==='en'&&english?english:formatLocale(),options).format(when);
/** A list joined the way the language joins one. English keeps Dervo's own "a, b and c", with no comma before "and"
 * or "or", whatever the Mac's region; other languages use Intl's. */
export function formatList(items:string[],type:Intl.ListFormatType='conjunction'):string{
  if(current!=='en'||type==='unit')return new Intl.ListFormat(formatLocale(),{style:'long',type}).format(items);
  return items.length<2?items.join(''):`${items.slice(0,-1).join(', ')} ${type==='disjunction'?'or':'and'} ${items.at(-1)}`;
}
