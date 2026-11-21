import { renderToStaticMarkup } from 'react-dom/server'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { CfdProject } from '../../src/schema/cfdproj'
import type { ServerInfo } from '../../src/run/session'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import * as store from '../../src/projectStore'
import { Inspector } from '../../src/components/Inspector'
import { SystemPanel } from '../../src/components/SystemPanel'
import { t } from '../../src/i18n'

const backend = vi.hoisted(() => ({ info: null as ServerInfo | null }))

vi.mock('../../src/run/session', () => ({
  getServerInfo: () => backend.info,
  getCaseStatus: () => null,
  getJob: () => null,
}))

beforeEach(() => {
  const project = stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject
  project.meta = { ...project.meta, casePath: '/cases/current-project' }
  store.loadProject(project, null)
  backend.info = null
})

for (const panel of ['inspector', 'system'] as const) {
  describe(`${panel} case directory display`, () => {
    function render(): HTMLElement {
      const host = document.createElement('div')
      host.innerHTML = renderToStaticMarkup(panel === 'inspector'
        ? <Inspector project={store.getProject()} selectedUid={null} />
        : <SystemPanel />)
      return host
    }

    it('keeps the saved directory visible while the backend is unavailable', () => {
      const host = render()
      const path = host.querySelector('#dctwin-case-display')
      expect(path?.textContent).toContain('/cases/current-project')
      expect(path?.getAttribute('title')).toBe('/cases/current-project')
      expect(host.querySelector('#dctwin-setdir-btn')?.textContent).not.toContain('已就绪')
    })

    it('does not show the previous backend directory as the current project or ready', () => {
      backend.info = {
        dctwinAvailable: true,
        caseDir: '/cases/previous-project',
        caseDirOk: true,
        allOk: true,
      }
      const host = render()
      expect(host.querySelector('#dctwin-case-display')?.textContent).toContain('/cases/current-project')
      expect(host.querySelector('#dctwin-case-display')?.textContent).not.toContain('/cases/previous-project')
      expect(host.querySelector('#dctwin-setdir-btn')?.textContent).not.toContain('已就绪')
    })

    it('shows the directory ready once the backend confirms the project directory', () => {
      backend.info = {
        dctwinAvailable: true,
        caseDir: '/cases/current-project',
        caseDirOk: true,
        allOk: true,
      }
      expect(render().querySelector('#dctwin-setdir-btn')?.textContent).toContain('已就绪')
    })

    it('shows not set when neither the project nor backend has a directory', () => {
      store.mutateLive((project) => {
        project.meta = { ...project.meta, casePath: '' }
      })
      expect(render().querySelector('#dctwin-case-display')?.textContent).toBe(t('inspector.dctwin_not_set'))
    })
  })
}
