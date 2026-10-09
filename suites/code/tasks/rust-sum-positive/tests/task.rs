use task::*;

#[test]
fn contract() { assert_eq!(sum_positive(&[-2,0,3,4]),7); assert_eq!(sum_positive(&[]),0); }
