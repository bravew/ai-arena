use task::*;

#[test]
fn contract() { assert_eq!(reverse_words(" one  two "),"two one"); assert_eq!(reverse_words(""),""); }
