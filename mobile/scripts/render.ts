/** Renders the phone app in Chromium at iPhone size, paired for real over the local relay with a Mac link, and saves
 * screenshots. ``node --experimental-strip-types scripts/render.ts <exported web dir> <out dir>``. Needs a web export
 * (npm run export:web) and Playwright; a browser render is not the native app, so it shows layout and copy, not iOS
 * motion or keyboard behavior. */
import {createServer} from 'node:http';
import {readFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
import WebSocket from 'ws';
import {chromium} from 'playwright';
import {startRelay} from '../../companion/relay/dev-server.ts';
import {MacLink,type LinkState,type SocketLike} from '../../companion/mac/link.ts';
import {previewHost} from './preview-host.ts';

const [web,out]=process.argv.slice(2).map(p=>path.resolve(p));
await mkdir(out,{recursive:true});
const relay=await startRelay(0);
let saved:LinkState|null=null;
const store={load:async()=>saved,save:async(s:LinkState)=>{saved=structuredClone(s);}};
const connect=(url:string)=>new WebSocket(url) as unknown as SocketLike;
const host=previewHost();
let mac=new MacLink({host,store,relay:relay.url,mac:'Bryce’s MacBook Pro',connect,pollMs:300});
await mac.start();
while(!mac.connected)await new Promise(r=>setTimeout(r,20));
const {link}=await mac.pairingOffer();

const types:Record<string,string>={'.js':'text/javascript','.html':'text/html','.css':'text/css','.ttf':'font/ttf','.png':'image/png','.ico':'image/x-icon','.json':'application/json'};
const server=createServer(async(req,res)=>{
  const url=decodeURIComponent(new URL(req.url??'/','http://x').pathname);
  const file=path.join(web,url);
  try{const body=await readFile(file.startsWith(path.resolve(web))&&path.extname(file)?file:path.join(web,'index.html'));res.setHeader('content-type',types[path.extname(file)]??'text/html');res.end(body);}
  catch{res.setHeader('content-type','text/html');res.end(await readFile(path.join(web,'index.html')));}
});
await new Promise<void>(r=>server.listen(0,'127.0.0.1',()=>r()));
const base=`http://127.0.0.1:${(server.address() as {port:number}).port}`;

const browser=await chromium.launch({executablePath:'/opt/pw-browsers/chromium-1194/chrome-linux/chrome'});
const errors:string[]=[];
async function phone(scheme:'light'|'dark'){
  const context=await browser.newContext({viewport:{width:393,height:852},deviceScaleFactor:3,colorScheme:scheme,isMobile:true,hasTouch:true});
  const page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  return page;
}
const shot=(page:import('playwright').Page,name:string)=>page.screenshot({path:path.join(out,name+'.png')});
async function until<T>(check:()=>T|null|undefined,ms=15000):Promise<T>{
  const end=Date.now()+ms;
  for(;;){const v=check();if(v)return v;if(Date.now()>end)throw new Error('timed out');await new Promise(r=>setTimeout(r,50));}
}
const settle=(ms=700)=>new Promise(r=>setTimeout(r,ms));

const page=await phone('light');
await page.goto(base+'/pair');
await page.getByText('Dervo on your phone').waitFor();
await settle();
await shot(page,'1-pair');
await page.getByLabel('Pairing code').fill(link);
await page.getByRole('button',{name:'Pair'}).click();
await page.getByText(/same six digits/).waitFor({timeout:15000});
const asking=await until(()=>mac.waiting);
await settle();
await shot(page,'1b-pair-allow');
await mac.allowPhone(asking.pk);
await page.getByText('You’re paired').waitFor({timeout:15000});
await settle();
await shot(page,'1c-paired');
await page.getByRole('button',{name:'Go to Home'}).click();
await page.getByText('Waiting on you').waitFor({timeout:15000});
await settle(1200);
await shot(page,'2-home');
await page.getByRole('button',{name:/^Feel premium/}).click();
await page.getByText('Keep one Claude Code alive').waitFor({timeout:10000});
await settle();
await shot(page,'3-thread-working');
await page.getByRole('textbox',{name:'Message',exact:true}).fill('Ship the warm start behind a flag first.');
await page.getByRole('button',{name:'Send'}).click();
await page.getByText('Queued. It goes in when the current run ends.').waitFor({timeout:10000});
await settle();
await shot(page,'4-thread-sent');
await page.getByRole('button',{name:'Back',exact:true}).click();
await page.getByRole('button',{name:/^Ideas storage/}).click();
await page.getByText('Waiting on your answer').waitFor({timeout:10000});
await settle(900);
await shot(page,'5-thread-needs');
await page.getByRole('button',{name:/^Keep them/}).click();
await page.getByText('You chose Keep them').waitFor({timeout:10000});
await settle();
await shot(page,'5c-thread-answered');
await page.getByRole('button',{name:'Back',exact:true}).click();
await page.getByRole('button',{name:/^Live board/}).click();
await page.getByText('Ready for your review').first().waitFor({timeout:10000});
await settle();
await shot(page,'5d-review-approvals-off');
await mac.setApprovals(asking.pk,true);
await page.getByRole('button',{name:'Accept'}).waitFor({timeout:10000});
await settle();
await shot(page,'5e-review');
await page.getByRole('button',{name:'Accept'}).click();
await page.getByText('Accepted and committed.').first().waitFor({timeout:10000});
await settle();
await shot(page,'5f-review-accepted');
await page.getByRole('button',{name:'Back',exact:true}).click();
await page.getByRole('button',{name:'Message Dervo'}).click();
await page.getByRole('link',{name:'Open Feel premium'}).waitFor({timeout:10000});
await page.getByRole('textbox',{name:'Message',exact:true}).fill('Then tidy the pricing copy on the site.');
await page.getByRole('button',{name:'Send'}).click();
await page.getByText('Thinking it through').waitFor({timeout:10000});
await settle();
await shot(page,'5b-dervo-chat');
await page.getByRole('button',{name:'Back',exact:true}).click();
mac.stop();
await page.getByText(/is asleep or offline/).first().waitFor({timeout:10000});
await settle();
await shot(page,'6-home-mac-away');
await page.getByRole('button',{name:'Settings'}).click();
await settle();
await shot(page,'7-settings');
mac=new MacLink({host,store,relay:relay.url,mac:'Bryce’s MacBook Pro',connect,pollMs:300});
await mac.start();
await page.getByRole('button',{name:'Back'}).click();
await page.getByText(/^Live from/).waitFor({timeout:10000});

// Dark: the same pairing, read from the light page's storage.
const storage=await page.evaluate(()=>JSON.stringify(localStorage));
const dark=await phone('dark');
await dark.goto(base+'/pair');
await dark.evaluate(s=>{for(const [k,v] of Object.entries(JSON.parse(s)))localStorage.setItem(k,v as string);},storage);
await dark.goto(base+'/');
await dark.getByText('Waiting on you').waitFor({timeout:15000});
await settle(1200);
await shot(dark,'8-home-dark');
await dark.getByRole('button',{name:/^Feel premium/}).click();
await dark.getByText('Keep one Claude Code alive').waitFor({timeout:10000});
await settle();
await shot(dark,'9-thread-dark');

console.log(JSON.stringify({errors},null,1));
mac.stop();
await browser.close();
server.close();
await relay.close();
