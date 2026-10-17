import assert from 'node:assert/strict';
import {test,after,afterEach} from 'node:test';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('',{url:'http://localhost'});
for(const key of ['window','document','HTMLElement','MutationObserver','Node','Event'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=await import('react');
const {render,act,waitFor,cleanup}=await import('@testing-library/react');
const {QueryClient,QueryClientProvider,useQuery}=await import('@tanstack/react-query');
const {useAccountForeground}=await import('./useAccountForeground.ts');
after(()=>dom.window.close());afterEach(cleanup);

test('native return refreshes an expired account lease once, preserving denial and cleanup',async()=>{
  let focus,disposed=0,reads=0,allowed=false,finish;
  const watch=callback=>{focus=callback;return ()=>disposed++;};
  const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0,refetchOnWindowFocus:false}}});
  const queryFn=async()=>{reads++;if(finish===null)await new Promise(resolve=>{finish=resolve;});return {allowed,access_valid_until:allowed?Date.now()/1000+30:0};};
  function Workspace(){useAccountForeground(watch);useQuery({queryKey:['dervo-account'],queryFn});return null;}
  const mounted=render(React.createElement(QueryClientProvider,{client},React.createElement(Workspace)));
  await waitFor(()=>assert.equal(client.getQueryData(['dervo-account'])?.allowed,false));
  await act(async()=>{focus(true);focus(true);});assert.equal(reads,1,'mount and duplicate focus do not read again');
  allowed=true;finish=null;
  await act(async()=>{focus(false);focus(true);focus(true);});
  assert.equal(reads,2);assert.equal(client.getQueryData(['dervo-account']).allowed,false,'no optimistic access while read is pending');
  await act(async()=>{focus(false);focus(true);});assert.equal(reads,2,'an in-flight read is not cancelled or duplicated');
  await act(async()=>{finish();});
  await waitFor(()=>assert.equal(client.getQueryData(['dervo-account'])?.allowed,true));
  allowed=false;
  await act(async()=>{focus(false);focus(true);});
  await waitFor(()=>assert.equal(client.getQueryData(['dervo-account'])?.allowed,false));
  mounted.unmount();assert.equal(disposed,1);client.clear();
});
