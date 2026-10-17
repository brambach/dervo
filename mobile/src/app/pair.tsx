import {useEffect,useMemo,useRef,useState} from 'react';
import {Linking,Platform,Pressable,ScrollView,StyleSheet,Text,TextInput,View} from 'react-native';
import {Redirect,router,useLocalSearchParams} from 'expo-router';
import {useSafeAreaInsets} from 'react-native-safe-area-context';
import {CameraView,useCameraPermissions} from 'expo-camera';
import * as Haptics from 'expo-haptics';
import {forget,pair,useLink} from '../store';
import {Glyph,Mark} from '../ui/glyphs';
import {pushStatus,registerPush,type PushStatus} from '../push';
import {font,radius,size,space,useColors,type Colors} from '../ui/theme';

/** Errors that mean this code can't work any more, so the digits go and a new code is the way on. */
const DEAD=new Set(['no_room','bad_key','unpaired']);

const ERRORS:Record<string,string>={
  mac_offline:'Your Mac isn\'t connected. Open Dervo on it, show the code again and scan it.',
  no_room:'This code isn\'t open any more. Show a new one on your Mac.',
  bad_key:'This code isn\'t valid any more. Show a new one on your Mac.',
  rate:'Too many tries. Wait a minute, then scan a new code.',
  full:'Too many phones are pairing with this Mac right now. Wait a minute, then scan a new code.',
  unpaired:'Your Mac didn\'t allow this phone, or removed it. Show a new code to pair again.',
};

/** Pairing: scan the code Dervo shows on the Mac. That one scan swaps keys, so from then on only this phone and that
 * Mac can read what passes between them. The smiley shows here, and nowhere in daily use. */
export default function Pair(){
  const c=useColors();
  const s=useMemo(()=>styles(c),[c]);
  const insets=useSafeAreaInsets();
  const {link,state,live}=useLink();
  const [scanning,setScanning]=useState(false);
  const [pasting,setPasting]=useState(Platform.OS==='web');
  const [text,setText]=useState('');
  const [problem,setProblem]=useState('');
  const [slow,setSlow]=useState(false);
  const [noRelay,setNoRelay]=useState(false);
  const [settings,setSettings]=useState(false);
  const [permission,requestPermission]=useCameraPermissions();
  const handled=useRef(false);
  // Pairing that finishes on this screen gets its own moment before Home. Opening an already paired app skips it.
  const [pairing]=useState(()=>!state?.paired);
  const waiting=!!state&&!state.paired&&!!state.code;
  const connected=!!live?.connected;
  useEffect(()=>{
    if(!waiting){setSlow(false);return;}
    // Long enough to walk to the Mac and read the digits.
    const timer=setTimeout(()=>setSlow(true),45_000);
    return ()=>clearTimeout(timer);
  },[waiting]);
  useEffect(()=>{
    if(!waiting||connected){setNoRelay(false);return;}
    const timer=setTimeout(()=>setNoRelay(true),10_000);
    return ()=>clearTimeout(timer);
  },[waiting,connected]);
  // Scanning the Mac's code with the iPhone's own Camera opens dervo://pair?d=…, which lands here.
  const {d}=useLocalSearchParams<{d?:string}>();
  const linked=useRef<string|null>(null);
  useEffect(()=>{
    if(!d||linked.current===d||state?.paired)return;
    linked.current=d;
    use(`dervo://pair?d=${d}`);
  },[d,state?.paired]);
  if(state?.paired)return pairing?<Paired mac={state.offer.name} s={s} c={c}/>:<Redirect href="/"/>;

  function use(code:string){
    if(handled.current)return;
    handled.current=true;
    if(pair(code)){void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);setScanning(false);setProblem('');}
    else{
      // Stop scanning, so a wrong code in view doesn't buzz again and again.
      void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      setScanning(false);
      setProblem('That isn\'t a Dervo pairing code, or it has expired. Show a new one on your Mac.');
    }
    setTimeout(()=>{handled.current=false;},1200);
  }
  async function scan(){
    setProblem('');
    if(!permission?.granted){
      const answer=await requestPermission();
      if(!answer.granted){
        setProblem('Dervo needs the camera to scan the code. You can paste the code instead.');
        setSettings(!answer.canAskAgain);
        setPasting(true);return;
      }
    }
    setScanning(true);
  }
  const error=live?.error?ERRORS[live.error]??null:null;
  const dead=!!live?.error&&DEAD.has(live.error);
  const startAgain=()=>{forget();handled.current=false;};

  return <ScrollView style={s.screen} contentContainerStyle={[s.body,{paddingTop:insets.top+space.s6,paddingBottom:insets.bottom+space.s5}]} keyboardShouldPersistTaps="handled">
    <Mark size={48}/>
    <Text style={s.title} accessibilityRole="header">Dervo on your phone</Text>
    <Text style={s.lead}>See what every agent is doing, answer the ones waiting on you, and send messages. It all runs on your Mac. Your phone only shows it.</Text>
    {waiting?<View style={s.card}>
      <Text style={s.cardTitle}>{dead?'This code can\'t be used':!connected?'Connecting to Dervo\'s relay…':`Waiting for ${state!.offer.name}…`}</Text>
      {!!link&&connected&&!dead&&<View style={s.digitsWrap} accessible accessibilityLabel={`Check your Mac shows ${link.digits.split('').join(' ')}`}>
        <Text style={s.digits}>{link.digits.slice(0,3)} {link.digits.slice(3)}</Text>
      </View>}
      <Text style={s.cardText}>{error??(!connected?(noRelay?'Can\'t reach Dervo\'s relay. Check this phone\'s connection.':'One moment.')
        :'Check that your Mac shows the same six digits, then click Allow there.')}</Text>
      {!error&&connected&&slow&&<Text style={s.hint}>Nothing came back yet. Keep Dervo open on your Mac. If its code has changed, scan the new one.</Text>}
      {dead?<Pressable accessibilityRole="button" onPress={startAgain} style={({pressed})=>[s.primary,pressed&&{opacity:.85}]}>
        <Text style={s.primaryText}>Scan a new code</Text>
      </Pressable>:<Pressable accessibilityRole="button" onPress={startAgain} style={({pressed})=>[s.quiet,pressed&&{backgroundColor:c.press}]}>
        <Text style={s.quietText}>Start again</Text>
      </Pressable>}
    </View>:<>
      <View style={s.steps}>
        <Step n={1} text="On your Mac, open Dervo, then Settings, Preferences, Phone." s={s}/>
        <Step n={2} text="Scan the code it shows." s={s}/>
      </View>
      {scanning&&Platform.OS!=='web'?<View style={s.camera}>
        <CameraView style={StyleSheet.absoluteFill} facing="back" barcodeScannerSettings={{barcodeTypes:['qr']}} onBarcodeScanned={result=>use(result.data)}/>
        <View style={s.frame} pointerEvents="none"/>
        <Pressable accessibilityRole="button" onPress={()=>setScanning(false)} style={({pressed})=>[s.cancel,pressed&&{opacity:.8}]}>
          <Text style={s.cancelText}>Cancel</Text>
        </Pressable>
      </View>:Platform.OS!=='web'&&<Pressable accessibilityRole="button" onPress={()=>void scan()} style={({pressed})=>[s.primary,pressed&&{opacity:.85}]}>
        <Text style={s.primaryText}>Scan the code</Text>
      </Pressable>}
      {pasting?<View style={s.paste}>
        <TextInput style={s.input} value={text} onChangeText={setText} placeholder="dervo://pair?d=…" placeholderTextColor={c.textFaint} autoCapitalize="none" autoCorrect={false} accessibilityLabel="Pairing code"/>
        <Pressable accessibilityRole="button" disabled={!text.trim()} onPress={()=>use(text)} style={({pressed})=>[s.secondary,!text.trim()&&{opacity:.4},pressed&&{backgroundColor:c.press}]}>
          <Text style={s.secondaryText}>Pair</Text>
        </Pressable>
      </View>:<Pressable accessibilityRole="button" onPress={()=>setPasting(true)} style={({pressed})=>[s.quiet,pressed&&{backgroundColor:c.press}]}>
        <Text style={s.quietText}>Paste the code instead</Text>
      </Pressable>}
      {!!(problem||error)&&<Text style={s.problem}>{problem||error}</Text>}
      {settings&&<Pressable accessibilityRole="button" onPress={()=>void Linking.openSettings()} style={({pressed})=>[s.quiet,pressed&&{backgroundColor:c.press}]}>
        <Text style={s.quietText}>Turn on the camera in iOS Settings</Text>
      </Pressable>}
    </>}
    <Text style={s.fine}>Dervo's relay passes sealed messages between your phone and your Mac. It can't read them.</Text>
  </ScrollView>;
}

/** You're paired: what the phone can do, and the one place it asks to send notices, saying what they hold. */
function Paired({mac,s,c}:{mac:string;s:ReturnType<typeof styles>;c:Colors}){
  const insets=useSafeAreaInsets();
  const {link}=useLink();
  const [push,setPush]=useState<PushStatus|null>(null);
  useEffect(()=>{void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);void pushStatus().then(setPush);},[]);
  const home=()=>router.replace('/');
  async function notices(){
    await registerPush(token=>link?.setPushToken(token),true);
    home();
  }
  return <ScrollView style={s.screen} contentContainerStyle={[s.body,{paddingTop:insets.top+space.s6,paddingBottom:insets.bottom+space.s5,flexGrow:1}]}>
    <Mark size={56}/>
    <Text style={s.title} accessibilityRole="header">{'You\u2019re paired'}</Text>
    <Text style={s.lead}>This phone and {mac} are linked. What passes between them is sealed for the two of them.</Text>
    <View style={s.steps}>
      <Feature bucket="working" title="Every agent at a glance" text="What each one is doing, live while your Mac is awake." s={s}/>
      <Feature bucket="needs" title="Answer the ones waiting on you" text="Pick a choice or write your own, and the agent carries on." s={s}/>
      <Feature bucket="finished" title="Approve runs and accept results" text={`Once you allow it for this phone on ${mac}, in Settings, Phone.`} s={s}/>
    </View>
    <View style={{flex:1}}/>
    {push===null?<View style={{height:52}}/>:push==='ask'?<>
      <Pressable accessibilityRole="button" onPress={()=>void notices()} style={({pressed})=>[s.primary,pressed&&{opacity:.85}]}>
        <Text style={s.primaryText}>Turn on notifications</Text>
      </Pressable>
      <Text style={[s.hint,{textAlign:'center'}]}>{'They only ever say \u201cSomething needs you.\u201d or \u201cSomething finished.\u201d Never which thread or what it said.'}</Text>
      <Pressable accessibilityRole="button" onPress={home} style={({pressed})=>[s.quiet,{alignSelf:'center',marginLeft:0},pressed&&{backgroundColor:c.press}]}>
        <Text style={s.quietText}>Not now</Text>
      </Pressable>
    </>:<Pressable accessibilityRole="button" onPress={home} style={({pressed})=>[s.primary,pressed&&{opacity:.85}]}>
      <Text style={s.primaryText}>Go to Home</Text>
    </Pressable>}
  </ScrollView>;
}

function Feature({bucket,title,text,s}:{bucket:'working'|'needs'|'finished';title:string;text:string;s:ReturnType<typeof styles>}){
  return <View style={[s.step,{alignItems:'flex-start',paddingVertical:14}]}>
    <View style={{paddingTop:3}}><Glyph bucket={bucket} size={16} accent={bucket==='needs'}/></View>
    <View style={{flex:1,gap:2}}><Text style={[s.stepText,{fontFamily:font.medium}]}>{title}</Text><Text style={s.hint}>{text}</Text></View>
  </View>;
}

function Step({n,text,s}:{n:number;text:string;s:ReturnType<typeof styles>}){
  return <View style={s.step}><View style={s.stepNumber}><Text style={s.stepNumberText}>{n}</Text></View><Text style={s.stepText}>{text}</Text></View>;
}

const styles=(c:Colors)=>StyleSheet.create({
  screen:{flex:1,backgroundColor:c.canvas},
  body:{paddingHorizontal:space.s5,gap:space.s4},
  title:{fontFamily:font.display,fontSize:40,lineHeight:44,color:c.ink,letterSpacing:-0.4,marginTop:space.s2},
  lead:{fontFamily:font.text,fontSize:16,lineHeight:16*1.55,color:c.muted},
  digitsWrap:{alignSelf:'flex-start',paddingVertical:8,paddingHorizontal:14,borderRadius:10,backgroundColor:c.sunk},
  digits:{fontFamily:font.semibold,fontSize:28,letterSpacing:3,color:c.ink},
  steps:{backgroundColor:c.raised,borderWidth:1,borderColor:c.line,borderRadius:radius.card,overflow:'hidden'},
  step:{flexDirection:'row',alignItems:'center',gap:12,paddingVertical:12,paddingHorizontal:14,borderTopWidth:StyleSheet.hairlineWidth,borderTopColor:c.line,marginTop:-StyleSheet.hairlineWidth},
  stepNumber:{width:22,height:22,borderRadius:7,backgroundColor:c.sunk,alignItems:'center',justifyContent:'center'},
  stepNumberText:{fontFamily:font.medium,fontSize:size.caption,color:c.muted,fontVariant:['tabular-nums']},
  stepText:{flex:1,fontFamily:font.text,fontSize:size.body,lineHeight:size.body*1.45,color:c.ink},
  camera:{height:300,borderRadius:radius.r3+4,overflow:'hidden',backgroundColor:'#000'},
  frame:{position:'absolute',left:'18%',right:'18%',top:'15%',bottom:'15%',borderRadius:18,borderWidth:2,borderColor:'rgba(255,255,255,0.85)'},
  primary:{backgroundColor:c.pine,borderRadius:12,paddingVertical:14,alignItems:'center'},
  primaryText:{fontFamily:font.semibold,fontSize:size.body,color:c.pineInk},
  secondary:{borderRadius:10,minHeight:44,paddingHorizontal:16,borderWidth:1,borderColor:c.lineStrong,justifyContent:'center'},
  secondaryText:{fontFamily:font.semibold,fontSize:size.body-0.5,color:c.ink},
  quiet:{alignSelf:'flex-start',marginLeft:-12,minHeight:44,justifyContent:'center',paddingHorizontal:12,borderRadius:8},
  hint:{fontFamily:font.text,fontSize:size.small,lineHeight:size.small*1.45,color:c.muted},
  cancel:{position:'absolute',bottom:12,alignSelf:'center',minHeight:40,paddingHorizontal:18,justifyContent:'center',borderRadius:20,backgroundColor:'rgba(0,0,0,0.55)'},
  cancelText:{fontFamily:font.semibold,fontSize:size.body,color:'#fff'},
  quietText:{fontFamily:font.medium,fontSize:size.body-0.5,color:c.muted},
  paste:{flexDirection:'row',gap:8},
  input:{flex:1,minHeight:44,fontFamily:font.text,fontSize:size.body,color:c.ink,borderWidth:1,borderColor:c.lineStrong,borderRadius:10,paddingHorizontal:12,paddingVertical:10,backgroundColor:c.canvas},
  problem:{fontFamily:font.text,fontSize:size.small,lineHeight:size.small*1.45,color:c.rust},
  card:{backgroundColor:c.raised,borderRadius:radius.card,padding:space.s4,gap:8,borderWidth:1,borderColor:c.line},
  cardTitle:{fontFamily:font.semibold,fontSize:size.title,color:c.ink},
  cardText:{fontFamily:font.text,fontSize:size.body,lineHeight:size.body*1.5,color:c.ink2},
  fine:{fontFamily:font.text,fontSize:size.caption+0.5,lineHeight:18,color:c.textFaint,marginTop:space.s2},
});
