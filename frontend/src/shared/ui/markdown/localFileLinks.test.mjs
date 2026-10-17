import assert from 'node:assert/strict';
import {test} from 'node:test';
import {localFilePath} from './localFileLinks.ts';

test('local file links decode spaces and Unicode and strip source line markers',()=>{
 assert.equal(localFilePath('file:///project/A%20report%20caf%C3%A9.txt'),'/project/A report café.txt');
 assert.equal(localFilePath('/project/source.ts:12:3'),'/project/source.ts');
 assert.equal(localFilePath('src/source.ts#L12'),'src/source.ts');
 assert.equal(localFilePath('./README.md'),'./README.md');
 assert.equal(localFilePath('README.md:12'),'README.md');
});
test('web links, remote file hosts, anchors and malformed paths never become local paths',()=>{
 for(const value of ['https://example.test/a','javascript:alert(1)','file://server/share/a','//server/a','#heading','file:///a%00b','file:///bad%ZZ'])assert.equal(localFilePath(value),null,value);
});

test('clicking a local link calls the project reveal handler and prevents browser navigation',async()=>{
 const {JSDOM}=await import('jsdom');
 const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
 for(const key of ['window','document','HTMLElement','Node','MutationObserver'])globalThis[key]=dom.window[key];
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
 globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const React=await import('react');
 const {render,fireEvent,cleanup}=await import('@testing-library/react');
 const {LocalFileLinks}=await import('./localFileLinks.ts');
 const {ExternalLinkAnchor}=await import('./ExternalLinkAnchor.tsx');
 const calls=[];
 try{
  const view=render(React.createElement(LocalFileLinks.Provider,{value:async path=>calls.push(path)},
   React.createElement(ExternalLinkAnchor,{anchorProps:{},href:'file:///project/A%20report.txt',label:'Report',isLinearLink:true},'Report')));
  assert.equal(fireEvent.click(view.getByRole('link',{name:'Report'})),false);
  assert.deepEqual(calls,['/project/A report.txt']);
 }finally{cleanup();dom.window.close();}
});
