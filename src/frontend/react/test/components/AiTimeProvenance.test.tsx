import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { expect, it } from 'vitest'
import { AiInferencePanel } from '../../src/components/AiInferencePanel'

it('labels the independent H5 prediction as not conditioned on the selected CFD time', () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  try {
    flushSync(() => root.render(<AiInferencePanel runDir="log/run" initialised
      session={{ runId: 'run', timeValue: 100, revision: 'rev' }} />))
    const notice = host.querySelector('#pp-ai-time-provenance')
    expect(notice?.textContent).toMatch(/H5/i)
    expect(notice?.textContent).toMatch(/时刻|time/i)
  } finally {
    flushSync(() => root.unmount())
    host.remove()
  }
})
