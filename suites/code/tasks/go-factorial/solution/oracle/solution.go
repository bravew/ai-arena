package task


func Factorial(value int) int {
    if value < 0 { panic("negative input") }; result := 1; for factor := 2; factor <= value; factor++ { result *= factor }; return result
}
