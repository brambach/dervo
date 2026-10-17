// Workflow: a long run. Pause it, see what's offered, continue, then Stop, then send again. Checks timings and leftovers.
import {execSync} from 'node:child_process';
import {start,slowest} from './harness.mjs';
const out=new URL('./out/stop',import.meta.url).pathname;
const h=await start({out,seed:{}});
const {page}=h;const t0=Date.now();const mark=(s)=>console.log(`[${((Date.now()-t0)/1000).toFixed(1)}s] ${s}`);
const fakes=()=>execSync("ps -eo pid,args | grep fake_ | grep -v grep || true").toString().trim().split('\n').filter(Boolean).length;
try{
  await h.call('polish_scenario',{profile:h.ids.profile,mode:'hang',hang_seconds:120,result_text:'Working on it',save_session:true});
  await page.goto(h.url);await page.getByText('First thread').first().click();
  const ed=page.locator('[data-testid="message-input"]').last();
  await ed.fill('Refactor greeting.py into a class');await ed.press('Enter');
  await page.getByRole('button',{name:'Pause',exact:true}).waitFor({timeout:15000});mark('running, Pause shown');await page.waitForTimeout(1500);await h.shot('running');
  mark(`fake CLIs alive: ${fakes()}`);
  let c=Date.now();await page.getByRole('button',{name:'Pause',exact:true}).click();
  await page.waitForTimeout(120);await h.shot('pause-clicked');
  for(let i=0;i<40&&await page.getByRole('button',{name:'Pause',exact:true}).count();i++)await page.waitForTimeout(100);
  mark(`Pause button gone after ${Date.now()-c} ms`);await page.waitForTimeout(1500);await h.shot('paused');
  mark(`fake CLIs alive after pause: ${fakes()}`);
  console.log('buttons now:',await page.getByRole('button').evaluateAll(b=>b.map(x=>x.getAttribute('aria-label')||x.textContent.trim()).filter(Boolean).filter(x=>x.length<40)));
  const cont=page.getByRole('button',{name:/^(Continue|Resume)/}).first();
  if(await cont.count()){c=Date.now();await cont.click();await page.getByRole('button',{name:'Pause',exact:true}).waitFor({timeout:15000});mark(`continued, running again after ${Date.now()-c} ms`);await page.waitForTimeout(1000);await h.shot('continued');}
  else mark('no Continue button visible');
  // Pause again, then send a new message while it's paused.
  c=Date.now();await page.getByRole('button',{name:'Pause',exact:true}).click();
  for(let i=0;i<50&&await page.getByRole('button',{name:'Pause',exact:true}).count();i++)await page.waitForTimeout(100);mark(`paused again after ${Date.now()-c} ms`);
  await page.waitForTimeout(1500);await h.shot('paused-again');mark(`fake CLIs alive after 2nd pause: ${fakes()}`);
  await h.call('polish_scenario',{profile:h.ids.profile,result_text:'Done this time.',save_session:true});
  await ed.fill('Try again, smaller change');await ed.press('Enter');
  await page.getByText('Done this time.').first().waitFor({timeout:20000});mark('next message answered');await page.waitForTimeout(800);await h.shot('next-answered');
  const ts=await h.call('tasks',h.ids.scope??{projectId:h.ids.project,channelId:h.ids.thread});
  console.log('tasks:',ts.map(t=>[t.state,(t.runs||[]).map(r=>r.state+(r.end_cause?'/'+r.end_cause:''))]));
}catch(e){console.log('FAIL',e.message.split('\n')[0]);await h.shot('fail');}
finally{console.log('errors',h.errors,h.consoleErrors.slice(0,5));console.log('slowest',slowest(h.log,6).map(l=>`${l.op} ${l.total}ms`));
  console.log('host errors',h.log.filter(l=>l.error).map(l=>l.op+': '+l.error).slice(0,10));await h.stop();mark(`fake CLIs alive at end: ${fakes()}`);}
