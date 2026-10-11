/** A stable contestant hue derived from its ID, independent of list order and process lifetime. */
export function contestantHue(id: string): number {
  let hash = 2166136261;
  for (const character of id) {
    hash ^= character.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) % 360;
}

export function contestantColor(id: string): string {
  return `hsl(${contestantHue(id)} 62% 48%)`;
}
