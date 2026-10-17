import {useEffect,useMemo,useRef,useState} from 'react';
import {Alert,Keyboard,KeyboardAvoidingView,Platform,Pressable,ScrollView,StyleSheet,Text,TextInput,View,type NativeScrollEvent,type NativeSyntheticEvent} from 'react-native';
import {router,useLocalSearchParams} from 'expo-router';
import {useSafeAreaInsets} from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';
import {unconfirmed,type Pending} from '../../../../companion/protocol/phone.ts';
import {HOME_CHANNEL,MAX_ANSWER,MAX_HOME_TEXT,MAX_TEXT,type Answer,type Ask} from '../../../../companion/protocol/messages.ts';
import {useLink} from '../../store';
import {BackIcon,Glyph,NextIcon,SendIcon,StopIcon} from '../../ui/glyphs';
import {font,radius,size,space,useColors,type Colors} from '../../ui/theme';
import {Shimmer,thinkingStep,useNow,useTick} from '../../ui/Presence';
import {AskCard,decisionFor,type AskHandlers} from '../../ui/AskCard';

/** How long a send can go unanswered by a connected Mac before the phone says it hasn't confirmed. */
const SLOW_ACK_MS=30_000;
/** How long the conversation can take to arrive from a connected Mac before the phone says it couldn't load it. */
const LOAD_MS=10_000;
/** A Stop's result shows this long after you tapped it. */
const STOP_SHOWN_MS=2*60_000;

/** One thread: its live line, its recent conversation, and a composer. Sending goes to the Mac as the same coding
 * request the desktop composer makes. While the Mac is away a message waits, sealed, and says so. */
export default function Thread(){
  const c=useColors();
  const s=useMemo(()=>styles(c),[c]);
  const insets=useSafeAreaInsets();
  const {id,p,focus}=useLocalSearchParams<{id:string;p:string;focus?:string}>();
  const {link,state,live}=useLink();
  const now=useNow(5_000);
  // The same screen shows a project's chat with Dervo's coordinator, where a message starts or routes work.
  const home=id===HOME_CHANNEL;
  const projects=state?.home?.projects??[];
  const projectName=projects.find(item=>item.id===p)?.name??'';
  const [text,setText]=useState('');
  const scroll=useRef<ScrollView>(null);
  const nearBottom=useRef(true);
  const lastSend=useRef(0);
  const [keyboard,setKeyboard]=useState(false);
  useEffect(()=>{
    const show=Keyboard.addListener(Platform.OS==='ios'?'keyboardWillShow':'keyboardDidShow',()=>{setKeyboard(true);nearBottom.current=true;});
    const hide=Keyboard.addListener(Platform.OS==='ios'?'keyboardWillHide':'keyboardDidHide',()=>setKeyboard(false));
    return ()=>{show.remove();hide.remove();};
  },[]);
  useEffect(()=>{if(link&&id&&p)link.open(p,id);return ()=>link?.close();},[link,id,p]);
  const row=state?.home?.rows.find(r=>r.id===id);
  const shownThread=live?.thread?.channelId===id&&live.thread.projectId===p?live.thread:null;
  // The phone's placeholder has at 0; only a copy from the Mac counts as loaded.
  const thread=shownThread&&shownThread.at>0&&!shownThread.missing?shownThread:null;
  const connected=!!live?.connected;
  const macOnline=connected&&!!live?.macOnline;
  const mac=state?.home?.mac??'your Mac';
  // The wait for the conversation starts over when the thread changes or the Mac comes back.
  const [openedAt,setOpenedAt]=useState(()=>Date.now());
  useEffect(()=>{setOpenedAt(Date.now());},[id,p,macOnline]);
  const slowLoad=!thread&&macOnline&&now-openedAt>LOAD_MS;
  const gone=!!shownThread?.missing;
  const base=thread?.messages??[];
  // What the thread waits on you for, from the Mac's own records. Each shows as a card you can act on.
  const asks=thread?.asks??[];
  const approvals=thread?.approvals===true;
  // A decision whose card is gone (the Mac took it) stays as one line for a little while, so you see it went through.
  const decided=(state?.outbox??[]).filter(item=>(item.action.kind==='answer'||item.action.kind==='scope'||item.action.kind==='review')
    &&item.action.channelId===id&&item.action.projectId===p&&now-item.at<STOP_SHOWN_MS&&!asks.some(ask=>decisionFor(ask,[item])));
  // The latest one the Mac took. Until Home catches up, the strip says that instead of what it last showed.
  const settled=asks.length?null:decided.filter(item=>item.state==='done'||item.state==='queued').at(-1)??null;
  // What each decision was, in words, kept from the card you tapped.
  const [said,setSaid]=useState<Record<string,string>>({});
  // An older Mac sends no cards: its question then shows as Dervo's last word, unless the thread already says it, or
  // you just answered. While a newer Mac's copy is still on its way, the card will show instead.
  const question=!asks.length&&!settled&&(!!thread||!macOnline)&&row?.bucket==='needs'&&row.question&&!base.some(m=>m.text.includes(row.question!))?row.question:null;
  const messages=question?[...base,{id:'question',from:'dervo' as const,text:question,at:null}]:base;
  const thinking=messages.find(m=>m.pending);
  useTick(!!thinking);
  // "Sent to Feel premium." opens that thread, when Home knows it in this project.
  const threadNamed=(name:string)=>state?.home?.rows.find(r=>r.projectId===p&&r.name===name);
  // A sent message shows once, in the thread. Until the Mac's copy arrives it shows here with where it is.
  const shown=new Set(messages.filter(m=>m.from==='you').map(m=>m.text.trim()));
  const pending=(state?.outbox??[]).filter((item):item is Pending&{action:{text:string}}=>(item.action.kind==='send'||item.action.kind==='home')
    &&item.action.channelId===id&&item.action.projectId===p&&!((item.state==='done'||item.state==='queued')&&shown.has(item.action.text.trim())));
  const stops=(state?.outbox??[]).filter(item=>item.action.kind==='stop'&&item.action.channelId===id&&item.action.projectId===p
    &&(item.state==='waiting'&&!unconfirmed(item,now)||now-item.at<STOP_SHOWN_MS)).slice(-1);
  const stopping=stops.some(item=>item.state==='waiting'&&!unconfirmed(item,now));
  // Writing an answer, or what should change in a result, uses the composer until it's sent or put away.
  const [writing,setWriting]=useState<Ask|null>(null);
  const input=useRef<TextInput>(null);
  // A card that's gone (answered on the Mac, say), or already decided from here, takes its composer mode with it.
  const writingDecision=writing&&decisionFor(writing,state?.outbox??[]);
  const writingNow=writing&&asks.some(ask=>sameAsk(ask,writing))&&!(writingDecision&&writingDecision.state!=='refused'&&writingDecision.state!=='expired')?writing:null;
  const limit=writingNow?.kind==='question'?MAX_ANSWER:home?MAX_HOME_TEXT:MAX_TEXT;
  const tooLong=text.length>limit;

  // A send the Mac turned down gets a warning tap, once.
  const warned=useRef(new Set<string>());
  useEffect(()=>{
    for(const item of state?.outbox??[]){
      if((item.state==='refused'||item.state==='expired')&&!warned.current.has(item.id)&&now-item.at<60_000){
        warned.current.add(item.id);
        void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
      }
    }
  },[state?.outbox,now]);

  function send(){
    const words=text.trim();
    if(!words||tooLong||!link||!p||!id)return;
    // Two quick taps send once.
    if(Date.now()-lastSend.current<800)return;
    lastSend.current=Date.now();
    void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    if(writingNow?.kind==='question')remember(link.act({kind:'answer',projectId:p,channelId:id,taskId:writingNow.taskId,questionId:writingNow.questionId,answer:{kind:'text',text:words}}),`You answered: ${words}`);
    else if(writingNow?.kind==='review')remember(link.act({kind:'review',projectId:p,channelId:id,taskId:writingNow.taskId,attemptNumber:writingNow.attemptNumber,decision:'rejected',text:words}),`You asked for changes: ${words}`);
    else link.act(home?{kind:'home',projectId:p,channelId:HOME_CHANNEL,text:words}:{kind:'send',projectId:p,channelId:id,text:words});
    setText('');
    setWriting(null);
    nearBottom.current=true;
    requestAnimationFrame(()=>scroll.current?.scrollToEnd({animated:true}));
  }
  function stop(){
    if(!link||!p||!id||stopping)return;
    const go=()=>{void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);link.act({kind:'stop',projectId:p,channelId:id});};
    // On a thread that's asking you something, Stop sits next to your answer, so it asks first.
    if(row?.bucket==='needs'&&Platform.OS!=='web'){
      Alert.alert('Stop this run?',`${mac} stops it where it is. You can send it a new message after.`,[{text:'Cancel',style:'cancel'},{text:'Stop',style:'destructive',onPress:go}]);
      return;
    }
    go();
  }
  /** A send that didn't go through: put it back in the composer, or drop it. */
  function fix(item:Pending){
    if(!link||(item.action.kind!=='send'&&item.action.kind!=='home'))return;
    const words=item.action.text;
    const edit=()=>{link.discard(item.id);setText(words);};
    const remove=()=>link.discard(item.id);
    if(Platform.OS==='web'){edit();return;}
    const lost=unconfirmed(item,Date.now());
    Alert.alert(lost?'Not confirmed':'Didn\'t send',lost?`${mac} may have run this already. Check the thread before you send it again.`:item.detail||'This message didn\'t reach the thread.',
      [{text:'Edit and send again',onPress:edit},{text:'Remove',style:'destructive',onPress:remove},{text:'Cancel',style:'cancel'}]);
  }
  function remember(item:Pending,words:string){setSaid(now=>({...now,[item.id]:words}));}
  // Deciding from the card ends any answer you'd started writing for it, so the same thing can't be sent twice.
  const on:AskHandlers={
    answer:(ask,answer:Answer)=>{
      if(!link||!p||!id)return;
      const label=answer.kind==='option'?ask.options.find(option=>option.id===answer.optionId)?.label:null;
      remember(link.act({kind:'answer',projectId:p,channelId:id,taskId:ask.taskId,questionId:ask.questionId,answer}),answer.kind==='skip'?'You skipped this question':label?`You chose ${label}`:'You answered');
      setWriting(null);
    },
    scope:(ask,decision)=>{
      if(!link||!p||!id)return;
      remember(link.act({kind:'scope',projectId:p,channelId:id,taskId:ask.taskId,scopeVersion:ask.scopeVersion,decision}),decision==='approve'?'You approved this run':'You declined this run');
      setWriting(null);
    },
    accept:ask=>{
      if(!link||!p||!id)return;
      remember(link.act({kind:'review',projectId:p,channelId:id,taskId:ask.taskId,attemptNumber:ask.attemptNumber,decision:'approved'}),'You accepted this result');
      setWriting(null);
    },
    write:ask=>{setWriting(ask);requestAnimationFrame(()=>input.current?.focus());},
  };
  function scrolled(event:NativeSyntheticEvent<NativeScrollEvent>){
    const {contentOffset,layoutMeasurement,contentSize}=event.nativeEvent;
    nearBottom.current=contentOffset.y+layoutMeasurement.height>contentSize.height-80;
  }

  const placeholder=!connected?'No connection. Your message will wait on this phone.'
    :!macOnline?`${mac} is asleep or offline. Your message will wait.`
    :writingNow?.kind==='question'?'Your answer':writingNow?.kind==='review'?'What should change?'
    :home?'Ask Dervo to start or hand off work':question?'Answer here':`Message ${row?.name??'this thread'}`;
  // The question itself shows once, in the conversation; the strip only says the run is paused on it.
  // A working thread says what it's doing once, at the end of the conversation where its output lands.
  const stripText=settled?settled.detail||'Done.':question||asks.some(ask=>ask.kind==='question')?'Paused for your answer'
    :asks.some(ask=>ask.kind==='scope')?'Waiting for your OK to start':asks.some(ask=>ask.kind==='review')?'Finished. Ready for your review':row?.bucket==='working'&&macOnline&&!home?`Working on ${mac}`:row?.line||'Idle';
  const working=!home&&row?.bucket==='working'&&macOnline&&!!thread;

  return <KeyboardAvoidingView style={s.screen} behavior={Platform.OS==='ios'?'padding':undefined}>
    <View style={[s.head,{paddingTop:insets.top+4},home&&projects.length>1&&{borderBottomWidth:0}]}>
      <Pressable accessibilityRole="button" accessibilityLabel="Back" hitSlop={10} onPress={()=>router.back()} style={({pressed})=>[s.back,pressed&&{backgroundColor:c.press}]}>
        <BackIcon color={c.ink}/>
      </Pressable>
      <View style={{flex:1,minWidth:0}}>
        <Text style={s.title} numberOfLines={1} accessibilityRole="header">{home?'Dervo':row?.name??thread?.name??'Thread'}</Text>
        {!home&&!!row?.project&&<Text style={s.subtitle} numberOfLines={1}>{row.project}</Text>}
        {home&&projects.length<=1&&!!projectName&&<Text style={s.subtitle} numberOfLines={1}>{projectName}</Text>}
      </View>
    </View>
    {home&&projects.length>1&&<ScrollView horizontal showsHorizontalScrollIndicator={false} style={s.chipsBar} contentContainerStyle={s.chips}>
      {projects.map(item=><Pressable key={item.id} accessibilityRole="button" accessibilityState={{selected:item.id===p}} hitSlop={{top:4,bottom:4}}
        onPress={()=>{void Haptics.selectionAsync();router.setParams({p:item.id});}}
        style={[s.chip,item.id===p&&{backgroundColor:c.ink,borderColor:c.ink}]}>
        <Text style={[s.chipText,item.id===p&&{color:c.canvas}]}>{item.name}</Text>
      </Pressable>)}
    </ScrollView>}
    {row&&<View style={s.liveWrap}>
      <View style={[s.live,row.bucket==='working'&&macOnline&&!settled?{backgroundColor:c.liveWash}:row.bucket==='needs'&&!settled?{backgroundColor:c.rustWash}:{backgroundColor:c.sunk}]}>
        <Glyph bucket={settled?'finished':row.bucket} size={14} accent={row.bucket==='needs'&&!settled} stale={!macOnline||row.quiet}/>
        <Text style={s.liveText} numberOfLines={2}>{stripText}</Text>
        {row.stoppable&&<Pressable accessibilityRole="button" accessibilityLabel="Stop this run" disabled={!macOnline||stopping} onPress={stop} hitSlop={8}
          style={({pressed})=>[s.stop,row.bucket==='needs'&&{borderColor:c.line},pressed&&{backgroundColor:c.press},(!macOnline||stopping)&&{opacity:.45}]}>
          <StopIcon color={row.bucket==='needs'?c.ink2:c.ink}/><Text style={[s.stopText,row.bucket==='needs'&&{color:c.ink2}]}>{stopping?'Stopping':'Stop'}</Text>
        </Pressable>}
      </View>
      {row.stoppable&&!macOnline&&<Text style={s.note}>{connected?`Stop needs ${mac} awake.`:'Stop needs a connection.'}</Text>}
      {stops.map(item=><Status key={item.id} item={item} s={s} c={c} mac={mac} macOnline={macOnline} connected={connected} now={now}/>)}
    </View>}
    <ScrollView ref={scroll} style={{flex:1}} contentContainerStyle={s.messages} keyboardDismissMode="interactive" scrollEventThrottle={100} onScroll={scrolled}
      onContentSizeChange={()=>{if(nearBottom.current)scroll.current?.scrollToEnd({animated:false});}}>
      {!thread&&!gone&&<Text style={s.note}>{!connected?'No connection, so the conversation can\'t load. Home shows what your Mac last sent.'
        :!macOnline?`${mac} is asleep or offline, so the conversation can't load. Home shows what it last sent.`
        :slowLoad?`Couldn't load this conversation from ${mac}. Go back and open it again.`:'Loading the conversation…'}</Text>}
      {gone&&<Text style={s.note}>This thread isn't on {mac} any more.</Text>}
      {thread?.earlier&&<Text style={s.note}>Older messages are on your Mac.</Text>}
      {thread&&!messages.length&&!asks.length&&!decided.length&&<Text style={s.note}>{home?'Tell Dervo what you want done. It starts a thread or sends it to the one already on it.':'No messages in this thread yet.'}</Text>}
      {messages.map(m=>m.from==='you'
        ?<View key={m.id} style={s.mine}><Text style={s.mineText} selectable>{m.text}</Text></View>
        :m.id==='question'?<View key={m.id} style={s.ask}><Text style={s.askLabel}>Waiting on you</Text><Text style={s.agent} selectable>{m.text}</Text></View>
        :m.pending?<Step key={m.id} at={m.at} s={s}/>
        :home&&m.from==='dervo'?<Reply key={m.id} text={m.text} s={s} c={c} find={threadNamed} project={p}/>
        :<Text key={m.id} style={m.from==='agent'?s.agent:s.system} selectable>{m.text}</Text>)}
      {working&&<View style={s.working} accessibilityRole="text" accessibilityLabel={`Working: ${row!.line}`}>
        <Glyph bucket="working" size={14}/><Shimmer text={row!.line||'Working'} style={s.workingText}/>
      </View>}
      {asks.map(ask=><AskCard key={askKey(ask)} ask={ask} approvals={approvals} decision={decisionFor(ask,state?.outbox??[])} c={c} mac={mac}
        macOnline={macOnline} connected={connected} now={now} on={on}/>)}
      {decided.map(item=><View key={item.id} style={s.decided}>
        <Text style={s.decidedText} numberOfLines={3}>{said[item.id]??decisionWords(item)}</Text>
        <Status item={item} s={s} c={c} mac={mac} macOnline={macOnline} connected={connected} now={now}/>
      </View>)}
      {pending.map(item=><Pressable key={item.id} disabled={!bad(item,now)} onPress={()=>fix(item)} accessibilityHint={bad(item,now)?'Edit, send again or remove':undefined}
        style={{alignSelf:'flex-end',maxWidth:'85%',gap:4}}>
        <View style={[s.mine,{maxWidth:'100%'},bad(item,now)&&{opacity:.7}]}><Text style={s.mineText}>{item.action.text}</Text></View>
        <Status item={item} s={s} c={c} mac={mac} macOnline={macOnline} connected={connected} now={now} right/>
      </Pressable>)}
    </ScrollView>
    <View style={[s.composerWrap,{paddingBottom:keyboard?space.s2:Math.max(insets.bottom,space.s3)}]}>
      {writingNow&&<View style={s.writing}>
        <Text style={s.writingText} numberOfLines={1}>{writingNow.kind==='question'?`Answering: ${writingNow.ask}`:`Changes for: ${writingNow.what||'this result'}`}</Text>
        <Pressable accessibilityRole="button" accessibilityLabel="Stop writing this" hitSlop={8} onPress={()=>setWriting(null)} style={s.writingCancelHit}>
          <Text style={s.writingCancel}>Cancel</Text>
        </Pressable>
      </View>}
      {text.length>limit*0.8&&<Text style={[s.count,tooLong&&{color:c.rust}]}>{tooLong?`Too long for one message. `:''}{text.length.toLocaleString()} / {limit.toLocaleString()}</Text>}
      <View style={s.composer}>
        <TextInput ref={input} style={s.input} value={text} onChangeText={setText} multiline numberOfLines={1} placeholder={placeholder} autoFocus={focus==='1'}
          placeholderTextColor={c.textFaint} accessibilityLabel="Message"/>
        <Pressable accessibilityRole="button" accessibilityLabel="Send" disabled={!text.trim()||tooLong} onPress={send} hitSlop={6}
          style={({pressed})=>[s.send,(!text.trim()||tooLong)&&{backgroundColor:c.lineStrong},pressed&&{opacity:.8}]}>
          <SendIcon color={text.trim()&&!tooLong?c.pineInk:c.faint}/>
        </Pressable>
      </View>
    </View>
  </KeyboardAvoidingView>;
}

/** A decision in words, for when the card you tapped is gone (after a restart, say). */
function decisionWords(item:Pending){
  const a=item.action;
  if(a.kind==='answer')return a.answer.kind==='skip'?'You skipped this question':a.answer.kind==='text'?`You answered: ${a.answer.text}`:'You answered';
  if(a.kind==='scope')return a.decision==='approve'?'You approved this run':'You declined this run';
  if(a.kind==='review')return a.decision==='approved'?'You accepted this result':a.text?`You asked for changes: ${a.text}`:'You asked for changes';
  return '';
}

function askKey(ask:Ask){return ask.kind==='question'?`q:${ask.taskId}:${ask.questionId}`:ask.kind==='scope'?`s:${ask.taskId}:${ask.scopeVersion}`:`r:${ask.taskId}:${ask.attemptNumber}`;}
function sameAsk(a:Ask,b:Ask){return askKey(a)===askKey(b);}

/** A send that needs you to do something: turned down, out of time, or never confirmed. */
function bad(item:Pending,now:number){return item.state==='refused'||item.state==='expired'||unconfirmed(item,now);}

/** Where Dervo's answer will land: one step, shimmering, its words moving on with the clock. */
function Step({at,s}:{at:number|null;s:ReturnType<typeof styles>}){
  const step=thinkingStep(at);
  return <View style={s.step} accessible accessibilityRole="progressbar" accessibilityLabel={step.waited?`${step.text}, ${step.waited}`:step.text}>
    <Glyph bucket="working" size={14}/>
    <Shimmer text={step.text} style={s.stepText}/>
    {!!step.waited&&<Text style={s.stepWaited}> · {step.waited}</Text>}
  </View>;
}

/** Dervo's reply in its chat. Each "Sent to <thread>." line opens that thread. */
function Reply({text,s,c,find,project}:{text:string;s:ReturnType<typeof styles>;c:Colors;find:(name:string)=>{id:string}|undefined;project:string|undefined}){
  // Consecutive hand-offs read as one list under the answer.
  const blocks:{prose?:string;sent?:string[]}[]=[];
  for(const line of text.split('\n\n')){
    const sent=/^Sent to (.+)\.$/.exec(line);
    const last=blocks.at(-1);
    if(sent){if(last?.sent)last.sent.push(sent[1]);else blocks.push({sent:[sent[1]]});}
    else blocks.push({prose:line});
  }
  return <View style={{gap:10}}>{blocks.map((block,i)=>block.prose!==undefined?<Text key={i} style={s.agent} selectable>{block.prose}</Text>
    :<View key={i} style={{marginTop:-4}}>{block.sent!.map(name=>{
      const target=find(name);
      return <Pressable key={name} disabled={!target} accessibilityRole={target?'link':undefined} accessibilityLabel={target?`Open ${name}`:`Sent to ${name}`}
        onPress={()=>target&&router.push({pathname:'/thread/[id]',params:{id:target.id,p:project??''}})}
        style={({pressed})=>[s.sent,pressed&&{backgroundColor:c.press}]}>
        <Text style={s.sentText}>Sent to <Text style={target?s.sentName:undefined}>{name}</Text></Text>
        {!!target&&<NextIcon color={c.faint}/>}
      </Pressable>;
    })}</View>)}</View>;
}

function Status({item,s,c,mac,macOnline,connected,now,right=false}:{item:Pending;s:ReturnType<typeof styles>;c:Colors;mac:string;macOnline:boolean;connected:boolean;now:number;right?:boolean}){
  const stop=item.action.kind==='stop';
  const lost=unconfirmed(item,now);
  const text=lost?(stop?`${mac} didn't confirm the Stop. Check the thread.`:`Not confirmed by ${mac}. Tap to check before sending again.`)
    :item.state==='waiting'?(!connected?'Waiting for a connection':!macOnline?`Queued until ${mac} is back`
      :now-item.at>SLOW_ACK_MS?`${mac} hasn't confirmed yet`:stop?'Stopping…':'Sending…')
    :item.state==='done'?item.detail||(stop?'Stopped':'Sent')
    :item.state==='queued'?item.detail||'Queued. It goes in when the current run ends.'
    :`${item.detail||'Didn\'t send.'}${item.action.kind==='send'||item.action.kind==='home'?' Tap to edit or remove.':''}`;
  const isBad=item.state==='refused'||item.state==='expired'||lost;
  return <Text style={[s.status,right&&{textAlign:'right'},isBad&&{color:c.rust}]}>{text}</Text>;
}

const styles=(c:Colors)=>StyleSheet.create({
  screen:{flex:1,backgroundColor:c.canvas},
  head:{flexDirection:'row',alignItems:'center',gap:6,paddingHorizontal:space.s2,paddingBottom:space.s2,borderBottomWidth:StyleSheet.hairlineWidth,borderBottomColor:c.line,backgroundColor:c.canvas},
  back:{width:44,height:44,borderRadius:22,alignItems:'center',justifyContent:'center'},
  title:{fontFamily:font.semibold,fontSize:size.title,color:c.ink},
  subtitle:{fontFamily:font.text,fontSize:size.caption,color:c.muted},
  chipsBar:{flexGrow:0,borderBottomWidth:StyleSheet.hairlineWidth,borderBottomColor:c.line},
  chips:{gap:8,paddingHorizontal:space.s4,paddingVertical:space.s2},
  chip:{paddingHorizontal:14,minHeight:36,justifyContent:'center',borderRadius:18,borderWidth:StyleSheet.hairlineWidth,borderColor:c.lineStrong,backgroundColor:c.canvas},
  chipText:{fontFamily:font.medium,fontSize:size.small,color:c.ink2},
  liveWrap:{paddingHorizontal:space.s4,paddingTop:space.s3,gap:6},
  live:{flexDirection:'row',alignItems:'center',gap:8,paddingVertical:7,paddingLeft:10,paddingRight:6,borderRadius:radius.r2+4,minHeight:46},
  liveText:{flex:1,fontFamily:font.text,fontSize:size.small,color:c.ink2},
  stop:{flexDirection:'row',alignItems:'center',gap:6,paddingHorizontal:12,minHeight:34,borderRadius:9,backgroundColor:c.canvas,borderWidth:StyleSheet.hairlineWidth,borderColor:c.lineStrong},
  stopText:{fontFamily:font.semibold,fontSize:size.small-0.5,color:c.ink},
  note:{fontFamily:font.text,fontSize:size.small,color:c.muted,lineHeight:size.small*1.45},
  messages:{paddingHorizontal:space.s4,paddingVertical:space.s4,gap:16},
  mine:{alignSelf:'flex-end',maxWidth:'85%',backgroundColor:c.bubble,paddingVertical:9,paddingHorizontal:14,borderRadius:18},
  mineText:{fontFamily:font.text,fontSize:size.read,lineHeight:size.read*1.5,color:c.ink},
  agent:{fontFamily:font.text,fontSize:size.read,lineHeight:size.read*1.5,color:c.ink},
  ask:{gap:4,paddingLeft:12,borderLeftWidth:2,borderLeftColor:c.rust},
  askLabel:{fontFamily:font.semibold,fontSize:size.caption,color:c.muted},
  system:{fontFamily:font.text,fontSize:size.small,lineHeight:size.small*1.5,color:c.muted},
  working:{flexDirection:'row',alignItems:'center',gap:10,minHeight:24},
  workingText:{fontFamily:font.text,fontSize:size.read,lineHeight:size.read*1.5,color:c.muted},
  step:{flexDirection:'row',alignItems:'center',gap:10,minHeight:24},
  stepText:{fontFamily:font.text,fontSize:size.read,lineHeight:size.read*1.5,color:c.muted},
  stepWaited:{fontFamily:font.text,fontSize:size.caption,color:c.textFaint,fontVariant:['tabular-nums']},
  sent:{flexDirection:'row',alignItems:'center',alignSelf:'flex-start',gap:6,paddingVertical:8,paddingHorizontal:10,marginLeft:-10,borderRadius:10,minHeight:40},
  sentText:{fontFamily:font.text,fontSize:size.small+0.5,color:c.muted},
  sentName:{fontFamily:font.medium,color:c.ink},
  status:{fontFamily:font.text,fontSize:size.caption,color:c.muted},
  count:{fontFamily:font.text,fontSize:size.caption,color:c.muted,textAlign:'right',paddingHorizontal:6,paddingBottom:4,fontVariant:['tabular-nums']},
  writing:{flexDirection:'row',alignItems:'center',gap:12,paddingHorizontal:8,paddingBottom:6},
  writingText:{flex:1,fontFamily:font.medium,fontSize:size.caption,color:c.muted},
  writingCancelHit:{minHeight:44,minWidth:44,paddingHorizontal:8,alignItems:'center',justifyContent:'center'},
  decided:{gap:3,paddingLeft:12,borderLeftWidth:2,borderLeftColor:c.lineStrong},
  decidedText:{fontFamily:font.medium,fontSize:size.small,lineHeight:size.small*1.45,color:c.ink2},
  writingCancel:{fontFamily:font.semibold,fontSize:size.caption,color:c.ink},
  composerWrap:{paddingHorizontal:space.s3,paddingTop:space.s2,backgroundColor:c.canvas},
  composer:{flexDirection:'row',alignItems:'flex-end',gap:8,backgroundColor:c.raised,borderWidth:1,borderColor:c.line,borderRadius:radius.composer,
    paddingLeft:16,paddingRight:7,paddingVertical:7,shadowColor:c.shadow,shadowOpacity:.08,shadowRadius:14,shadowOffset:{width:0,height:6}},
  input:{flex:1,fontFamily:font.text,fontSize:size.read,lineHeight:size.read*1.4,color:c.ink,minHeight:30,maxHeight:140,paddingTop:6,paddingBottom:6},
  send:{width:36,height:36,borderRadius:18,backgroundColor:c.pine,alignItems:'center',justifyContent:'center'},
});
