/* Local sample data through Buzz's supported E2E bridge, before React mounts. */
(() => {
  const bryce = 'deadbeef'.repeat(8), finch = 'f1'.repeat(32), moss = 'a2'.repeat(32);
  const room = '9dae0116-799b-5071-a0a8-fdd30a91a35d';
  const projectId = '30621:' + bryce + ':dervo';
  window.__DERVO_WORKFLOW__ = true;
  const communities = JSON.parse(localStorage.getItem('buzz-communities'));
  communities[0].name = 'Dervo';
  localStorage.setItem('buzz-communities', JSON.stringify(communities));
  const origin = communities[0].relayUrl.replace(/^ws/, 'http');
  localStorage.setItem('buzz.sidebar.projects.membership.v1.' + encodeURIComponent(origin) + '.' + bryce, JSON.stringify({version:1,projects:{[projectId]:{selected:true,updatedAt:1}}}));
  window.__BUZZ_E2E__.mock = {
    searchProfiles:[
      {pubkey:bryce,displayName:'Bryce',about:'Human. Reviewing the Dervo sample.',isAgent:false},
      {pubkey:finch,displayName:'Finch',about:'Design agent. Local fixture. Tool and model not configured.',isAgent:true,ownerPubkey:bryce},
      {pubkey:moss,displayName:'Moss',about:'Engineering agent. Local fixture. Tool and model not configured.',isAgent:true,ownerPubkey:bryce}
    ],
    relayAgents:[
      {pubkey:finch,ownerPubkey:bryce,name:'Finch',agentType:'Design',status:'offline',channelIds:[room,'b5e2f8a1-3c44-5912-9e67-4a8d1f2b3c4e']},
      {pubkey:moss,ownerPubkey:bryce,name:'Moss',agentType:'Engineering',status:'offline',channelIds:[room,'1c7e1c02-87bb-5e88-b2da-5a7a9432d0c9']}
    ],managedAgents:[],personas:[],teams:[]
  };
  window.__BUZZ_E2E_EMPTY_PROJECTS__ = true;
  window.__BUZZ_E2E_EXTRA_PROJECT_EVENTS__ = [{id:'d3'.repeat(32),pubkey:bryce,kind:30621,created_at:1788955200,content:'',sig:'0'.repeat(128),tags:[['d','dervo'],['name','Dervo'],['description','First-run flow. Local sample project.'],['buzz-channel',room],['buzz-related-channel','b5e2f8a1-3c44-5912-9e67-4a8d1f2b3c4e'],['buzz-related-channel','1c7e1c02-87bb-5e88-b2da-5a7a9432d0c9']]}];
  if (!location.hash) location.hash = '/projects/' + encodeURIComponent(projectId);
  Object.defineProperty(window,'__BUZZ_E2E_INVOKE_MOCK_COMMAND__',{configurable:true,set(invoke){
    Object.defineProperty(window,'__BUZZ_E2E_INVOKE_MOCK_COMMAND__',{value:invoke,writable:true,configurable:true});
    window.__DERVO_FIXTURE_READY__ = seed(invoke);
  }});
  async function seed(invoke) {
    const scenario = await (await fetch('/scenario.json')).json();
    await invoke('update_profile',{displayName:'Bryce',about:'Human. Reviewing the Dervo sample.'});
    const channels = await invoke('get_open_channel_directory');
    for (const c of channels) {
      const target = scenario.channels.find(t => t.id === c.id);
      if (!target) {await invoke('delete_channel',{channelId:c.id});continue;}
      await invoke('join_channel',{channelId:c.id});
      await invoke('set_channel_topic',{channelId:c.id,topic:''});
      await invoke('set_channel_purpose',{channelId:c.id,purpose:''});
      await invoke('update_channel',{input:{channelId:c.id,name:target.name,description:'Dervo first-run flow. '+target.role+' conversation.'}});
      const {members}=await invoke('get_channel_members',{channelId:c.id});
      for (const m of members) if(m.pubkey!==bryce) await invoke('remove_channel_member',{channelId:c.id,pubkey:m.pubkey});
      await invoke('add_channel_members',{channelId:c.id,pubkeys:[finch,moss],role:'bot'});
    }
    const emit=window.__BUZZ_E2E_EMIT_MOCK_MESSAGE__;
    window.__DERVO_SAMPLE_EVENTS__=scenario.events.map(e=>emit(e));
    const stored=JSON.parse(localStorage.getItem(scenario.storageKey)||'null');
    if(stored?.version===1) for(const d of stored.decisions) emit(d);
    await window.__BUZZ_E2E_QUERY_CLIENT__?.invalidateQueries({});
    return {projectId,channels:scenario.channels};
  }
})();
