pub fn unique_sorted(values: &[i64]) -> Vec<i64> {
    let mut out = values.to_vec(); out.sort_unstable(); out.dedup(); out
}
