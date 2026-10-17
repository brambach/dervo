import {t} from '@/i18n';

/** Opens Settings on Connected accounts from a screen the shell didn't give a route of its own (Home's "Connect Claude",
 * the one-account usage-limit row's "Add an account"). The click goes to the sidebar, the way Home hands Sign in to the
 * account banner: a usage line opens the usage popover, whose foot has Connected accounts. With no accounts listed the
 * shell opens Connected accounts straight from the usage line. Returns false when there's no sidebar to hand it to. */
export function openConnectedAccounts():boolean{
 const line=document.querySelector<HTMLElement>('[data-testid="dervo-sidebar-usage"] button');
 if(!line)return false;
 line.click();
 let tries=0;
 const manage=()=>{
  // The foot's button is marked data-manage; its words are in the window's language.
  const button=[...document.querySelectorAll<HTMLElement>('.dv-pop-foot button')].find(node=>node.hasAttribute('data-manage')||[t('Connected accounts'),'Connected accounts'].includes(node.textContent?.trim()??''));
  if(button){button.click();return;}
  if(++tries<20)setTimeout(manage,25);
 };
 requestAnimationFrame(manage);
 return true;
}
