use task::*;

#[test]
fn contract() { assert_eq!(slugify("Hello, Arena!"),"hello-arena"); assert_eq!(slugify("---"),""); }
