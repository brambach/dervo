import {useEffect,useState} from 'react';
import {AccessibilityInfo} from 'react-native';

/** Whether Reduce Motion is on. Read once, then followed as it changes. Everything that loops checks it. */
export function useReducedMotion(){
  const [reduced,setReduced]=useState(false);
  useEffect(()=>{
    let live=true;
    AccessibilityInfo.isReduceMotionEnabled().then(value=>{if(live)setReduced(value);}).catch(()=>{});
    const sub=AccessibilityInfo.addEventListener('reduceMotionChanged',setReduced);
    return ()=>{live=false;sub.remove();};
  },[]);
  return reduced;
}
