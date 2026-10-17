import {useEffect} from 'react';
import {Stack,router} from 'expo-router';
import * as Notifications from 'expo-notifications';
import {Platform} from 'react-native';
import {StatusBar} from 'expo-status-bar';
import {useFonts} from 'expo-font';
import {InstrumentSans_400Regular,InstrumentSans_500Medium,InstrumentSans_600SemiBold} from '@expo-google-fonts/instrument-sans';
import {InstrumentSerif_400Regular} from '@expo-google-fonts/instrument-serif';
import {SafeAreaProvider} from 'react-native-safe-area-context';
import {load,refresh,useLink} from '../store';
import {registerPush} from '../push';
import {useColors} from '../ui/theme';

export default function Root(){
  const c=useColors();
  const [fonts]=useFonts({InstrumentSans_400Regular,InstrumentSans_500Medium,InstrumentSans_600SemiBold,InstrumentSerif_400Regular});
  const {loaded,state,link}=useLink();
  useEffect(()=>{void load();},[]);
  const paired=!!state?.paired;
  useEffect(()=>{if(paired&&link)void registerPush(token=>link.setPushToken(token));},[paired,link]);
  // A notice says only "Something needs you", so tapping it opens Home, freshly asked for, where that thread is on top.
  const ready=!!(fonts&&loaded);
  useEffect(()=>{
    if(!ready||Platform.OS==='web')return;
    const go=()=>{refresh();router.dismissTo('/');};
    void Notifications.getLastNotificationResponseAsync().then(response=>{if(response){go();void Notifications.clearLastNotificationResponseAsync?.();}}).catch(()=>{});
    const sub=Notifications.addNotificationResponseReceivedListener(go);
    return ()=>sub.remove();
  },[ready]);
  return <SafeAreaProvider>
    <StatusBar style="auto"/>
    {fonts&&loaded?<Stack screenOptions={{headerShown:false,contentStyle:{backgroundColor:c.canvas},animation:'default'}}/>:null}
  </SafeAreaProvider>;
}
