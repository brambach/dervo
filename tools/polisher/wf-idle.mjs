// Idle load: how many host requests the window makes per minute while nothing happens, on Home and in a thread.
import {start} from './harness.mjs';
const h=await start({out:new URL('./out/idle',import.meta.url).pathname});
const count=async(label,ms)=>{const from=h.log.length;await h.page.waitForTimeout(ms);const ops={};for(const l of h.log.slice(from))ops[l.op]=(ops[l.op]??0)+1;
  const total=h.log.length-from;console.log(`${label}: ${(total*60000/ms).toFixed(0)} requests/min`,Object.entries(ops).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`${k}×${v}`).join(' '));};
try{await h.page.goto(h.url);await h.page.locator('#dervo-home-ask').waitFor();await h.page.waitForTimeout(5000);
  await count('Home idle 30s',30000);
  await h.page.getByText('First thread').first().click();await h.page.waitForTimeout(5000);
  await count('Thread idle 30s',30000);
}finally{await h.stop();}
