import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { getScanoRoi, scanoRoiRequest, setScanoRoi } from '../src/run/scanoRoi'
import { RangeSlider } from '../src/components/RangeSlider'

describe('AI solve region', () => {
  it('sends a box only when the local ROI is chosen, min and max ordered', () => {
    setScanoRoi({ enabled: false, box: { min: [3, 0, 2], max: [1, 4, 5] } })
    expect(scanoRoiRequest()).toBeNull()
    expect(getScanoRoi().box).toEqual({ min: [1, 0, 2], max: [3, 4, 5] })
    setScanoRoi({ enabled: true })
    expect(scanoRoiRequest()).toEqual({ min: [1, 0, 2], max: [3, 4, 5] })
    setScanoRoi({ box: null })
    expect(scanoRoiRequest()).toBeNull()
  })
})

describe('RangeSlider', () => {
  it('marks the kept window, inside or (reversed) outside the thumbs', () => {
    const html = renderToStaticMarkup(
      <RangeSlider min={0} max={10} lo={2} hi={8} invert onChange={() => undefined} />,
    )
    expect(html).toContain('--lo:20%')
    expect(html).toContain('--hi:80%')
    expect(html).toContain('range-slider inverted')
    expect(html).toContain('data-invert="1"')
  })
})
