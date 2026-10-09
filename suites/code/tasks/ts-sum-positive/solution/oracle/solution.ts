export function sumPositive(values: number[]): number {
  return values.filter(value => value > 0).reduce((total, value) => total + value, 0);
}
