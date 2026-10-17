// Workflow: Home. Arrival summary, a message that starts two threads, then a rapid stream of three messages.
import {start,slowest} from './harness.mjs';
const out=new URL('./out/home',import.meta.url).pathname;
const h=await start({out,seed:{result_text:'Done. The signup test passes now.'}});
const {page}=h;const t0=Date.now();const mark=(s)=>console.log(`[${((Date.now()-t0)/1000).toFixed(1)}s] ${s}`);
await h.call('polish_home_rules',{profile:h.ids.profile,rules:[
 {match:'signup test and add a dark mode',delay:1.5,answer:{reply:'Starting two threads: one for the signup test, one for dark mode.',
   actions:[{kind:'start',title:'Fix signup test',workstream:'Auth',focus:'fix the signup test',quote:'fix the signup test'},
            {kind:'start',title:'Dark mode toggle',workstream:'Settings',focus:'add a dark mode toggle',quote:'add a dark mode toggle'}]}},
 {match:'what is running',delay:1,answer:{reply:'Two threads are working: Fix signup test and Dark mode toggle.'}},
 {match:'rename',delay:1,answer:{reply:'Noted.'}},
 {match:'README',delay:2,answer:{reply:'Starting a thread for the README.',actions:[{kind:'start',title:'Update README',workstream:'Docs',focus:'update the README',quote:'update the README'}]}},
]});
try{
  await page.goto(h.url);await page.locator('#dervo-home-ask').waitFor();await page.waitForTimeout(2500);await h.shot('home-arrival');
  const editor=page.locator('#dervo-home-ask');
  await editor.click();await editor.fill('Please fix the signup test and add a dark mode toggle');
  const sent=Date.now();await editor.press('Enter');
  await page.getByText('Please fix the signup test',{exact:false}).first().waitFor();mark(`your message shows after ${Date.now()-sent} ms`);
  await page.waitForTimeout(150);await h.shot('home-thinking');
  await page.getByText('Dark mode toggle').first().waitFor({timeout:20000});mark(`reply shows after ${Date.now()-sent} ms`);
  await page.waitForTimeout(800);await h.shot('home-replied');
  await page.waitForTimeout(4000);await h.shot('home-jobs-running');
  // Rapid stream, like typing three thoughts in a row.
  for(const text of ['what is running right now?','Also update the README please','rename nothing, just checking']){
    await editor.fill(text);await editor.press('Enter');await page.waitForTimeout(250);
  }
  await h.shot('home-rapid-sent');console.log('composer after rapid stream:',JSON.stringify(await editor.inputValue()));
  await page.waitForTimeout(6000);await h.shot('home-rapid-settled');
  console.log('home text:',(await page.locator('main').innerText()).slice(0,2500));
  console.log('threads:',(await h.call('conversations',{projectId:h.ids.project})).map(c=>c.name));
}catch(e){console.log('FAIL',e.message);await h.shot('fail');}
finally{console.log('errors',h.errors,h.consoleErrors.slice(0,5));console.log('slowest',slowest(h.log,6).map(l=>`${l.op} ${l.total}ms wait ${l.waited}`));
  console.log('host errors',h.log.filter(l=>l.error).map(l=>l.op+': '+l.error).slice(0,10));await h.stop();}
