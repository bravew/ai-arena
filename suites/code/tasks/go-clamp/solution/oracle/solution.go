package task


func Clamp(value, lower, upper int) int {
    if lower > upper { panic("lower exceeds upper") }; if value < lower { return lower }; if value > upper { return upper }; return value
}
