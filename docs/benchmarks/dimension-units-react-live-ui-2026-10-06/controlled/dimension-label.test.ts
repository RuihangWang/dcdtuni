import { createElement } from '/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/src/frontend/react/node_modules/react/index.js'
import { createRoot } from '/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/src/frontend/react/node_modules/react-dom/client.js'
import { flushSync } from '/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/src/frontend/react/node_modules/react-dom/index.js'
import { afterEach, describe, expect, it } from '/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/src/frontend/react/node_modules/vitest/dist/index.js'
import { Inspector } from '/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/src/frontend/react/src/components/Inspector.tsx'
import { setLocale } from '/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/src/frontend/react/src/i18n.ts'
import * as store from '/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/src/frontend/react/src/projectStore.ts'
import { stateV4 } from '/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/src/frontend/react/src/legacy/cfdprojModules.ts'
import { createModel } from '/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/src/frontend/react/src/legacy/factories.ts'
let root
let host
afterEach(() => {
  if (root) flushSync(() => root.unmount())
  host?.remove()
  root = undefined
  host = undefined
  setLocale('zh-CN')
})
describe('actual generic Inspector dimension heading follows its input unit', () => {
  for (const locale of ['zh-CN', 'en-US']) {
    for (const unit of ['m', 'cm', 'mm']) {
      it(locale + ' / ' + unit + ' keeps canonical .125m while labelling the displayed value', () => {
        setLocale(locale)
        const project = stateV4().normalizeStateV4(stateV4().getInitialStateV4())
        project.meta = { ...project.meta, inputUnit: unit }
        store.loadProject(project, null)
        const made = createModel(store.getProject(), 'box')
        made.model.meta.name = 'Thermal_block_control'
        made.model.geometry.size = { x: .125, y: .125, z: .125 }
        store.addModel(made.bucket, made.model)
        const before = JSON.stringify(store.getProject())
        host = document.createElement('div')
        document.body.appendChild(host)
        root = createRoot(host)
        flushSync(() => root.render(createElement(Inspector, {
          project: store.getProject(), selectedUid: made.model.uid, clearSelection: () => {},
        })))
        const prefix = locale === 'zh-CN' ? '📐 尺寸' : '📐 Size'
        const title = [...host.querySelectorAll('.prop-title')].find(el => el.textContent?.startsWith(prefix))
        expect(title).toBeDefined()
        const values = [...title.parentElement.querySelectorAll('input')].map(el => el.value)
        const wanted = unit === 'mm' ? '125.0000' : unit === 'cm' ? '12.5000' : '0.1250'
        expect(values).toEqual([wanted, wanted, wanted])
        expect(JSON.stringify(store.getProject())).toBe(before)
        expect(store.findConstruction(store.getProject(), made.model.uid).model.geometry.size).toEqual({x: .125, y: .125, z: .125})
        expect(title.textContent).toBe(prefix + ' (' + unit + ')')
      })
    }
  }
})
