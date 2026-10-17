import {formatDate,t} from '@/i18n';
/** How Home and its brief say times: the way you'd say them out loud, never a raw date. */
const DAY=86400;
/** A time as you'd say it: "9:14 PM" today, "yesterday 11:40 PM", "Monday 3:02 PM", "Mon 7 Sep". */
export function spoken(at:number,now=Date.now()/1000):string{
 const then=new Date(at*1000),today=new Date(now*1000);
 const midnight=(date:Date)=>new Date(date.getFullYear(),date.getMonth(),date.getDate()).getTime()/1000;
 const days=Math.round((midnight(today)-midnight(then))/DAY);
 const time=formatDate(then,{hour:'numeric',minute:'2-digit'});
 if(days<=0)return time;
 if(days===1)return t('yesterday {time}',{time});
 if(days<7)return `${formatDate(then,{weekday:'long'})} ${time}`;
 return formatDate(then,{weekday:'short',day:'numeric',month:'short'});
}

/** How long something has been going: "38 min", "2 h", "3 days". */
export function lasted(since:number,now=Date.now()/1000):string{
 const minutes=Math.max(1,Math.round((now-since)/60));
 if(minutes<60)return t('{minutes} min',{minutes});
 const hours=Math.round(minutes/60);
 return hours<48?t('{hours} h',{hours}):t('{days} days',{days:Math.round(hours/24)});
}

