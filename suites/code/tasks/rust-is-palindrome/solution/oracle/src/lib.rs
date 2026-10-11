pub fn is_palindrome(text: &str) -> bool {
    let cleaned: String = text.chars().filter(|ch| ch.is_ascii_alphanumeric()).flat_map(char::to_lowercase).collect(); cleaned.chars().eq(cleaned.chars().rev())
}
