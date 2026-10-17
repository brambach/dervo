import {useEffect,useMemo,useState} from 'react';
import {Alert,AppState,Linking,Platform,Pressable,ScrollView,StyleSheet,Text,View} from 'react-native';
import {router} from 'expo-router';
import {useSafeAreaInsets} from 'react-native-safe-area-context';
import {forget,useLink} from '../store';
import {pushStatus,registerPush,type PushStatus} from '../push';
import {BackIcon} from '../ui/glyphs';
import {Pulse} from '../ui/Pulse';
import {useNow} from '../ui/Presence';
import {ago} from '../ui/time';
import {font,radius,size,space,useColors,type Colors} from '../ui/theme';

const NOTICES:Record<PushStatus,string>={
  on:'On. A notice says only "Something needs you."',
  off:'Off in iOS Settings.',
  ask:'Not turned on yet.',
  unavailable:'Not available in this build.',
};

export default function Settings(){
  const c=useColors();
  const s=useMemo(()=>styles(c),[c]);
  const insets=useSafeAreaInsets();
  const {link,state,live}=useLink();
  useNow(15_000);
  const [push,setPush]=useState<PushStatus|null>(null);
  useEffect(()=>{
    const read=()=>void pushStatus().then(setPush);
    read();
    // Coming back from iOS Settings, read it again.
    const sub=AppState.addEventListener('change',next=>{if(next==='active')read();});
    return ()=>sub.remove();
  },[]);
  const mac=state?.home?.mac??state?.offer.name??'your Mac';
  const connected=!!live?.connected;
  const online=connected&&!!live?.macOnline;
  const status=!connected?'Can\'t reach Dervo\'s relay from this phone.':online?'Connected':`Asleep or offline, last seen ${live?.macSeen?ago(live.macSeen):'a while ago'}`;
  function remove(){
    const go=()=>{forget();router.replace('/pair');};
    if(Platform.OS==='web'){go();return;}
    // Offline, the removal waits on this phone until it can reach the Mac.
    const body=connected?`It stops seeing ${mac}. To use it again, scan a new code on your Mac.`
      :`This phone can't reach ${mac} right now. It stops showing anything at once, and tells ${mac} the next time it has a connection.`;
    Alert.alert('Remove this phone?',body,[{text:'Cancel',style:'cancel'},{text:'Remove',style:'destructive',onPress:go}]);
  }
  function notices(){
    if(push==='ask'&&link)void registerPush(token=>link.setPushToken(token),true).then(()=>pushStatus().then(setPush));
    else if(push==='off')void Linking.openSettings();
  }
  return <ScrollView style={s.screen} contentContainerStyle={{paddingTop:insets.top+4,paddingBottom:insets.bottom+space.s5}}>
    <View style={s.head}>
      <Pressable accessibilityRole="button" accessibilityLabel="Back" hitSlop={10} onPress={()=>router.back()} style={({pressed})=>[s.back,pressed&&{backgroundColor:c.press}]}><BackIcon color={c.ink}/></Pressable>
    </View>
    <Text style={s.title} accessibilityRole="header">Settings</Text>
    <View style={s.body}>
      <Text style={s.groupHead}>Your Mac</Text>
      <View style={s.box}>
        <View style={s.line}>
          <Text style={s.lineLabel} numberOfLines={2}>{mac}</Text>
          <View style={s.status}><Pulse live={online}/><Text style={s.lineValue}>{status}</Text></View>
        </View>
      </View>
      <Text style={s.groupHead}>Notifications</Text>
      <Pressable accessibilityRole={push==='ask'||push==='off'?'button':'text'} disabled={push!=='ask'&&push!=='off'} onPress={notices}
        style={({pressed})=>[s.box,pressed&&{backgroundColor:c.press}]}>
        <View style={s.line}>
          <Text style={s.lineValue}>{push?NOTICES[push]:' '}</Text>
          {push==='ask'&&<Text style={s.action}>Turn on</Text>}
          {push==='off'&&<Text style={s.action}>Open iOS Settings</Text>}
        </View>
      </Pressable>
      <Text style={s.groupHead}>How this works</Text>
      <View style={s.box}>
        <Text style={s.about}>Your projects, threads and memory stay on your Mac. Your Mac sends this phone a sealed copy of Home, and what you send goes back to it to run.</Text>
        <Text style={[s.about,{borderTopWidth:StyleSheet.hairlineWidth,borderTopColor:c.line}]}>The relay in between can't read anything. It holds sealed messages for up to a week while a device is away, and wakes this phone with a notice that says only "Something needs you." or "Something finished."</Text>
      </View>
      <Pressable accessibilityRole="button" onPress={remove} style={({pressed})=>[s.box,s.danger,pressed&&{backgroundColor:c.press}]}>
        <Text style={s.dangerText}>Remove this phone</Text>
      </Pressable>
    </View>
  </ScrollView>;
}

const styles=(c:Colors)=>StyleSheet.create({
  screen:{flex:1,backgroundColor:c.paper},
  head:{flexDirection:'row',alignItems:'center',paddingHorizontal:space.s2,height:44},
  back:{width:44,height:44,borderRadius:22,alignItems:'center',justifyContent:'center'},
  title:{fontFamily:font.display,fontSize:size.h1,lineHeight:size.h1*1.1,color:c.ink,letterSpacing:-0.3,paddingHorizontal:20,marginTop:space.s1},
  body:{paddingHorizontal:space.s4,gap:space.s2},
  groupHead:{fontFamily:font.semibold,fontSize:size.caption,color:c.muted,marginTop:space.s4,marginLeft:4},
  box:{backgroundColor:c.raised,borderWidth:1,borderColor:c.line,borderRadius:radius.card,overflow:'hidden'},
  line:{gap:4,paddingVertical:12,paddingHorizontal:space.s4},
  lineLabel:{fontFamily:font.medium,fontSize:size.title,color:c.ink},
  status:{flexDirection:'row',alignItems:'center',gap:8},
  lineValue:{flexShrink:1,fontFamily:font.text,fontSize:size.small,lineHeight:size.small*1.4,color:c.muted},
  action:{fontFamily:font.semibold,fontSize:size.small,color:c.ink},
  about:{fontFamily:font.text,fontSize:size.small+0.5,lineHeight:21,color:c.ink2,paddingVertical:12,paddingHorizontal:space.s4},
  danger:{marginTop:space.s5,minHeight:48,justifyContent:'center',alignItems:'center'},
  dangerText:{fontFamily:font.semibold,fontSize:size.body,color:c.rust},
});
