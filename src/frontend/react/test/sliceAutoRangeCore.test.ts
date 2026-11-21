import {expect,it} from 'vitest'
import {defaultPostprocessState} from '../src/postprocess/ppConfig'
import {isSliceAutoRange,resolveSliceColorRange} from '../src/postprocess/ppSliceRange'
it('defaults explicitly to auto, while missing historical flags retain nonflat limits',()=>{
 expect(defaultPostprocessState().slice.autoScale).toBe(true)
 expect(resolveSliceColorRange({climMin:293.15,climMax:303.15},[300,302])).toEqual([293.15,303.15])
})
it.each([[0,0],[295.15,295.15],[295.149993896,295.15]])('preserves missing or manual literal range %s .. %s', (min,max)=>{
 for(const autoScale of [undefined,false]){
  expect(isSliceAutoRange({climMin:min,climMax:max,autoScale})).toBe(false)
  expect(resolveSliceColorRange({climMin:min,climMax:max,autoScale},[300,302])).toEqual([min,max])
 }
 expect(resolveSliceColorRange({climMin:min,climMax:max,autoScale:true},[300,302])).toEqual([300,302])
})
