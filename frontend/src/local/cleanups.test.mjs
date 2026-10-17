import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync,readdirSync} from 'node:fs';

const here=new URL('./',import.meta.url);
const sources=readdirSync(here).filter(name=>/\.(tsx?|css)$/.test(name)).map(name=>[name,readFileSync(new URL(name,here),'utf8')]);
const backend=new URL('../../../backend/',import.meta.url);
const hostSources=readdirSync(backend).filter(name=>name.endsWith('.py')&&!name.startsWith('test_')).map(name=>[name,readFileSync(new URL(name,backend),'utf8')]);

test('B12: no screen or host message points at the retired Connections and permissions dialog',()=>{
 for(const [name,text] of [...sources,...hostSources])assert.equal(text.includes('Connections and permissions'),false,name);
});

test('B12: the dead AccountSetup is gone, and coding and agent settings open only inside Project settings',()=>{
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.equal(shell.includes('AccountSetup'),false);
 // Design sweep, 19 Sep: Access, Checks and review and Helper agents are the project's own pages now, one each.
 assert.equal(shell.includes('<CodingPermissionDialog '),false,'the old coding access form is not mounted');
 assert.equal(shell.includes('<CollaborationSettings '),false,'the old agents form is not mounted');
 for(const page of ['AccessPage','ChecksPage','HelpersPage'])assert.equal((shell.match(new RegExp(`<${page} `,'g'))??[]).length,1,`one ${page}`);
 assert.equal(shell.includes('<TaskStart '),false,'the hand-entered task dialog had no way in');
 // 18 Sep: first run allows coding as one of its own steps, so a new project no longer detours through Coding access.
 assert.match(shell,/<FirstRun key=\{firstRunMode\} mode=\{firstRunMode\}/,'first launch and Add project both use the first-run page');
 assert.equal(shell.includes('<ProjectSetup '),false,'the old setup dialog is gone');
});

test('a refusal shown in one conversation clears when you open another',()=>{
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(shell,/useEffect\(\(\)=>\{localStorage\.setItem\('dervo-channel',channelId\);setReply\(null\);setError\(''\);/);
 assert.match(shell,/<ModelPicker key=\{`model:\$\{channelId\}`\} [^]*?onChanged=\{async\(\)=>\{await Promise\.all\(\[activity\.refetch\(\),binding\.refetch\(\)\]\);\}\} overrides=\{chipOverrides\}/,'the composer has one model picker for account, model, thinking, check and review');
 assert.equal((shell.match(/<ModelPicker /g)??[]).length,1,'one picker');
});

test('the Task guide gets a provider suggestion only when the task has one, so no empty actions band is drawn',()=>{
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(shell,/suggestion=\{\(guideTask\.suggest\|\|guideTask\.route\?\.suggest\)&&<ProviderSuggestion task=\{guideTask\}/);
 assert.match(shell,/onFollowUp=\{\(\)=>document\.querySelector<HTMLElement>\('\[data-testid="message-input"\]'\)\?\.focus\(\)\}/);
});

test('the pinned day label never floats over message text in a Dervo conversation',()=>{
 const css=readFileSync(new URL('./TaskGuide.css',import.meta.url),'utf8');
 assert.match(css,/\.dervo-connected-main \[data-testid="message-timeline-sticky-day-divider"\] \{ display:none; \}/);
});

test('the shell tells the status bar and the floating Stop that each task already has its own status and Stop',()=>{
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.match(shell,/<ConversationIndicator taskFooterShown task=\{guideTask\}/);
 assert.match(shell,/<ConversationStop [^>]*tasksStoppedElsewhere\/>/);
});

test('the profile card and wave card work without a huddle provider, which Dervo’s window doesn’t have',async()=>{
 // Bryce, 17 Sep: clicking into a profile crashed the whole window with "useHuddle must be used within a HuddleProvider".
 const shared=name=>readFileSync(new URL(`../features/${name}`,import.meta.url),'utf8');
 for(const file of ['profile/ui/useProfileInteractionActions.ts','messages/ui/WaveMessageAttachment.tsx']){
  const text=shared(file);
  assert.equal(/\buseHuddle\(\)/.test(text),false,file);
  assert.match(text,/useOptionalHuddle\(\)/,file);
 }
 assert.match(shared('profile/ui/useProfileInteractionActions.ts'),/const canHuddle = !!huddle && canInteract/);
 const React=await import('react');
 const {renderToStaticMarkup}=await import('react-dom/server');
 const {useOptionalHuddle}=await import('../features/huddle/HuddleContext.tsx');
 const Probe=()=>React.createElement('span',null,useOptionalHuddle()===null?'no huddle':'huddle');
 assert.equal(renderToStaticMarkup(React.createElement(Probe)),'<span>no huddle</span>');
});
