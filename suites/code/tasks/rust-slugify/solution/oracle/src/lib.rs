pub fn slugify(text: &str) -> String {
    let mut out = String::new(); for ch in text.chars().flat_map(char::to_lowercase) { if ch.is_ascii_alphanumeric() { out.push(ch); } else if !out.is_empty() && !out.ends_with('-') { out.push('-'); } } while out.ends_with('-') { out.pop(); } out
}
