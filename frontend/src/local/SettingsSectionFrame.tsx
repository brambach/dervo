import type {ReactNode,RefObject} from 'react';
import {Dialog,DialogContent,DialogHeader,DialogTitle,DialogDescription} from '@/shared/ui/dialog';

type Props={embedded:boolean;open:boolean;onOpenChange:(open:boolean)=>void;title:string;description:string;titleRef:RefObject<HTMLHeadingElement|null>;children:ReactNode};

/** One settings form and host contract, whether opened directly or in project settings. */
export function SettingsSectionFrame({embedded,open,onOpenChange,title,description,titleRef,children}:Props){
 if(embedded)return open?<section className="dervo-settings-form" aria-label={title}><header className="shrink-0 space-y-2"><h2 ref={titleRef} tabIndex={-1}>{title}</h2><p>{description}</p></header>{children}</section>:null;
 return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="flex max-h-[85vh] flex-col overflow-hidden sm:max-w-2xl" onOpenAutoFocus={event=>{event.preventDefault();titleRef.current?.focus();}}><DialogHeader className="shrink-0"><DialogTitle ref={titleRef} tabIndex={-1} className="outline-none">{title}</DialogTitle><DialogDescription>{description}</DialogDescription></DialogHeader>{children}</DialogContent></Dialog>;
}
