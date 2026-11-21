import {useEffect,useSyncExternalStore} from 'react'
import {sharedPostprocessDisplay} from '../postprocess/sharedDisplay'
import {createThreePostprocessAdapter} from './threePostprocessAdapter'
import {createOvPostprocessAdapter} from './ovPostprocessAdapter'
import {ovSession,subscribeOvSession} from './ovSession'
/** Renderer changes replace only the carrier and replay the accepted common frame. */
export function useSharedPostprocessRenderer() {
  const session=useSyncExternalStore(subscribeOvSession,ovSession)
  useEffect(()=>{
    if(session.mode==='three') {sharedPostprocessDisplay.activate(createThreePostprocessAdapter());return}
    if(!session.ready || !session.client || session.degraded) {
      sharedPostprocessDisplay.activate({kind:'ov',render:()=>({status:'pending',message:'Waiting for an acknowledged OV display connection'}),clear(){},dispose(){}})
      return
    }
    const client=session.client
    const adapter=createOvPostprocessAdapter({client,onInteraction:event=>sharedPostprocessDisplay.interact(event),
      sceneIdle:()=>ovSession().sceneIdle?.()??Promise.resolve(),
      scene:()=>{
        const current=ovSession()
        return current.client===client&&current.ready&&!current.degraded&&current.projectUid&&current.projectContentSha256&&current.sceneRevision!==null
          ?{projectUid:current.projectUid,projectHash:current.projectContentSha256,sceneRevision:current.sceneRevision}:null
      },
    })
    sharedPostprocessDisplay.activate(adapter)
    return ()=>adapter.dispose()
  },[session.mode,session.client,session.ready,session.degraded,session.projectUid,session.projectContentSha256,session.sceneRevision])
}
