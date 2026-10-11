use task::*;

#[test]
fn contract() { assert_eq!(count_vowels("Arena"),3); assert_eq!(count_vowels("rhythm"),0); }
