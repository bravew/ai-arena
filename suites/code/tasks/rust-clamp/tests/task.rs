use task::*;

#[test]
fn contract() { assert_eq!(clamp(-2,0,5),0); assert_eq!(clamp(3,0,5),3); assert_eq!(clamp(9,0,5),5); }
