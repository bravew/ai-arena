package task

import "strings"
    "unicode"

func IsPalindrome(text string) bool {
    cleaned := []rune{}; for _, r := range strings.ToLower(text) { if unicode.IsLetter(r) || unicode.IsDigit(r) { cleaned = append(cleaned, r) } }; for left, right := 0, len(cleaned)-1; left < right; left, right = left+1, right-1 { if cleaned[left] != cleaned[right] { return false } }; return true
}
