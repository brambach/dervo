import {Platform} from 'react-native';
import Constants from 'expo-constants';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';

/** Content-free notices: the relay only ever sends "Something needs you." The app shows it as is while in the
 * background and opens Home when tapped. Needs an EAS project id (app.json extra.eas.projectId) and a real device;
 * without either it does nothing, and Home still shows everything once opened. */
Notifications.setNotificationHandler({
  handleNotification:async()=>({shouldPlaySound:false,shouldSetBadge:false,shouldShowBanner:false,shouldShowList:true}),
});

export type PushStatus='on'|'off'|'ask'|'unavailable';

/** What Settings says about notices: on, off in iOS Settings, not asked yet, or not possible on this build. */
export async function pushStatus():Promise<PushStatus>{
  try{
    if(Platform.OS==='web'||!Device.isDevice)return 'unavailable';
    const projectId=Constants?.expoConfig?.extra?.eas?.projectId??Constants?.easConfig?.projectId;
    if(!projectId)return 'unavailable';
    const {status,canAskAgain}=await Notifications.getPermissionsAsync();
    return status==='granted'?'on':canAskAgain?'ask':'off';
  }catch{return 'unavailable';}
}

/** Stop notices on this phone, for when it's removed. */
export async function stopPush(){try{if(Platform.OS!=='web')await Notifications.unregisterForNotificationsAsync();}catch{/* nothing to stop */}}

/** ``ask`` shows iOS's permission prompt if it hasn't been answered. Without it, this only picks up a token you already
 * allowed, so the prompt comes once, on the paired screen, where it says what notices hold. */
export async function registerPush(use:(token:string|null)=>void,ask=false){
  try{
    if(Platform.OS==='web'||!Device.isDevice)return;
    const projectId=Constants?.expoConfig?.extra?.eas?.projectId??Constants?.easConfig?.projectId;
    if(!projectId)return;
    let {status}=await Notifications.getPermissionsAsync();
    if(status!=='granted'&&ask)({status}=await Notifications.requestPermissionsAsync());
    if(status!=='granted'){use(null);return;}
    use((await Notifications.getExpoPushTokenAsync({projectId})).data);
  }catch{/* No notices; Home still has it all. */}
}
