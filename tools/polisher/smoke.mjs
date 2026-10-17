import {start,slowest} from './harness.mjs';
const h=await start({out:new URL('./out/smoke',import.meta.url).pathname});
try{
  await h.page.goto(h.url);
  await h.page.waitForTimeout(4000);
  await h.shot('boot');
  console.log(await h.page.evaluate(()=>document.body.innerText.slice(0,1500)));
}finally{
  console.log('errors',h.errors,'console',h.consoleErrors.slice(0,10));console.log('unknown',[...h.unknown]);
  console.log('slowest',slowest(h.log,8));await h.stop();console.log(h.stderr().slice(-2000));
}
