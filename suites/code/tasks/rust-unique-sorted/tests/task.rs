use task::*;

#[test]
fn contract() { assert_eq!(unique_sorted(&[3,1,3,-2]),vec![-2,1,3]); }
