/** Times the way Home on the Mac says them (frontend/src/local/homeTime.ts): "9:14 PM" today, "yesterday", a weekday,
 * then a date. Short forms for row edges. */
const DAY=86400;
const clock=(date:Date)=>date.toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'});

export function short(at:number|null,now=Date.now()/1000):string{
  if(!at)return '';
  const then=new Date(at*1000),today=new Date(now*1000);
  const midnight=(date:Date)=>new Date(date.getFullYear(),date.getMonth(),date.getDate()).getTime()/1000;
  const days=Math.round((midnight(today)-midnight(then))/DAY);
  if(days<=0)return clock(then);
  if(days===1)return 'Yesterday';
  if(days<7)return then.toLocaleDateString(undefined,{weekday:'short'});
  return then.toLocaleDateString(undefined,{day:'numeric',month:'short'});
}

/** "just now", "4 min ago", "2 h ago", "3 days ago": for "Mac last seen". */
export function ago(atMs:number,nowMs=Date.now()):string{
  const minutes=Math.round((nowMs-atMs)/60_000);
  if(minutes<1)return 'a moment ago';
  if(minutes<60)return `${minutes} min ago`;
  const hours=Math.round(minutes/60);
  return hours<48?`${hours} h ago`:`${Math.round(hours/24)} days ago`;
}

export function greeting(now=new Date()):string{
  const hour=now.getHours();
  return hour<5?'Good evening':hour<12?'Good morning':hour<18?'Good afternoon':'Good evening';
}
