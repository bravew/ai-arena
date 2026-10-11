export function gcd(left: number, right: number): number {
  left = Math.abs(left); right = Math.abs(right);
  while (right !== 0) [left, right] = [right, left % right];
  return left;
}
