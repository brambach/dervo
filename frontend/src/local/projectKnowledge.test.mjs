import assert from 'node:assert/strict';
import {test} from 'node:test';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {readFileSync} from 'node:fs';
test('a rule shows its row until opened, then its versions, where it came from and the record',async()=>{
 const {KnowledgeHistory}=await import('./ProjectKnowledge.tsx');
 const rules=[{id:'r',revision:2,active_version_id:'v1',versions:[{id:'v1',text:'Approved rule',created_at:'2026-09-13T12:00:00Z',provenance:'Added in Dervo.',source:null},{id:'v2',text:'Proposed edit',created_at:'2026-09-13T12:30:00Z',provenance:'Human edit',source:null}],events:[]}];
 const props={rules,busy:false,onEdit:()=>{},onUseVersion:()=>{},onArchive:()=>{},onSelectionChange:()=>{}};
 const closed=renderToStaticMarkup(React.createElement(KnowledgeHistory,{...props,selection:{ruleId:'',versionId:'',history:false,reading:false}}));
 assert.ok(closed.includes('Approved rule')&&closed.includes('13 Sep'),'a closed row is the rule and its date');
 assert.ok(!closed.includes('Versions'),'nothing else until you open it');
 const open=renderToStaticMarkup(React.createElement(KnowledgeHistory,{...props,selection:{ruleId:'r',versionId:'',history:false,reading:true}}));
 for(const text of ['Approved rule','In use · version 1 of 2 · added in Dervo','Use version 2','Saved 13 Sep, not in use','Archive','Record','v1'])assert.ok(open.includes(text),text);
 const shell=readFileSync(new URL('./ConnectedApp.tsx',import.meta.url),'utf8');
 assert.ok(shell.includes("setKnowledgeOpen(action==='knowledge')"));assert.ok(shell.includes('KnowledgeSnapshots'));
 assert.ok(shell.includes('knowledgeCount={knowledgeWaiting}'),'the sidebar counts what waits in Knowledge');
});
