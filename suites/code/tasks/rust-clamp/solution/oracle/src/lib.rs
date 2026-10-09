pub fn clamp(value: i64, lower: i64, upper: i64) -> i64 {
    assert!(lower <= upper); value.max(lower).min(upper)
}
