import assert from 'node:assert/strict';
import {test,afterEach} from 'node:test';
import {isValidElement,createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import en from './en.json' with {type:'json'};
import ptBR from './pt-BR.json' with {type:'json'};
const {t,tr,systemLanguage,resolveLanguage,completeLanguage,useLanguageForTests,language,formatNumber}=await import('./index.ts');

/** The catalog lookup (beta.17, interface language): English is the key, holes fill from vars, plurals follow the
 * language's rules, a key a language lacks shows its English, and Match macOS takes the first language Dervo has. */
afterEach(()=>useLanguageForTests('en'));

test('English shows its own text, with holes filled and counts formatted', ()=>{
  assert.equal(language(),'en');
  assert.equal(t('Copy path'),'Copy path');
  assert.equal(t('Delete {name}',{name:'dervo app'}),'Delete dervo app');
  assert.equal(t('{count} results',{count:1}),'1 result');
  assert.equal(t('{count} results',{count:2}),'2 results');
  assert.equal(t('{count} results',{count:1200}),`${formatNumber(1200)} results`);
  assert.equal(t('Stopping {count} runs…',{count:1}),'Stopping a run…','a form can leave the number out');
});

test('a key that isn’t in the catalog shows its text, without any context after the bar', ()=>{
  assert.equal(t('Not in the catalog yet|button'),'Not in the catalog yet');
});

test('tr puts React nodes in the holes, in the sentence’s order', ()=>{
  const node=tr('Type {name} to confirm',{name:createElement('b',null,'dervo app')});
  assert.ok(isValidElement(node));
  assert.equal(renderToStaticMarkup(node),'Type <b>dervo app</b> to confirm');
});

test('Match macOS takes the first preferred language Dervo has, and any Portuguese gets Brazilian', ()=>{
  const all=()=>true;
  assert.equal(systemLanguage(['pt-PT','en-AU'],all),'pt-BR');
  assert.equal(systemLanguage(['fr-FR','en-AU'],all),'en');
  assert.equal(systemLanguage(['de-DE'],all),'en','nothing Dervo has falls back to English');
  assert.equal(systemLanguage(['en-GB','pt-BR'],all),'en');
  assert.equal(resolveLanguage('en',['pt-BR']),'en','a pick beats the Mac');
});

test('Match macOS only picks a language once its catalog is complete; a part-done one waits for Settings', ()=>{
  const missing=Object.keys(en).filter(key=>!(key in ptBR));
  assert.equal(completeLanguage('en'),true);
  assert.deepEqual(missing,[],'every English text has its Portuguese, so Match macOS can pick Portuguese for Portuguese Macs');
  assert.deepEqual(Object.keys(ptBR).filter(key=>!(key in en)),[],'Portuguese has no texts English lacks');
  assert.equal(completeLanguage('pt-BR'),missing.length===0);
  if(missing.length){
    assert.equal(systemLanguage(['pt-BR','en-US']),'en',`${missing.length} English texts have no Portuguese yet`);
    assert.equal(systemLanguage(['pt-PT']),'en');
    assert.equal(resolveLanguage('system',['pt-BR']),'en');
  }
  assert.equal(resolveLanguage('pt-BR',['en-US']),'pt-BR','picking it in Settings still works');
  assert.equal(systemLanguage(['pt-BR'],()=>false),'en');
});

test('in Portuguese, each translation fills every hole, and a text it lacks shows in English', ()=>{
  useLanguageForTests('pt-BR');
  const vars=text=>Object.fromEntries([...text.matchAll(/\{(\w+)\}/g)].map(([,name])=>[name,name==='count'?3:`<${name}>`]));
  for(const [key,value] of Object.entries(ptBR)){
    const english=typeof en[key]==='string'?en[key]:en[key].other;
    const shown=t(key,vars(english));
    assert.doesNotMatch(shown,/\{\w+\}/,`“${key}” left a hole unfilled: ${shown}`);
    if(typeof value==='string')assert.ok(shown.length>0);
  }
  const lacking=Object.keys(en).find(key=>!(key in ptBR)&&typeof en[key]==='string'&&!/\{/.test(key));
  if(lacking)assert.equal(t(lacking),en[lacking],'shown in English, unmarked outside a development build');
});
