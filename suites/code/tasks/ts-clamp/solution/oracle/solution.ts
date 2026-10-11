export function clamp(value: number, lower: number, upper: number): number {
  if (lower > upper) throw new RangeError("lower exceeds upper");
  return Math.min(Math.max(value, lower), upper);
}
