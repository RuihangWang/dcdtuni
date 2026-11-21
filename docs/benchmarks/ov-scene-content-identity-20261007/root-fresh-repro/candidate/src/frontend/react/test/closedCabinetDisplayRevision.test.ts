import {afterEach,expect,it,vi} from 'vitest'
import {displaySceneKey,leafDisplayRevision,setShowCabinetPipes,showCabinetPipes,
 registerCabinetDisplayRefresh,subscribeClosedCabinetDisplay} from '../src/liquid/closedCabinetDisplay'
import {selectedResultSample,selectedResultTime,resultRenderRevision} from '../src/liquid/liquidTempColor'
import * as store from '../src/projectStore'
import {LatestSceneSync} from '../src/renderer/RendererControl'
afterEach(()=>{registerCabinetDisplayRefresh(null);vi.useRealTimers()})
it('the independent display revision does not notify project/history/result frame state',()=>{
 registerCabinetDisplayRefresh(null)
 setShowCabinetPipes(false)
 const a=[store.revision(),selectedResultSample(),selectedResultTime(),resultRenderRevision()]
 const refresh=vi.fn(),listen=vi.fn()
 registerCabinetDisplayRefresh(refresh)
 const unsubscribe=subscribeClosedCabinetDisplay(listen)
 const revision=leafDisplayRevision()
 setShowCabinetPipes(true)
 expect(refresh).toHaveBeenCalledOnce();expect(listen).toHaveBeenCalledOnce()
 expect(leafDisplayRevision()).toBe(revision+1)
 expect([store.revision(),selectedResultSample(),selectedResultTime(),resultRenderRevision()]).toEqual(a)
 unsubscribe()
})
it('schedules a new acknowledged scene when display changes under an otherwise frozen OV result tuple',async()=>{
 vi.useFakeTimers()
 registerCabinetDisplayRefresh(null);setShowCabinetPipes(false)
 const tuple=[60,600,18] as const
 const key=()=>displaySceneKey('case',12,tuple,leafDisplayRevision())
 const load=vi.fn(async()=>({ok:true}))
 const sync=new LatestSceneSync<{show:boolean}>(load,()=>{},key())
 setShowCabinetPipes(true)
 sync.offer(key(),()=>({show:showCabinetPipes()}))
 await vi.advanceTimersByTimeAsync(350)
 expect(load).toHaveBeenCalledOnce()
 expect(load.mock.calls[0]).toEqual([{show:true}])
 expect(sync.isCurrent(key())).toBe(true)
 sync.close()
})
