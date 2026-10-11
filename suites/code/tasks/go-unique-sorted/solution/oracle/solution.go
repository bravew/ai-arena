package task

import "sort"

func UniqueSorted(values []int) []int {
    seen := map[int]bool{}; out := []int{}; for _, value := range values { if !seen[value] { seen[value] = true; out = append(out, value) } }; sort.Ints(out); return out
}
