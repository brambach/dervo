// The boot paint: what index.html and boot.css put on the window before the app bundle loads (dark audit #1).
// The inline script is read out of index.html and run against a stand-in document, so the test follows the real file.
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import vm from 'node:vm';

const frontend=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..','..');
const html=readFileSync(path.join(frontend,'index.html'),'utf8');
const bootCss=readFileSync(path.join(frontend,'public','boot.css'),'utf8');
const script=/<script>([\s\S]*?)<\/script>/.exec(html)?.[1];
const PAPER_DARK='#141312', PAPER_LIGHT='#f7f6f3';

function boot({stored={}, osDark=false, storageThrows=false}={}){
  const classes=new Set(), attributes={};
  const root={classList:{add:c=>classes.add(c)}, style:{backgroundColor:''}, setAttribute:(k,v)=>{attributes[k]=v;}};
  const localStorage={getItem:key=>{ if(storageThrows) throw new Error('no storage'); return Object.hasOwn(stored,key)?stored[key]:null; }};
  const window={localStorage, matchMedia:q=>({matches:q.includes('dark')&&osDark})};
  vm.runInNewContext(script,{window, document:{documentElement:root}});
  return Object.keys(attributes).length?{classes:[...classes], background:root.style.backgroundColor, attributes}:{classes:[...classes], background:root.style.backgroundColor};
}

test('the boot script exists and paints only Dervo paper, never Buzz\'s cached --background',()=>{
  assert.ok(script,'index.html keeps its inline boot script');
  assert.ok(!script.includes('--background'),'the cached Buzz --background is not read any more');
  assert.ok(!script.includes('#fff'),'no white paint on a fresh light profile');
});

test('a fixed Dark theme boots dark whatever the Mac says, and a fixed Light boots light',()=>{
  const cachedDark=JSON.stringify({themeName:'buzz-dark',vars:{},isDark:true});
  const cachedLight=JSON.stringify({themeName:'buzz',vars:{},isDark:false});
  const dark=boot({stored:{'buzz-follow-system':'false','buzz-theme':'buzz-dark','buzz-theme-cache':cachedDark}, osDark:false});
  assert.deepEqual(dark,{classes:['dark'], background:PAPER_DARK});
  // Negative control: the same cache with Light fixed on a dark Mac boots light.
  const light=boot({stored:{'buzz-follow-system':'false','buzz-theme':'buzz','buzz-theme-cache':cachedLight}, osDark:true});
  assert.deepEqual(light,{classes:['light'], background:PAPER_LIGHT});
  // A profile that picked a theme before the toggle existed counts as fixed too (no follow key, a stored theme).
  const older=boot({stored:{'buzz-theme':'buzz-dark','buzz-theme-cache':cachedDark}, osDark:false});
  assert.deepEqual(older,{classes:['dark'], background:PAPER_DARK});
});

test('the Dervo theme boots in forest paper with its palette on the root, and only while Dark',()=>{
  const cachedDark=JSON.stringify({themeName:'buzz-dark',vars:{},isDark:true});
  const forest=boot({stored:{'buzz-follow-system':'false','buzz-theme':'buzz-dark','buzz-theme-cache':cachedDark,'dervo-palette':'forest'}, osDark:false});
  assert.deepEqual(forest,{classes:['dark'], background:'#0e1813', attributes:{'data-dervo-palette':'forest'}});
  // Negative control: a stored forest palette with Light fixed (after ⌘K switched to light) boots plain light.
  const cachedLight=JSON.stringify({themeName:'buzz',vars:{},isDark:false});
  const light=boot({stored:{'buzz-follow-system':'false','buzz-theme':'buzz','buzz-theme-cache':cachedLight,'dervo-palette':'forest'}, osDark:true});
  assert.deepEqual(light,{classes:['light'], background:PAPER_LIGHT});
});

test('Match macOS and a fresh profile follow the OS scheme, not a stale cache',()=>{
  const staleDark=JSON.stringify({themeName:'buzz-dark',vars:{},isDark:true});
  const follows=boot({stored:{'buzz-follow-system':'true','buzz-theme-cache':staleDark}, osDark:false});
  assert.deepEqual(follows,{classes:['light'], background:PAPER_LIGHT});
  const followsDark=boot({stored:{'buzz-follow-system':'true','buzz-theme-cache':staleDark}, osDark:true});
  assert.deepEqual(followsDark,{classes:['dark'], background:PAPER_DARK});
  // Nothing stored at all: the OS decides.
  assert.deepEqual(boot({osDark:true}),{classes:['dark'], background:PAPER_DARK});
  assert.deepEqual(boot({osDark:false}),{classes:['light'], background:PAPER_LIGHT});
});

test('with no storage at all the OS scheme still paints, and never throws',()=>{
  assert.deepEqual(boot({storageThrows:true, osDark:true}),{classes:['dark'], background:PAPER_DARK});
  assert.deepEqual(boot({storageThrows:true, osDark:false}),{classes:['light'], background:PAPER_LIGHT});
});

test('boot.css paints paper for both schemes and no longer black',()=>{
  assert.match(bootCss,/html\s*\{\s*background-color:\s*#f7f6f3;\s*\}/);
  assert.match(bootCss,/@media \(prefers-color-scheme: dark\)\s*\{\s*html\s*\{\s*background-color:\s*#141312;\s*\}\s*\}/);
  assert.ok(!/#000\b/.test(bootCss),'the black default is gone');
});

test('the index.html comment no longer claims tauri.conf.json paints the window',()=>{
  const comment=/<!--([\s\S]*?)-->/.exec(html)?.[1]??'';
  assert.ok(!/painted pre-document via `backgroundColor`/.test(comment));
  assert.ok(/src\/main\.rs/.test(comment),'the comment points at where the native paint lives');
});
