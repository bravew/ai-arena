export function factorial(value: number): number {
  if (!Number.isInteger(value) || value < 0) throw new RangeError("invalid value");
  let result = 1; for (let factor = 2; factor <= value; factor++) result *= factor;
  return result;
}
