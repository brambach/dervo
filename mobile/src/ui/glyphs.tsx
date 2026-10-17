import {useEffect,useRef} from 'react';
import {Animated,Easing} from 'react-native';
import Svg,{Circle,Ellipse,G,Path,Rect} from 'react-native-svg';
import type {Bucket} from '../../../companion/protocol/messages.ts';
import {useColors} from './theme';
import {useReducedMotion} from './motion';

/** The desktop's status glyphs (the mockup's G set): a spinning arc for working, a ringed dot for needs you (rust for the
 * first one), a check for done, a ring for idle, a dashed ring for "lost sight". Same shapes, so a thread reads the same
 * on both screens. */
export function Glyph({bucket,accent=false,size=16,stale=false}:{bucket:Bucket;accent?:boolean;size?:number;stale?:boolean}){
  const c=useColors();
  // While the Mac is away a run that was working is only "last known": the arc stops and goes faint, so nothing spins
  // from memory.
  if(bucket==='working')return stale?<Svg width={size} height={size} viewBox="0 0 16 16" accessibilityLabel="Working when last seen"><Circle cx={8} cy={8} r={6} fill="none" stroke={c.faint} strokeOpacity={0.35} strokeWidth={1.8}/><Path d="M8 2a6 6 0 016 6" fill="none" stroke={c.faint} strokeWidth={1.8} strokeLinecap="round"/></Svg>:<Spinner size={size} color={c.live}/>;
  const props={width:size,height:size,viewBox:'0 0 16 16'};
  switch(bucket){
    case 'needs':
      // Every thread waiting on you is rust; the first one at full strength.
      return <Svg {...props} accessibilityLabel="Needs you" opacity={accent?1:0.7}><Circle cx={8} cy={8} r={6.5} fill="none" stroke={c.rust} strokeWidth={1.6}/><Circle cx={8} cy={8} r={3} fill={c.rust}/></Svg>;
    case 'finished':
      return <Svg {...props} accessibilityLabel="Finished"><Circle cx={8} cy={8} r={7} fill={c.sage} opacity={0.16}/><Path d="M5 8.2l2 2 4-4.4" fill="none" stroke={c.sage} strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round"/></Svg>;
    case 'unknown':
      return <Svg {...props} accessibilityLabel="Unknown"><Circle cx={8} cy={8} r={6.5} fill="none" stroke={c.faint} strokeWidth={1.5} strokeDasharray="2.4 2.4"/></Svg>;
    default:
      return <Svg {...props} accessibilityLabel="Idle"><Circle cx={8} cy={8} r={5.5} fill="none" stroke={c.faint} strokeWidth={1.5}/></Svg>;
  }
}

function Spinner({size,color}:{size:number;color:string}){
  const turn=useRef(new Animated.Value(0)).current;
  const reduced=useReducedMotion();
  useEffect(()=>{
    // With Reduce Motion the arc holds still; its green still says it's running.
    if(reduced)return;
    const loop=Animated.loop(Animated.timing(turn,{toValue:1,duration:1100,easing:Easing.linear,useNativeDriver:true}));
    loop.start();
    return ()=>{loop.stop();turn.setValue(0);};
  },[reduced,turn]);
  const rotate=turn.interpolate({inputRange:[0,1],outputRange:['0deg','360deg']});
  return <Animated.View style={{width:size,height:size,transform:[{rotate}]}} accessibilityLabel="Working">
    <Svg width={size} height={size} viewBox="0 0 16 16"><Circle cx={8} cy={8} r={6} fill="none" stroke={color} strokeOpacity={0.2} strokeWidth={1.8}/><Path d="M8 2a6 6 0 016 6" fill="none" stroke={color} strokeWidth={1.8} strokeLinecap="round"/></Svg>
  </Animated.View>;
}

/** The Dervo pebble, the app's one mark (icons/dervo.svg, from frontend/src/local/pebble.ts): pine with a cream face.
 * Brand moments only: pairing, and nowhere in daily use. */
export function Mark({size=44}:{size?:number}){
  const c=useColors();
  return <Svg width={size} height={size} viewBox="0 0 98 98" accessibilityLabel="Dervo">
    <Path fill={c.pine} d="M4.3 45.3C4.7 43.0 5.3 41.1 5.9 39.1C6.4 37.1 6.9 35.3 7.7 33.1C8.5 31.0 9.4 28.4 10.4 26.5C11.4 24.5 12.3 23.0 13.8 21.4C15.3 19.8 17.5 18.0 19.2 16.8C21.0 15.5 22.7 14.7 24.3 13.7C26.0 12.7 27.5 11.8 29.3 10.8C31.2 9.8 33.4 8.5 35.3 7.7C37.3 6.9 38.9 6.4 40.9 6.0C43.0 5.6 45.7 5.3 47.8 5.3C49.9 5.2 51.7 5.4 53.7 5.6C55.6 5.9 57.4 6.3 59.4 6.9C61.4 7.6 63.8 8.7 65.8 9.6C67.7 10.5 69.1 11.3 71.0 12.3C73.0 13.3 75.5 14.5 77.4 15.7C79.3 16.9 81.0 18.0 82.5 19.5C84.0 21.0 85.3 22.6 86.4 24.6C87.6 26.6 88.7 29.3 89.6 31.5C90.5 33.7 91.2 35.4 91.8 37.7C92.4 40.0 93.1 42.9 93.3 45.3C93.5 47.7 93.3 49.8 93.0 52.0C92.6 54.2 92.0 56.2 91.2 58.5C90.5 60.8 89.3 63.5 88.3 65.7C87.3 67.9 86.5 69.6 85.2 71.7C83.9 73.7 82.2 76.2 80.5 78.0C78.8 79.8 77.1 81.0 75.2 82.3C73.3 83.5 71.4 84.4 69.2 85.4C67.0 86.5 64.3 87.6 62.1 88.5C59.8 89.4 58.1 90.2 55.7 90.8C53.3 91.4 50.2 92.0 47.8 92.1C45.3 92.2 43.2 91.9 40.9 91.5C38.7 91.2 36.6 90.6 34.2 90.0C31.8 89.4 28.8 88.6 26.4 87.8C24.0 86.9 22.0 86.2 19.9 84.8C17.9 83.4 15.5 81.2 13.8 79.3C12.2 77.3 11.2 75.3 10.1 73.2C9.0 71.2 8.2 69.3 7.3 67.0C6.3 64.7 5.3 61.9 4.7 59.5C4.1 57.2 3.7 55.3 3.7 53.0C3.6 50.6 3.9 47.6 4.3 45.3Z"/>
    <G transform="rotate(-12 49 50)" fill={c.pineInk}>
      <Ellipse cx={35} cy={39} rx={4} ry={5}/><Ellipse cx={61} cy={37} rx={4} ry={5}/>
      <Path d="M30 56Q48 79 68 54" fill="none" stroke={c.pineInk} strokeWidth={6} strokeLinecap="round"/>
    </G>
  </Svg>;
}

export function SendIcon({color}:{color:string}){
  return <Svg width={15} height={15} viewBox="0 0 16 16"><Path d="M8 13V3M3.5 7.5L8 3l4.5 4.5" fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"/></Svg>;
}
export function StopIcon({color}:{color:string}){
  return <Svg width={13} height={13} viewBox="0 0 16 16"><Rect x={3} y={3} width={10} height={10} rx={2} fill={color}/></Svg>;
}
/** A small right chevron for rows that open something. */
export function NextIcon({color}:{color:string}){
  return <Svg width={10} height={10} viewBox="0 0 16 16"><Path d="M6 3.5L10.5 8 6 12.5" fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"/></Svg>;
}
export function BackIcon({color}:{color:string}){
  return <Svg width={20} height={20} viewBox="0 0 16 16"><Path d="M9.5 3.5L5 8l4.5 4.5" fill="none" stroke={color} strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round"/></Svg>;
}
export function MoreIcon({color}:{color:string}){
  return <Svg width={20} height={20} viewBox="0 0 16 16"><Circle cx={3.5} cy={8} r={1.25} fill={color}/><Circle cx={8} cy={8} r={1.25} fill={color}/><Circle cx={12.5} cy={8} r={1.25} fill={color}/></Svg>;
}
/** Settings: two sliders, drawn on the same 1.6 stroke as the back chevron. */
export function GearIcon({color}:{color:string}){
  return <Svg width={20} height={20} viewBox="0 0 20 20">
    <Path d="M3 6.5h8M15 6.5h2M3 13.5h2M9 13.5h8" stroke={color} strokeWidth={1.6} strokeLinecap="round"/>
    <Circle cx={13} cy={6.5} r={2} fill="none" stroke={color} strokeWidth={1.6}/>
    <Circle cx={7} cy={13.5} r={2} fill="none" stroke={color} strokeWidth={1.6}/>
  </Svg>;
}
/** Turns a quarter as its section opens and closes, on the same curve as the section. */
export function Chevron({color,open}:{color:string;open:boolean}){
  const turn=useRef(new Animated.Value(open?1:0)).current;
  const reduced=useReducedMotion();
  useEffect(()=>{
    if(reduced){turn.setValue(open?1:0);return;}
    Animated.timing(turn,{toValue:open?1:0,duration:220,easing:Easing.bezier(.2,.7,.2,1),useNativeDriver:true}).start();
  },[open,reduced,turn]);
  const rotate=turn.interpolate({inputRange:[0,1],outputRange:['-90deg','0deg']});
  return <Animated.View style={{width:11,height:11,transform:[{rotate}]}}>
    <Svg width={11} height={11} viewBox="0 0 16 16"><Path d="M4 6l4 4 4-4" fill="none" stroke={color} strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round"/></Svg>
  </Animated.View>;
}
