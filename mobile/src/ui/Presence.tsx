import {useEffect,useRef,useState} from 'react';
import {Animated,Easing,Platform,StyleSheet,Text,View,type TextStyle} from 'react-native';
import MaskedView from '@react-native-masked-view/masked-view';
import {LinearGradient} from 'expo-linear-gradient';
import {useColors} from './theme';
import {useReducedMotion} from './motion';

/** Dervo's presence while it answers, the same as the Mac's (frontend/src/local/Presence.tsx): one honest step where the
 * reply will land, its words moving on with the clock, under a soft shimmer. With Reduce Motion on, nothing moves. */

/** When "Thinking it through" becomes "Still thinking", with the time. The Mac's value. */
const STILL_AFTER=20;

/** The step's words for a reply the Mac started thinking at ``since`` (seconds). The phone can't know more than that. */
export function thinkingStep(since:number|null,now=Date.now()/1000):{text:string;waited:string|null}{
  const elapsed=since?Math.max(0,now-since):0;
  if(elapsed<STILL_AFTER)return {text:'Thinking it through',waited:null};
  return {text:'Still thinking',waited:elapsed<60?`${Math.floor(elapsed/10)*10} sec`:`${Math.floor(elapsed/60)} min`};
}

/** Re-renders every ``ms``, for anything that reads the clock (how current Home is, whether a send is overdue). */
export function useNow(ms:number){
  const [,tick]=useState(0);
  useEffect(()=>{const timer=setInterval(()=>tick(n=>n+1),ms);return ()=>clearInterval(timer);},[ms]);
  return Date.now();
}

/** Re-renders every second while ``on``, so a step that reads the clock moves on. */
export function useTick(on:boolean){
  const [,tick]=useState(0);
  useEffect(()=>{
    if(!on)return;
    const timer=setInterval(()=>tick(n=>n+1),1000);
    return ()=>clearInterval(timer);
  },[on]);
}

/** A lighter band sweeping across one line of real text, then a rest: the text is laid out once (kerning, ligatures,
 * truncation and Dynamic Type intact), a brighter copy sits on top, and a soft gradient mask slides across it on the
 * native driver. At rest it reads at ``textFaint`` (4.5:1). With Reduce Motion it's just that text. */
export function Shimmer({text,style,active=true}:{text:string;style:TextStyle;active?:boolean}){
  const c=useColors();
  const reduced=useReducedMotion();
  const sweep=useRef(new Animated.Value(0)).current;
  const [width,setWidth]=useState(0);
  const on=active&&!reduced&&width>0;
  useEffect(()=>{
    if(!on)return;
    const loop=Animated.loop(Animated.sequence([
      Animated.timing(sweep,{toValue:1,duration:1500,easing:Easing.inOut(Easing.quad),useNativeDriver:true}),
      Animated.delay(350),
    ]),{resetBeforeIteration:true});
    loop.start();
    return ()=>{loop.stop();sweep.setValue(0);};
  },[on,sweep]);
  const base=<Text style={[style,{color:c.textFaint}]} numberOfLines={1} onLayout={event=>setWidth(event.nativeEvent.layout.width)}>{text}</Text>;
  // The web preview has no native mask, so it shows the still line.
  if(!active||reduced||Platform.OS==='web')return base;
  const band=Math.max(48,width*0.3);
  const translateX=sweep.interpolate({inputRange:[0,1],outputRange:[-band,width]});
  return <View accessible accessibilityLabel={text}>
    {base}
    {on&&<MaskedView style={StyleSheet.absoluteFill} pointerEvents="none"
      maskElement={<Animated.View style={{width:band,height:'100%',transform:[{translateX}]}}>
        <LinearGradient colors={['transparent','#000','transparent']} start={{x:0,y:.5}} end={{x:1,y:.5}} style={StyleSheet.absoluteFill}/>
      </Animated.View>}>
      <Text style={[style,{color:c.ink2}]} numberOfLines={1}>{text}</Text>
    </MaskedView>}
  </View>;
}
