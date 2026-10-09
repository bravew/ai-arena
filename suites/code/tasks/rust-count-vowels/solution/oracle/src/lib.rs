pub fn count_vowels(text: &str) -> usize {
    text.chars().filter(|ch| "aeiouAEIOU".contains(*ch)).count()
}
