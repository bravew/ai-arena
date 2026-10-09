use task::*;

#[test]
fn contract() { assert!(is_palindrome("Never odd or even")); assert!(!is_palindrome("Arena")); }
