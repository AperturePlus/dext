import { describe, expect, it } from 'vitest'
import { formatNumber, shortId } from '../src/utils/format'

describe('format utilities', () => {
  it('formats metric values', () => {
    expect(formatNumber(12000)).toBe('12,000')
    expect(formatNumber(undefined)).toBe('—')
  })

  it('shortens long IDs', () => {
    expect(shortId('019f135d-d244-7c5f-be55-f5fed4c1a231')).toBe('019f135d…a231')
  })
})
