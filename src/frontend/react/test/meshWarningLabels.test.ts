import { afterEach, expect, it } from 'vitest'
import { getLocale, setLocale } from '../src/i18n'
import { meshWarningLabel, meshWarningMessage } from '../src/run/meshWarningLabels'
const previousLocale = getLocale()
afterEach(() => setLocale(previousLocale))
it('renders native count and threshold in the chosen locale', () => {
  setLocale('zh-CN')
  expect(meshWarningLabel({level:2,code:'low_volume_ratio'})).toBe('相邻单元体积差异过大')
  expect(meshWarningMessage({level:2,code:'low_volume_ratio',count:60,threshold:0.01})).toBe('有 60 个面的相邻单元体积比低于 0.01。')
  setLocale('en-US')
  expect(meshWarningMessage({level:2,code:'bad_face_tets',count:77})).toContain('77 faces')
})
it('keeps unstructured or unfamiliar native reports verbatim', () => {
  expect(meshWarningMessage({level:2,code:'bad_face_tets',message:'exact native details'})).toBe('exact native details')
  expect(meshWarningMessage({level:2,code:'unknown',message:'new native detail'})).toBe('new native detail')
})
