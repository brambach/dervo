import {useRef} from 'react';
import {Alert,Platform,Pressable,StyleSheet,Text,View} from 'react-native';
import * as Haptics from 'expo-haptics';
import {unconfirmed,type Pending} from '../../../companion/protocol/phone.ts';
import type {Answer,Ask} from '../../../companion/protocol/messages.ts';
import {font,radius,size,space,type Colors} from './theme';

export type AskHandlers={
  answer:(ask:Extract<Ask,{kind:'question'}>,answer:Answer)=>void;
  /** Opens the composer for your own words, or for what should change in a result. */
  write:(ask:Ask)=>void;
  scope:(ask:Extract<Ask,{kind:'scope'}>,decision:'approve'|'deny')=>void;
  accept:(ask:Extract<Ask,{kind:'review'}>)=>void};

/** The latest decision this phone sent for an ask, if any. */
export function decisionFor(ask:Ask,outbox:Pending[]):Pending|null{
  const mine=outbox.filter(item=>{
    const a=item.action;
    if(a.kind==='answer')return ask.kind==='question'&&a.taskId===ask.taskId&&a.questionId===ask.questionId;
    if(a.kind==='scope')return ask.kind==='scope'&&a.taskId===ask.taskId&&a.scopeVersion===ask.scopeVersion;
    if(a.kind==='review')return ask.kind==='review'&&a.taskId===ask.taskId&&a.attemptNumber===ask.attemptNumber;
    return false;
  });
  return mine.at(-1)??null;
}

/** One thing a thread waits on you for, with what you can do about it from here. Every tap is a sealed action to the
 * Mac, held to the same rules as Stop, and the card says where it is until the Mac answers. */
export function AskCard({ask,approvals,decision,c,mac,macOnline,connected,now,on}:{ask:Ask;approvals:boolean;decision:Pending|null;c:Colors;mac:string;
  macOnline:boolean;connected:boolean;now:number;on:AskHandlers}){
  const s=styles(c);
  // A decision that's waiting or went through holds the buttons; one the Mac turned down gives them back.
  const lost=!!decision&&unconfirmed(decision,now);
  const settled=!!decision&&!lost&&decision.state!=='refused'&&decision.state!=='expired';
  const status=decision&&(lost?`${mac} didn't confirm this. Check on your Mac before deciding again.`
    :decision.state==='waiting'?(!connected?'Waiting for a connection. It goes when this phone is back online.':!macOnline?`Queued until ${mac} is back.`:'Sending…')
    :decision.detail||(decision.state==='done'?'Done.':decision.state==='queued'?'Saved.':'Didn\'t go through.'));
  const bad=!!decision&&(lost||decision.state==='refused'||decision.state==='expired');
  // Two quick taps, even on two different choices, decide once.
  const lastTap=useRef(0);
  const tap=(go:()=>void)=>()=>{
    if(Date.now()-lastTap.current<800)return;
    lastTap.current=Date.now();
    void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);go();
  };

  const label=ask.kind==='question'?'Waiting on your answer':ask.kind==='scope'?'Waiting for your OK to start':'Ready for your review';
  const body=ask.kind==='question'?ask.ask:ask.what;
  return <View style={s.card}>
    <Text style={s.label}>{label}</Text>
    {!!body&&<Text style={s.body} selectable>{body}</Text>}
    {ask.kind==='scope'&&!!ask.files.length&&<View style={s.files}>
      {ask.files.slice(0,4).map(file=><Text key={file} style={s.file} numberOfLines={1}>{file}</Text>)}
      {ask.files.length>4&&<Text style={s.meta}>and {ask.files.length-4} more</Text>}
    </View>}
    {ask.kind==='review'&&<Text style={s.meta}>{ask.files===1?'1 file changed':`${ask.files} files changed`} · {ask.checked?'Checks passed':'Not checked yet'}</Text>}

    {!settled&&ask.kind==='question'&&<View style={s.actions}>
      {ask.options.map(option=><Pressable key={option.id} accessibilityRole="button" accessibilityLabel={option.detail?`${option.label}. ${option.detail}`:option.label}
        onPress={tap(()=>on.answer(ask,{kind:'option',optionId:option.id}))} style={({pressed})=>[s.option,pressed&&{backgroundColor:c.press}]}>
        <Text style={s.optionText}>{option.label}</Text>
        {!!option.detail&&<Text style={s.optionDetail}>{option.detail}</Text>}
      </Pressable>)}
      <View style={s.row}>
        <Button c={c} s={s} kind={ask.options.length?'quiet':'primary'} text={ask.options.length?'Answer in your own words':'Answer'} onPress={tap(()=>on.write(ask))}/>
        {ask.canSkip&&<Button c={c} s={s} kind="quiet" text="Skip" onPress={tap(()=>on.answer(ask,{kind:'skip'}))}/>}
      </View>
    </View>}

    {!settled&&ask.kind==='scope'&&(approvals&&!ask.phone
      ?<View style={{gap:10}}>
        <Text style={s.meta}>This run asks for more than its own files, so approve it on {mac}, where you can see what it covers.</Text>
        <View style={s.row}><Button c={c} s={s} kind="quiet" text="Don't run" onPress={()=>confirm('Don\'t run this?','Nothing runs. You can ask for it again in the thread.','Don\'t run',()=>on.scope(ask,'deny'))}/></View>
      </View>
      :approvals
      ?<View style={s.row}>
        <Button c={c} s={s} kind="primary" text="Approve and start" onPress={()=>confirm('Start this run?',`${mac} starts it with the files and access shown.`,'Start',()=>on.scope(ask,'approve'))}/>
        <Button c={c} s={s} kind="quiet" text="Don't run" onPress={()=>confirm('Don\'t run this?','Nothing runs. You can ask for it again in the thread.','Don\'t run',()=>on.scope(ask,'deny'))}/>
      </View>
      :<Text style={s.meta}>To approve from this phone, open Dervo on {mac}, then Settings, Phone, and turn on approvals.</Text>)}

    {!settled&&ask.kind==='review'&&approvals&&<View style={s.row}>
      {approvals&&ask.phone&&<Button c={c} s={s} kind="primary" text="Accept" onPress={()=>confirm('Accept this result?',`${mac} commits ${ask.files===1?'its 1 file':`its ${ask.files} files`}.`,'Accept',()=>on.accept(ask))}/>}
      {approvals&&<Button c={c} s={s} kind={ask.phone?'quiet':'primary'} text="Ask for changes" onPress={tap(()=>on.write(ask))}/>}
    </View>}
    {!settled&&ask.kind==='review'&&(!approvals
      ?<Text style={s.meta}>Review this on {mac}. To accept results from this phone, open Settings, Phone there and turn on approvals.</Text>
      :!ask.phone&&<Text style={s.meta}>Accepting this one needs the review sheet on {mac}.</Text>)}

    {!!status&&<Text style={[s.status,bad&&{color:c.rust}]}>{status}</Text>}
  </View>;
}

function confirm(title:string,message:string,yes:string,go:()=>void){
  const run=()=>{void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);go();};
  if(Platform.OS==='web'){run();return;}
  Alert.alert(title,message,[{text:'Cancel',style:'cancel'},{text:yes,onPress:run}]);
}

function Button({c,s,kind,text,onPress}:{c:Colors;s:ReturnType<typeof styles>;kind:'primary'|'quiet';text:string;onPress:()=>void}){
  return <Pressable accessibilityRole="button" onPress={onPress} hitSlop={4}
    style={({pressed})=>[kind==='primary'?s.primary:s.quiet,pressed&&(kind==='primary'?{opacity:.85}:{backgroundColor:c.press})]}>
    <Text style={kind==='primary'?s.primaryText:s.quietText}>{text}</Text>
  </Pressable>;
}

const styles=(c:Colors)=>StyleSheet.create({
  card:{gap:10,padding:space.s4,borderRadius:radius.r3,backgroundColor:c.raised,borderWidth:StyleSheet.hairlineWidth,borderColor:c.lineStrong,
    borderLeftWidth:3,borderLeftColor:c.rust},
  label:{fontFamily:font.semibold,fontSize:size.caption,color:c.rust,letterSpacing:.2},
  body:{fontFamily:font.text,fontSize:size.read,lineHeight:size.read*1.5,color:c.ink},
  files:{gap:2},
  file:{fontFamily:Platform.OS==='ios'?'Menlo':'monospace',fontSize:size.caption,color:c.ink2},
  meta:{fontFamily:font.text,fontSize:size.small,lineHeight:size.small*1.45,color:c.muted},
  actions:{gap:8},
  option:{paddingVertical:11,paddingHorizontal:14,borderRadius:radius.r2+2,borderWidth:1,borderColor:c.lineStrong,backgroundColor:c.canvas,gap:2,minHeight:44,justifyContent:'center'},
  optionText:{fontFamily:font.medium,fontSize:size.body,color:c.ink},
  optionDetail:{fontFamily:font.text,fontSize:size.small,lineHeight:size.small*1.4,color:c.muted},
  row:{flexDirection:'row',flexWrap:'wrap',gap:8},
  primary:{backgroundColor:c.pine,borderRadius:radius.card,paddingVertical:11,paddingHorizontal:16,minHeight:44,justifyContent:'center'},
  primaryText:{fontFamily:font.semibold,fontSize:size.body,color:c.pineInk},
  quiet:{borderRadius:radius.card,paddingVertical:11,paddingHorizontal:14,minHeight:44,justifyContent:'center',borderWidth:StyleSheet.hairlineWidth,borderColor:c.lineStrong},
  quietText:{fontFamily:font.medium,fontSize:size.body,color:c.ink},
  status:{fontFamily:font.text,fontSize:size.caption,color:c.muted},
});
