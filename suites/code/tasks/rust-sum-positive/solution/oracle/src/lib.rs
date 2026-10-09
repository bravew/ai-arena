pub fn sum_positive(values: &[i64]) -> i64 {
    values.iter().filter(|value| **value > 0).sum()
}
