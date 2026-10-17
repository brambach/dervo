import {NextUp} from './NextUp';

/** Dervo reports went away in beta.16 (b16/lifecycle): what testers send shows as Tester reports on Next up, in the one
 * project chosen for them. The sidebar no longer opens this; ConnectedApp still names it, so it shows Next up until
 * ConnectedApp drops it (asked of b16/thread). Then this file goes. */
export function DervoFeedbackList({projectId,profileId,onClose}:{projectId:string;profileId:string;onClose:()=>void;onOpenSource?:(projectId:string,itemId:string)=>void}){
 return <NextUp open projectId={projectId} profileId={profileId} onClose={onClose}/>;
}
