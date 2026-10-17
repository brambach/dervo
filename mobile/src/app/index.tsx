import {useCallback,useEffect,useMemo,useState} from 'react';
import {LayoutAnimation,Platform,Pressable,RefreshControl,ScrollView,StyleSheet,Text,UIManager,View,useColorScheme} from 'react-native';
import {BlurView} from 'expo-blur';
import {Redirect,router} from 'expo-router';
import {useSafeAreaInsets} from 'react-native-safe-area-context';
import * as Haptics from 'expo-haptics';
import {HOME_CHANNEL,type Bucket,type HomeRow} from '../../../companion/protocol/messages.ts';
import {forget,useLink} from '../store';
import {BUCKETS,freshness,grouped,sentence} from '../ui/summary';
import {Chevron,GearIcon,Glyph,SendIcon} from '../ui/glyphs';
import {useNow} from '../ui/Presence';
import {useReducedMotion} from '../ui/motion';
import {Pulse} from '../ui/Pulse';
import {greeting,short} from '../ui/time';
import {font,size,space,useColors,type Colors} from '../ui/theme';

/** Home: every thread's state, grouped the way the Mac's Home panel groups it, and how current that is. The bar at the
 * bottom opens a chat with Dervo's coordinator, which starts or routes work; everything else you do inside a thread. */
export default function Home(){
  const c=useColors();
  const s=useMemo(()=>styles(c),[c]);
  const insets=useSafeAreaInsets();
  const {link,state,live}=useLink();
  // How current Home is depends on the clock too, so it's re-read even when nothing arrives.
  useNow(15_000);
  const reduced=useReducedMotion();
  const scheme=useColorScheme();
  const [open,setOpen]=useState<Record<Bucket,boolean>>(()=>Object.fromEntries(BUCKETS.map(b=>[b.key,b.open])) as Record<Bucket,boolean>);
  const [refreshing,setRefreshing]=useState(false);
  const homeAt=state?.home?.at??0;
  const refresh=useCallback(()=>{
    setRefreshing(true);
    link?.wake();
    link?.hello();
  },[link]);
  // The spinner stops when a newer Home arrives, or after a few seconds if the Mac doesn't answer.
  useEffect(()=>{setRefreshing(false);},[homeAt]);
  useEffect(()=>{if(!refreshing)return;const timer=setTimeout(()=>setRefreshing(false),4000);return ()=>clearTimeout(timer);},[refreshing]);
  if(!state?.paired)return <Redirect href="/pair"/>;
  const home=state.home;
  const fresh=freshness(home,live,Date.now(),state.homeAt??null);
  const stale=fresh.tone!=='live';
  const groups=grouped(home?.rows??[]);
  const manyProjects=(home?.projects.length??0)>1;
  const firstNeeds=groups.get('needs')?.[0]?.id;
  // Dervo's chat opens on the project you worked in last.
  const latest=[...(home?.rows??[])].sort((a,b)=>(b.at??0)-(a.at??0))[0]?.projectId??home?.projects[0]?.id;
  const ask=()=>{if(latest)router.push({pathname:'/thread/[id]',params:{id:HOME_CHANNEL,p:latest,focus:'1'}});};

  return <View style={s.screen}><ScrollView style={s.screen} contentContainerStyle={{paddingTop:insets.top+space.s3,paddingBottom:insets.bottom+96}}
    refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={c.faint}/>}>
    <View style={s.top}>
      <View style={{flex:1}}/>
      <Pressable accessibilityRole="button" accessibilityLabel="Settings" hitSlop={10} onPress={()=>router.push('/settings')}
        style={({pressed})=>[s.iconButton,pressed&&{backgroundColor:c.press}]}><GearIcon color={c.muted}/></Pressable>
    </View>
    <View style={s.hello}>
      <Text style={s.greeting} accessibilityRole="header">{greeting()}</Text>
      <Text style={s.sentence}>{home?(stale?'When last seen: ':'')+sentence(home):'Waiting for your Mac to send Home.'}</Text>
      <View style={s.asof} accessibilityRole="text"><View style={s.asofDot}>{fresh.tone==='lost'?<Glyph bucket="unknown" size={11}/>:<Pulse live={fresh.tone==='live'}/>}</View>
        <Text style={[s.asofText,fresh.tone==='lost'&&{color:c.ink2}]}>{fresh.text}</Text></View>
      {live?.error==='not_allowed'&&<Pressable accessibilityRole="button" onPress={()=>{forget();router.replace('/pair');}}
        style={({pressed})=>[s.forget,pressed&&{backgroundColor:c.press}]}><Text style={s.forgetText}>Pair again</Text></Pressable>}
    </View>
    {home&&BUCKETS.map(bucket=>{
      const rows=groups.get(bucket.key)!;
      if(!rows.length&&!bucket.empty)return null;
      const isOpen=open[bucket.key];
      const label=stale&&bucket.key==='working'?'Working when last seen':bucket.label;
      return <View key={bucket.key} style={s.bucket}>
        <Pressable accessibilityRole="button" accessibilityLabel={`${label}, ${rows.length}`} accessibilityState={{expanded:isOpen}}
          onPress={()=>{
            void Haptics.selectionAsync();
            if(!reduced)LayoutAnimation.configureNext(LayoutAnimation.create(220,'easeInEaseOut','opacity'));
            setOpen({...open,[bucket.key]:!isOpen});
          }}
          style={({pressed})=>[s.bucketHead,pressed&&{backgroundColor:c.press}]}>
          <View style={s.chevron}><Chevron color={c.muted} open={isOpen}/></View>
          <Text style={s.bucketLabel}>{label}</Text>
          <Text style={s.bucketCount}>{rows.length}</Text>
        </Pressable>
        {isOpen&&(rows.length?rows.map(row=><Row key={row.id} row={row} s={s} c={c} accent={row.id===firstNeeds} project={manyProjects} stale={stale||row.quiet} state={label}/>)
          :<Text style={s.empty}>{bucket.empty}</Text>)}
      </View>;
    })}
    {!!home?.hidden&&<Text style={[s.empty,{paddingLeft:GUTTER}]}>{home.hidden} more idle {home.hidden===1?'thread is':'threads are'} only on your Mac.</Text>}
  </ScrollView>
  {!!latest&&<View style={[s.askWrap,{paddingBottom:Math.max(insets.bottom,space.s3)}]} pointerEvents="box-none">
    {/* The list goes soft under the bar instead of colliding with it. */}
    <BlurView intensity={40} tint={scheme==='dark'?'systemChromeMaterialDark':'systemChromeMaterialLight'} style={StyleSheet.absoluteFill}/>
    <Pressable accessibilityRole="button" accessibilityLabel="Message Dervo" accessibilityHint="Start or hand off work" onPress={ask} style={({pressed})=>[s.ask,pressed&&{backgroundColor:c.sunk}]}>
      <Text style={s.askText} numberOfLines={1}>Message Dervo</Text>
      <View style={s.askSend}><SendIcon color={c.muted}/></View>
    </Pressable>
  </View>}
  </View>;
}

function Row({row,s,c,accent,project,stale,state}:{row:HomeRow;s:ReturnType<typeof styles>;c:Colors;accent:boolean;project:boolean;stale:boolean;state:string}){
  return <Pressable accessibilityRole="button" accessibilityHint="Opens the thread"
    accessibilityLabel={[row.name,state,project?row.project:'',row.line,short(row.at)].filter(Boolean).join('. ')}
    onPress={()=>router.push({pathname:'/thread/[id]',params:{id:row.id,p:row.projectId}})}
    style={({pressed})=>[s.row,pressed&&{backgroundColor:c.press}]}>
    <View style={s.glyph}><Glyph bucket={row.bucket} accent={accent} stale={stale}/></View>
    <View style={s.rowMain}>
      <View style={s.nameLine}>
        <Text style={[s.name,row.unread>0&&{fontFamily:font.semibold}]} numberOfLines={1}>{row.name}</Text>
        {project&&<Text style={s.project} numberOfLines={1}>{row.project}</Text>}
      </View>
      {!!row.line&&<Text style={[s.line,row.bucket==='needs'&&{color:c.ink2}]} numberOfLines={row.bucket==='needs'?2:1}>{row.line}</Text>}
    </View>
    <Text style={s.time}>{short(row.at)}</Text>
  </Pressable>;
}

/** One left edge for the greeting, the bucket labels and the row text. */
const GUTTER=20;
if(Platform.OS==='android')UIManager.setLayoutAnimationEnabledExperimental?.(true);

const styles=(c:Colors)=>StyleSheet.create({
  screen:{flex:1,backgroundColor:c.canvas},
  askWrap:{position:'absolute',left:0,right:0,bottom:0,paddingHorizontal:space.s3,paddingTop:space.s3},
  ask:{flexDirection:'row',alignItems:'center',gap:10,minHeight:50,paddingLeft:18,paddingRight:7,borderRadius:25,backgroundColor:c.raised,borderWidth:1,borderColor:c.line,
    shadowColor:c.shadow,shadowOpacity:.08,shadowRadius:14,shadowOffset:{width:0,height:6}},
  askText:{flex:1,fontFamily:font.text,fontSize:size.read,color:c.textFaint},
  askSend:{width:36,height:36,borderRadius:18,backgroundColor:c.sunk,alignItems:'center',justifyContent:'center'},
  forget:{alignSelf:'flex-start',minHeight:40,justifyContent:'center',paddingHorizontal:14,marginTop:space.s2,borderRadius:10,borderWidth:1,borderColor:c.lineStrong},
  forgetText:{fontFamily:font.semibold,fontSize:size.small,color:c.ink},
  top:{flexDirection:'row',paddingHorizontal:space.s2,height:44,alignItems:'center'},
  iconButton:{width:44,height:44,borderRadius:22,alignItems:'center',justifyContent:'center'},
  hello:{paddingHorizontal:GUTTER,paddingTop:0,paddingBottom:space.s5,gap:6},
  greeting:{fontFamily:font.display,fontSize:size.h1,lineHeight:size.h1*1.1,color:c.ink,letterSpacing:-0.3},
  sentence:{fontFamily:font.text,fontSize:size.read,lineHeight:size.read*1.5,color:c.muted},
  asof:{flexDirection:'row',alignItems:'flex-start',gap:8,marginTop:2},
  asofDot:{height:size.small*1.4,width:11,alignItems:'center',justifyContent:'center'},
  asofText:{fontFamily:font.text,fontSize:size.small,lineHeight:size.small*1.4,color:c.muted,flexShrink:1},
  bucket:{paddingHorizontal:GUTTER-12,marginTop:space.s4},
  // The chevron sits in the row glyphs' column and the label starts where row names do.
  bucketHead:{flexDirection:'row',alignItems:'center',gap:12,minHeight:44,paddingHorizontal:12,borderRadius:10,marginBottom:2},
  chevron:{width:16,alignItems:'center'},
  bucketLabel:{fontFamily:font.semibold,fontSize:size.small,color:c.ink},
  bucketCount:{fontFamily:font.text,fontSize:size.small,color:c.muted,fontVariant:['tabular-nums']},
  empty:{fontFamily:font.text,fontSize:size.small,color:c.textFaint,paddingVertical:10,paddingLeft:40},
  row:{flexDirection:'row',alignItems:'flex-start',gap:12,paddingVertical:10,paddingLeft:12,paddingRight:12,borderRadius:12,minHeight:56},
  glyph:{paddingTop:2.5},
  rowMain:{flex:1,minWidth:0,gap:2},
  nameLine:{flexDirection:'row',alignItems:'baseline',gap:8},
  name:{fontFamily:font.medium,fontSize:size.body,color:c.ink,flexShrink:1},
  project:{fontFamily:font.text,fontSize:size.caption,color:c.textFaint,flexShrink:0,maxWidth:'40%'},
  line:{fontFamily:font.text,fontSize:size.small,lineHeight:size.small*1.4,color:c.muted},
  time:{fontFamily:font.text,fontSize:size.caption+0.5,color:c.textFaint,fontVariant:['tabular-nums'],paddingTop:2,textAlign:'right'},
});
