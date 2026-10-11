package task


func SumPositive(values []int) int {
    total := 0; for _, value := range values { if value > 0 { total += value } }; return total
}
