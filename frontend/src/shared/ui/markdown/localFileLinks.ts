import {createContext} from 'react';

export const LocalFileLinks=createContext<((path:string)=>Promise<unknown>)|null>(null);

/** Only file paths. Web links and in-page anchors keep their existing behaviour. */
export function localFilePath(href:string|undefined):string|null{
 if(!href||href.startsWith('#')||href.startsWith('//'))return null;
 let path=href;
 try{
  if(/^file:/i.test(href)){
   const url=new URL(href);
   if(url.hostname&&url.hostname!=='localhost')return null;
   path=url.pathname;
  }else if(/^[a-z][a-z0-9+.-]*:/i.test(href)&&!/^\.?[^/:]+\.[^/:]+:\d+(?::\d+)?$/.test(href))return null;
  path=decodeURIComponent(path).replace(/#L\d+(?:C\d+)?(?:-L?\d+)?$/,'').replace(/:\d+(?::\d+)?$/,'');
 }catch{return null;}
 return path&&!path.startsWith('//')&&!/[\x00-\x1f]/.test(path)?path:null;
}
