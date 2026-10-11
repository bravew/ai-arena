package task

import "math"

func GCD(left, right int) int {
    left = int(math.Abs(float64(left))); right = int(math.Abs(float64(right))); for right != 0 { left, right = right, left%right }; return left
}
