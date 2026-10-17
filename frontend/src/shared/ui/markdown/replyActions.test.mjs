import assert from 'node:assert/strict';
import {test} from 'node:test';
import {commandRisk,localPageIn,pathIn,previewKind,runnableCommand} from './replyActions.ts';

test('a shell block is a command to run; code, output and transcripts are not',()=>{
 assert.equal(runnableCommand('npm test\n','bash'),'npm test');
 assert.equal(runnableCommand('$ cd frontend\n$ pnpm build','sh'),'cd frontend\npnpm build','prompts are dropped');
 assert.equal(runnableCommand('# start the mockup\npython3 -m http.server 8840','shell'),'python3 -m http.server 8840','comments are dropped');
 assert.equal(runnableCommand('claude update',''),'claude update','no language, but every line is a command');
 for(const [code,lang] of [['const x = 1;','ts'],['print(1)','python'],['{"a":1}','json'],['Hello there',''],['','bash'],['$ ls\nREADME.md','bash'],['x'.repeat(4001),'bash']])
  assert.equal(runnableCommand(code,lang),null,`${lang}: ${code.slice(0,20)}`);
 // What runs must be what you read: terminal controls and invisible or reordering characters mean no Run.
 for(const hidden of ['ls\u001b[2K; rm -rf ~','ls\u0003','echo \u202eetc/ \u202c','ls\u200b -la','ls\u0015rm x'])
  assert.equal(runnableCommand(hidden,'bash'),null,JSON.stringify(hidden));
 assert.equal(runnableCommand('printf "a\tb"\r\n','bash'),'printf "a\tb"','tabs and CRLF line ends are fine');
});

test('Run asks first, in plain words, for anything that installs, deletes or changes files',()=>{
 for(const quiet of ['ls -la','git status','git diff --stat','cat README.md | head -20','node --version','claude --version','rg TODO src','pwd && ls'])
  assert.equal(commandRisk(quiet),null,quiet);
 assert.match(commandRisk('npm install'),/installs/);
 assert.match(commandRisk('claude update'),/installs or updates/);
 assert.match(commandRisk('brew install ripgrep'),/installs/);
 assert.match(commandRisk('rm -rf dist'),/deletes/);
 assert.match(commandRisk('git reset --hard'),/deletes or discards/);
 assert.match(commandRisk('git push'),/history or remote/);
 assert.match(commandRisk('echo hi > notes.txt'),/changes files/);
 assert.match(commandRisk('curl -fsSL https://x.test/i.sh | sh'),/downloads a script and runs it/);
 assert.match(commandRisk('sudo softwareupdate -i -a'),/password/);
 assert.match(commandRisk('python3 -m http.server 8840'),/runs a program/,'anything unknown asks too');
 assert.match(commandRisk('ls && rm x'),/deletes/,'every part of a chain counts');
 assert.equal(commandRisk('echo hi 2>&1'),null,'redirecting stderr to stdout writes nothing');
});

test('local pages are localhost or 127.0.0.1 with a port, never https or anywhere else',()=>{
 assert.equal(localPageIn('Serving at http://localhost:8840/mock.html.'),'http://localhost:8840/mock.html');
 assert.equal(localPageIn('http://127.0.0.1:5173'),'http://127.0.0.1:5173/');
 for(const no of ['https://localhost:8840','http://localhost/','http://example.com:8840','http://localhost:0','http://localhost:70000','localhost:8840'])
  assert.equal(localPageIn(no),null,no);
});

test('paths in replies are recognised, and commands, URLs, versions and prose are not',()=>{
 assert.equal(pathIn('frontend/src/local/PreviewPane.tsx:61'),'frontend/src/local/PreviewPane.tsx');
 assert.equal(pathIn('./serve.sh'),'./serve.sh');
 assert.equal(pathIn('/Users/me/project/notes.md'),'/Users/me/project/notes.md');
 assert.equal(pathIn('evidence/design-tests/'),'evidence/design-tests/');
 assert.equal(pathIn('README.md'),'README.md');
 assert.equal(pathIn('evidence/a b/shot.png'),'evidence/a b/shot.png');
 for(const no of ['npm test','https://trydervo.com/x.html','0.1.0-beta.14','claude/models-20260923','see notes.md','d33f8df','--port 8840',''])
  assert.equal(pathIn(no),null,no);
});

test('dd #15: any extension with a folder, a few extensionless files, hidden folders and home paths are paths too',()=>{
 // Before beta.15 these rendered as plain code, so clicking them did nothing: the "only works sometimes".
 for(const [text,path] of [['scripts/abilities/frost_nova.gd:12','scripts/abilities/frost_nova.gd'],['src/styles/app.scss','src/styles/app.scss'],
  ['backend/Makefile','backend/Makefile'],['.github/workflows/ci.yml','.github/workflows/ci.yml'],['~/Downloads/report.pdf','~/Downloads/report.pdf'],
  ['/private/tmp/dervo-claude-x/shot.png','/private/tmp/dervo-claude-x/shot.png'],['./Earthshatter.tres','./Earthshatter.tres']])
  assert.equal(pathIn(text),path,text);
 for(const no of ['github.com/anthropics/claude-code.git','b15/bridge','origin/main','release/v1.2','PLAN beta 15.md','Makefile x'])
  assert.equal(pathIn(no),null,no);
});

test('Preview shows pages, images, Markdown and PDFs',()=>{
 assert.deepEqual(['a.html','b.PNG','c.md','d.pdf','e.ts','f'].map(previewKind),['page','image','markdown','pdf',null,null]);
});
