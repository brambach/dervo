import assert from 'node:assert/strict';
import {test} from 'node:test';

const check=(name,...argv)=>({name,argv,cwd:'.',timeout:600});
const coding=(run,full={enabled:true})=>({state:'granted',full_access:full,run_checks:{commands:[],suggested:[check('Tests','npm','test'),check('Lint','npm','run','lint')],offer:true,...run}});
const task=(extra={})=>({full_access:true,origin:'conversation',state:'ready_for_review',runs:[{id:'r'}],...extra});

test('a full-access result with no checks offers what the project suggests, until Not now or a choice',async()=>{
 const {checksOffer}=await import('./ChecksOffer.tsx');
 assert.deepEqual(checksOffer(task(),coding()).map(c=>c.name),['Tests','Lint']);
 assert.deepEqual(checksOffer(task(),coding({offer:false})),[],'Not now keeps it away');
 assert.deepEqual(checksOffer(task(),coding({commands:[check('Tests','npm','test')]})),[],'already on');
 assert.deepEqual(checksOffer(task(),coding({suggested:[]})),[],'nothing to offer');
 assert.deepEqual(checksOffer(task({full_access:false}),coding()),[],'only full-access runs run them');
 assert.deepEqual(checksOffer(task({state:'working'}),coding()),[],'not while it works');
 assert.deepEqual(checksOffer(task({runs:[{id:'r',checks:{state:'passed',summary:'Checks pass'}}]}),coding()),[],'checks ran');
 assert.deepEqual(checksOffer(task(),undefined),[]);
 assert.deepEqual(checksOffer(task(),coding({},{enabled:false})),[],'full access is off now');
 assert.deepEqual(checksOffer(task(),coding({},{enabled:true,commands:false})),[],'its runs can’t run commands, so checks would be skipped');
});

test('the offer names the commands as you would type them',async()=>{
 const {offerWords}=await import('./ChecksOffer.tsx');
 assert.equal(offerWords([check('Tests','npm','test')]),'npm test');
 assert.equal(offerWords([check('Tests','npm','test'),check('Lint','npm','run','lint')]),'npm test and npm run lint');
 assert.equal(offerWords([check('A','a'),check('B','b'),check('C','c'),check('D','d')]),'a, b and 2 more');
 assert.equal(offerWords([check('Tests','npm','test'),{...check('Tests (frontend)','npm','test'),cwd:'frontend'}]),'npm test and npm test (frontend)');
});

test('the offer shows one plain line, the reason, and the two choices',async()=>{
 const React=(await import('react')).default;
 const {renderToStaticMarkup}=await import('react-dom/server');
 const {ChecksOffer}=await import('./ChecksOffer.tsx');
 const html=renderToStaticMarkup(React.createElement(ChecksOffer,{task:task(),projectId:'p',coding:coding(),refresh:async()=>{}}));
 for(const text of ['Dervo can run npm test and npm run lint after each change','not the agent','Run them after each change','Not now'])assert.ok(html.includes(text),text);
 assert.equal(renderToStaticMarkup(React.createElement(ChecksOffer,{task:task(),projectId:'p',coding:coding({offer:false}),refresh:async()=>{}})),'');
});
