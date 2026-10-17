// Tour: every main surface in light and dark, with axe (serious and critical only) and a keyboard focus check.
// AXE=/path/to/axe.min.js (axe-core isn't a frontend dependency, so it's passed in).
import {readFile,writeFile} from 'node:fs/promises';
import {start} from './harness.mjs';
const axe=process.env.AXE?await readFile(process.env.AXE,'utf8'):null;
const scheme=process.argv[2]??'light';
const out=new URL(`./out/tour-${scheme}`,import.meta.url).pathname;
const h=await start({out,colorScheme:scheme,seed:{result_text:'## Changed\n- greet() says Hi now.\n\n## Checked\n- test_sample.py passes.'}});
const {page}=h;const report={};
async function audit(name){
  await page.waitForTimeout(700);await h.shot(name);
  if(!axe)return;
  await page.evaluate(axe);
  const r=await page.evaluate(async()=>{const res=await window.axe.run(document,{resultTypes:['violations']});return res.violations.filter(v=>['serious','critical'].includes(v.impact)).map(v=>({id:v.id,impact:v.impact,help:v.help,nodes:v.nodes.slice(0,4).map(n=>n.target.join(' ')+' :: '+(n.failureSummary||'').split('\n')[1]?.trim())}));});
  report[name]=r;
}
async function step(name,fn){try{await fn();await audit(name);}catch(e){report[name]={error:e.message.split('\n')[0]};await h.shot(name+'-fail');}}
try{
  await page.goto(h.url);await page.locator('#dervo-home-ask').waitFor();
  await step('home',async()=>{});
  await step('thread',async()=>{await page.getByText('First thread').first().click();
    const ed=page.locator('[data-testid="message-input"]').last();await ed.fill('Make greet say Hi');await ed.press('Enter');
    await page.getByText('greet() says Hi now.').first().waitFor({timeout:15000});});
  await step('palette',async()=>{await page.keyboard.press('Control+k');await page.waitForTimeout(300);await page.keyboard.type('first');});
  await step('palette-empty',async()=>{await page.keyboard.type('zzqx');});
  await page.keyboard.press('Escape');
  for(const nav of ['Recent requests','Knowledge','Next up'])await step(nav.toLowerCase().replace(' ','-'),async()=>{await page.getByRole('button',{name:nav,exact:true}).or(page.getByRole('link',{name:nav,exact:true})).first().click();});
  await step('new-thread',async()=>{await page.getByText('New thread').first().click();});
  await step('account-menu',async()=>{await page.keyboard.press('Escape');await page.getByText('You',{exact:true}).first().click();});
  await step('settings',async()=>{await page.getByRole('menuitem',{name:/Settings/}).first().click();});
  // Keyboard: from the top of Home, does each Tab stop show where focus is?
  await page.keyboard.press('Escape');await page.goto(h.url);await page.locator('#dervo-home-ask').waitFor();await page.waitForTimeout(800);
  const stops=[];
  for(let i=0;i<25;i++){await page.keyboard.press('Tab');
    stops.push(await page.evaluate(()=>{const el=document.activeElement;if(!el||el===document.body)return null;const s=getComputedStyle(el);
      const ring=(s.outlineStyle!=='none'&&parseFloat(s.outlineWidth)>0)||(s.boxShadow&&s.boxShadow!=='none');
      return {label:(el.getAttribute('aria-label')||el.textContent||el.tagName).trim().slice(0,40),ring};}));}
  report.focus=stops;
}finally{await writeFile(`${out}/report.json`,JSON.stringify(report,null,1));
  console.log(JSON.stringify(Object.fromEntries(Object.entries(report).map(([k,v])=>[k,Array.isArray(v)&&k!=='focus'?v.map(x=>`${x.impact} ${x.id}: ${x.help} [${x.nodes.length}] ${x.nodes[0]}`):v])),null,1).slice(0,9000));
  console.log('errors',h.errors,h.consoleErrors.slice(0,5));await h.stop();}
