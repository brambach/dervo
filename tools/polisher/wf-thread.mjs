// Workflow: open a thread, send a coding request, watch it run and finish; then a hanging run, send mid-run, stop.
import {start,slowest} from './harness.mjs';
const out=new URL('./out/thread',import.meta.url).pathname;
const h=await start({out,seed:{result_text:'Changed greeting.py and the tests pass.'}});
const {page}=h;const t0=Date.now();const mark=(s)=>console.log(`[${((Date.now()-t0)/1000).toFixed(1)}s] ${s}`);
try{
  await page.goto(h.url);await page.getByText('First thread').first().waitFor();
  await page.getByText('First thread').first().click();await page.waitForTimeout(800);await h.shot('thread-empty');
  const editor=page.locator('[data-testid="message-input"]').last();
  await editor.click();await editor.fill('Make greet() say Hi instead of Hello');
  await h.shot('thread-typed');
  const sent=Date.now();await editor.press('Enter');
  // How long until the message shows in the timeline?
  await page.getByText('Make greet() say Hi instead of Hello').first().waitFor({timeout:10000});mark(`message visible after ${Date.now()-sent} ms`);
  await page.waitForTimeout(250);await h.shot('thread-sent');
  for(let i=0;i<6;i++){await page.waitForTimeout(500);await h.shot(`thread-run-${i}`);}
  await page.getByText('Changed greeting.py and the tests pass.').first().waitFor({timeout:30000});mark(`answer visible after ${Date.now()-sent} ms`);
  await page.waitForTimeout(1000);await h.shot('thread-done');
  // Hanging run: send, then send again mid-run, then stop.
  await h.call('polish_scenario',{profile:h.ids.profile,mode:'hang',hang_seconds:60,result_text:'late'});
  await editor.fill('Now add a docstring');const s2=Date.now();await editor.press('Enter');
  await page.getByText('Now add a docstring').first().waitFor();mark(`2nd message visible after ${Date.now()-s2} ms`);
  await page.waitForTimeout(3000);await h.shot('hang-running');
  await editor.fill('Also rename the test');await editor.press('Enter');await page.waitForTimeout(1500);await h.shot('hang-midrun-send');
  console.log('buttons:',await page.getByRole('button').evaluateAll(b=>b.map(x=>x.getAttribute('aria-label')||x.textContent.trim()).filter(Boolean).slice(0,80)));
}catch(e){console.log('FAIL',e.message);await h.shot('fail');}
finally{console.log('errors',h.errors,h.consoleErrors.slice(0,5));console.log('slowest',slowest(h.log,6).map(l=>`${l.op} ${l.total}ms wait ${l.waited}`));
  const span=(h.log.at(-1)?.at-h.log[0]?.at)/1000;console.log(`requests ${h.log.length} over ${span.toFixed(0)}s`);await h.stop();}
