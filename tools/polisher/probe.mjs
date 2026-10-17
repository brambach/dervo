import {createRequire} from "node:module";const require=createRequire(import.meta.url);
import {start} from './harness.mjs';
const h=await start({out:new URL('./out/probe',import.meta.url).pathname});
try{await h.page.goto(h.url);await h.page.waitForTimeout(3000);
const l=await h.call('orchestrator_list',{projectId:h.ids.project,profileId:h.ids.profile});console.log(JSON.stringify(l.entries??l,null,1).slice(0,2500));
}finally{await h.stop();console.log(require("fs").readFileSync(h.data+"/host/host-stderr.log","utf8").slice(-3000));}
