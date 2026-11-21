import {useEffect,useState} from 'react'
import type {CfdProject,Construction} from '../schema/cfdproj'
import {fromDisplay,LENGTH_UNITS,stepFor,toDisplay,unitOf} from '../lengthUnits'
import {hasAuthoredTBranchLayout,positivePhysicalDiameter} from '../liquid/tBranch'
import {declareStandardFitting,fittingDiameter,setFittingDiameter} from '../liquid/pipeJunctionInspector'
import {useTranslation} from '../useTranslation'
import {NumberField} from './NumberField'

export function PipeJunctionInspector({project,model}:{project:CfdProject;model:Construction}) {
  const {t}=useTranslation(),unit=unitOf(project),diameter=fittingDiameter(model),layout=hasAuthoredTBranchLayout(model.cooling)
  const [draft,setDraft]=useState<number|null>(diameter),[error,setError]=useState(false)
  useEffect(()=>{setDraft(diameter);setError(false)},[model.uid,diameter])
  const value=layout?diameter:draft
  return <div className="prop-group" style={{borderLeftColor:'var(--hint-color)'}}>
    <div className="prop-title">{t('liquid.t_fitting_title')}</div>
    <div className="prop-row">
      <label htmlFor="fitting-diameter">{t('liquid.t_fitting_diameter')} ({LENGTH_UNITS[unit].label})</label>
      <NumberField id="fitting-diameter" value={value===null?null:toDisplay(value,unit)} step={stepFor(unit)} decimals={4}
        ariaDescribedBy="fitting-physical-help" onCommit={raw=>{
          const metres=fromDisplay(raw,unit)
          if (!positivePhysicalDiameter(metres)) {setError(true);return false}
          setError(false)
          if (layout) return setFittingDiameter(model.uid,metres)
          setDraft(metres);return true
        }}/>
    </div>
    <div id="fitting-physical-help" className="prop-hint">
      {layout ? (diameter===null?t('liquid.t_fitting_missing'):'') : t('liquid.t_fitting_layout_missing')}
    </div>
    {!layout && <>
      <div className="prop-hint">{t('liquid.t_fitting_declare_help')}</div>
      <button className="btn" disabled={!positivePhysicalDiameter(draft)} onClick={()=>setError(!declareStandardFitting(model.uid,draft!))}>
        {t('liquid.t_fitting_declare')}
      </button>
    </>}
    {error && <div role="alert" className="prop-hint">{t('liquid.t_fitting_invalid')}</div>}
  </div>
}
