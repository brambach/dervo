import {SidebarRail,useSidebar} from '@/shared/ui/sidebar';

/** The shared sidebar owns persistence and pointer dragging on every app screen. */
export function SidebarResize(){
 const {sidebarWidth,setSidebarWidth}=useSidebar();
 return <SidebarRail className="dv-sidebar-resize" tabIndex={0} role="separator" aria-orientation="vertical"
  aria-valuemin={220} aria-valuemax={420} aria-valuenow={sidebarWidth}
  title="Drag to resize. Double-click to reset. Arrow keys adjust width."
  onDoubleClick={()=>setSidebarWidth(300)}
  onKeyDown={event=>{
   if(event.key==='ArrowLeft'||event.key==='ArrowRight'){
    event.preventDefault();setSidebarWidth(sidebarWidth+(event.key==='ArrowRight'?1:-1)*(event.shiftKey?40:10));
   }else if(event.key==='Home'){event.preventDefault();setSidebarWidth(220);}
   else if(event.key==='End'){event.preventDefault();setSidebarWidth(420);}
  }}/>;
}
