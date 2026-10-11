import { describe, expect, it } from 'vitest';
import { contestantColor, contestantHue } from '../../src/theme';

describe('contestant colors', () => {
  it('assigns a stable hue independent of input order', () => {
    expect(contestantHue('opus-direct')).toBe(contestantHue('opus-direct'));
    expect(contestantColor('opus-direct')).toMatch(/^hsl\(\d+ 62% 48%\)$/);
    expect(contestantColor('gpt-direct')).not.toBe(contestantColor('opus-direct'));
  });

  it('keeps hue within the CSS hue circle', () => {
    for (const id of ['', 'same', 'c-ablation-kit', '🤖']) {
      expect(contestantHue(id)).toBeGreaterThanOrEqual(0);
      expect(contestantHue(id)).toBeLessThan(360);
    }
  });
});
