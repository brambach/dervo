import {useEffect,useRef} from 'react';
import {Animated,Easing,View} from 'react-native';
import {useColors} from './theme';
import {useReducedMotion} from './motion';

/** The "live" dot from the mockup's as-of line: green with a slow ring when the Mac is connected, a still faint dot
 * when it isn't. Nothing pulses unless it's actually live. */
export function Pulse({live}:{live:boolean}){
  const c=useColors();
  const ring=useRef(new Animated.Value(0)).current;
  const reduced=useReducedMotion();
  useEffect(()=>{
    if(!live||reduced)return;
    const loop=Animated.loop(Animated.timing(ring,{toValue:1,duration:2400,easing:Easing.bezier(.2,.7,.2,1),useNativeDriver:true}));
    loop.start();
    return ()=>{loop.stop();ring.setValue(0);};
  },[live,reduced,ring]);
  const color=live?c.live:c.faint;
  return <View style={{width:6,height:6,alignItems:'center',justifyContent:'center'}}>
    {live&&!reduced&&<Animated.View style={{position:'absolute',width:6,height:6,borderRadius:3,backgroundColor:color,
      opacity:ring.interpolate({inputRange:[0,.7,1],outputRange:[.5,0,0]}),transform:[{scale:ring.interpolate({inputRange:[0,1],outputRange:[1,3]})}]}}/>}
    <View style={{width:6,height:6,borderRadius:3,backgroundColor:color}}/>
  </View>;
}
