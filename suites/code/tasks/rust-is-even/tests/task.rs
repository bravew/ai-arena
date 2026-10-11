use task::*;

#[test]
fn contract() { assert!(is_even(0)); assert!(is_even(-4)); assert!(!is_even(7)); }
