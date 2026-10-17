// Small interactions, measured: paste and drop a screenshot, remove it, send it, then what a sent message and a reply
// offer on hover (copy, edit, retry), scroll-to-latest and keyboard. Compared by hand with the Claude desktop app.
import {start,slowest} from './harness.mjs';
const out=new URL('./out/interact',import.meta.url).pathname;
const h=await start({out,seed:{result_text:'## Changed\n- greet() says Hi now.\n\n## Checked\n- test_sample.py passes.'}});
const {page}=h;const t0=Date.now();const mark=s=>console.log(`[${((Date.now()-t0)/1000).toFixed(1)}s] ${s}`);
// A real screenshot-sized PNG, made in the page, handed over the way the clipboard and Finder do.
const makeFile=(name)=>page.evaluateHandle(async(name)=>{const c=document.createElement('canvas');c.width=1200;c.height=800;const g=c.getContext('2d');
  g.fillStyle='#e8f0ff';g.fillRect(0,0,1200,800);g.fillStyle='#234';g.font='64px sans-serif';g.fillText('Screenshot',80,140);
  const blob=await new Promise(r=>c.toBlob(r,'image/png'));const dt=new DataTransfer();dt.items.add(new File([blob],name,{type:'image/png'}));return dt;},name);
const editor=()=>page.locator('[data-testid="message-input"]').last();
const tiles=()=>page.locator('[aria-label="Attached files"] > div');
try{
  await page.goto(h.url);await page.locator('#dervo-home-ask').waitFor();
  await page.getByText('First thread').first().click();await editor().waitFor();await editor().click();
  // 1. Paste a screenshot.
  let c=Date.now();const dt=await makeFile('Screenshot 2026-10-08 at 21.04.png');
  await editor().evaluate((el,dt)=>el.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true})),dt);
  await tiles().first().waitFor({timeout:10000});mark(`paste: tile shows after ${Date.now()-c} ms`);
  await page.waitForTimeout(60);await h.shot('paste-early');
  await page.getByText(/1200 × 800/).first().waitFor({timeout:10000});mark(`paste: ready after ${Date.now()-c} ms`);await h.shot('paste-ready');
  console.log('tray text:',JSON.stringify(await page.locator('[aria-label="Attached files"]').evaluate(el=>el.parentElement.innerText)));
  // 2. Remove it.
  c=Date.now();await page.getByRole('button',{name:/^Remove /}).first().click();await tiles().first().waitFor({state:'detached',timeout:5000});mark(`remove: gone after ${Date.now()-c} ms`);
  // 3. Drag a file over, then drop.
  const dt2=await makeFile('drop.png');
  const zone=await editor().boundingBox();
  await editor().evaluate((el,dt)=>{el.dispatchEvent(new DragEvent('dragenter',{dataTransfer:dt,bubbles:true,cancelable:true}));el.dispatchEvent(new DragEvent('dragover',{dataTransfer:dt,bubbles:true,cancelable:true}));},dt2);
  await page.waitForTimeout(150);await h.shot('drag-over');
  c=Date.now();await editor().evaluate((el,dt)=>el.dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true})),dt2);
  await page.getByText(/1200 × 800/).first().waitFor({timeout:10000});mark(`drop: ready after ${Date.now()-c} ms`);
  // 4. Send with the image.
  await editor().click();await editor().type('What does this screenshot show?');c=Date.now();await editor().press('Enter');
  await page.getByText('What does this screenshot show?').first().waitFor();mark(`send: bubble after ${Date.now()-c} ms`);
  await page.getByText('greet() says Hi now.').first().waitFor({timeout:20000});mark(`send: answer after ${Date.now()-c} ms`);
  await page.waitForTimeout(800);await h.shot('sent-with-image');
  const thumbs=page.locator('[data-testid="sent-images"] .dv-sent-thumb img');await thumbs.first().waitFor({timeout:5000});
  console.log('sent thumbnails:',await thumbs.count(),await page.locator('[data-testid="sent-images"] button').first().getAttribute('aria-label'));
  // 5. Hover yours, then the reply: Copy (and Edit on yours), visible only on hover.
  const actionsOf=text=>page.locator('div:has(> div > [data-testid="message-row"])',{hasText:text}).locator('.dv-msg-actions').first();
  const opacity=loc=>loc.evaluate(el=>getComputedStyle(el).opacity);
  await page.mouse.move(5,5);await page.waitForTimeout(300);
  console.log('actions at rest (yours):',await opacity(actionsOf('What does this screenshot show?')));
  const bubble=page.getByText('What does this screenshot show?').first();await bubble.hover();await page.waitForTimeout(300);await h.shot('hover-user');
  console.log('hovering yours:',await opacity(actionsOf('What does this screenshot show?')),await actionsOf('What does this screenshot show?').locator('button').evaluateAll(b=>b.map(x=>x.getAttribute('aria-label'))));
  await page.getByText('greet() says Hi now.').last().hover();await page.waitForTimeout(300);await h.shot('hover-reply');
  const replyActions=actionsOf('test_sample.py passes.');
  console.log('hovering the reply:',await opacity(replyActions),await replyActions.locator('button').evaluateAll(b=>b.map(x=>x.getAttribute('aria-label'))));
  await page.context().grantPermissions(['clipboard-read','clipboard-write']);
  await replyActions.locator('button').first().click();await page.waitForTimeout(100);
  console.log('after copy:',await replyActions.locator('button').first().getAttribute('aria-label'),JSON.stringify((await page.evaluate(()=>navigator.clipboard.readText())).slice(0,60)));
  await h.shot('copied');
  await bubble.hover();await actionsOf('What does this screenshot show?').getByRole('button',{name:'Edit and send again'}).click();await page.waitForTimeout(200);
  console.log('composer after Edit:',JSON.stringify(await editor().inputValue()),'focused:',await editor().evaluate(el=>el===document.activeElement||el.contains(document.activeElement)));
  await h.shot('edit');await editor().press('ControlOrMeta+a');await editor().press('Backspace');
  // Open the sent picture.
  await page.locator('[data-testid="sent-images"] button').first().click();await page.getByRole('dialog').waitFor();await h.shot('thumb-open');await page.keyboard.press('Escape');
  // 6. Keyboard: Up arrow in an empty composer (Claude Code edits the last message), Escape, Shift+Enter.
  await editor().click();await editor().press('Shift+Enter');await editor().type('second line');
  console.log('shift+enter keeps a newline:',JSON.stringify(await editor().inputValue()));
  await editor().press('ControlOrMeta+a');await editor().press('Backspace');
  // 7. Scroll: fill the thread, scroll up, look for a way back to the latest.
  for(let i=0;i<6;i++){await editor().type(`Message ${i} `+'lorem ipsum '.repeat(30));await editor().press('Enter');await page.waitForTimeout(1500);}
  await page.waitForTimeout(3000);
  const scroller=await page.evaluate(()=>{const all=[...document.querySelectorAll('*')].filter(e=>e.scrollHeight>e.clientHeight+50&&['auto','scroll'].includes(getComputedStyle(e).overflowY));const e=all.sort((a,b)=>b.scrollHeight-a.scrollHeight)[0];if(!e)return null;
    const atBottom=e.scrollTop+e.clientHeight>=e.scrollHeight-4;e.scrollTop=0;return {atBottom,height:e.scrollHeight,cls:e.className.slice(0,80)};});
  console.log('scroller:',scroller);await page.waitForTimeout(500);await h.shot('scrolled-up');
  console.log('jump-to-latest controls:',await page.evaluate(()=>[...document.querySelectorAll('button')].filter(b=>b.offsetParent&&/latest|bottom|new message|jump/i.test((b.getAttribute('aria-label')||'')+b.innerText)).map(b=>b.getAttribute('aria-label')||b.innerText)));
}catch(e){console.log('FAIL',e.message.split('\n').slice(0,3).join(' '));await h.shot('fail');}
finally{console.log('errors',h.errors,h.consoleErrors.slice(0,5));console.log('unknown',[...h.unknown]);console.log('slowest',slowest(h.log,6));await h.stop();}
