import {useEffect} from 'react';
import {useQueryClient} from '@tanstack/react-query';
import {watchConversationForeground} from './conversationForeground';

/** The workspace owns one account refresh when this native window returns. */
export function useAccountForeground(watch=watchConversationForeground){
  const client=useQueryClient();
  useEffect(()=>{
    // The account query already reads on mount. Only a return needs another read.
    let focused=true;
    return watch(next=>{
      const returned=next&&!focused;
      focused=next;
      if(returned)void client.refetchQueries({queryKey:['dervo-account'],exact:true},{cancelRefetch:false});
    });
  },[client,watch]);
}
