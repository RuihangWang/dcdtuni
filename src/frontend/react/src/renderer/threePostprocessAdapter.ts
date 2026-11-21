/** Thin carrier adapter around the retained Three viewport. */
import type { PostprocessLayers } from '../postprocess/ppLayers'
import type { DisplayAppearance, DisplayApplyContext, PostprocessRendererAdapter } from '../postprocess/displaySession'
interface ThreePostprocessViewport {
  apply(layers: PostprocessLayers): void
  clear(): void
  setShown?(shown: boolean): void
  setInternalVisible?(visible: boolean): void
  setModelOpacity?(value:number):void
  setFieldOpacity?(value:number):void
  setLightIntensity?(value:number):void
  setAO?(value:boolean):void
  setAOStrength?(value:number):void
  setRenderOrder?(keys:string[]):void
}
function viewport(): ThreePostprocessViewport | null {
  return (window as unknown as {__ppViewport?: ThreePostprocessViewport}).__ppViewport ?? null
}
function configureAppearance(value:DisplayAppearance,context:DisplayApplyContext) {
  if(!context.isCurrent())return
  const active=viewport();if(!active)return
  active.setModelOpacity?.(value.modelOpacity);active.setFieldOpacity?.(value.fieldOpacity)
  // Light is the viewport's shared lighting state (ui/lightingState), set from the
  // top bar or the panel slider; a display frame's appearance must not reset it.
  active.setAO?.(value.ao)
  active.setAOStrength?.(value.aoStrength);active.setRenderOrder?.([...value.renderOrder])
}
export function createThreePostprocessAdapter(): PostprocessRendererAdapter {
  return {kind:'three',render(frame,context) {
    if (!context.isCurrent()) return {status:'rejected',message:'Shared display source changed'}
    const active = viewport()
    if (!active) return {status:'rejected',message:'Postprocess viewport is not ready'}
    if(frame.appearance)configureAppearance(frame.appearance,context)
    active.apply(frame.layers);active.setInternalVisible?.(frame.internalVisible)
    return {status:'displayed'}
  },clear(context) {if (context.isCurrent()) viewport()?.clear()},dispose() {},configureAppearance}
}
