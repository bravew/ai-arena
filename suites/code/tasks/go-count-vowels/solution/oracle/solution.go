package task

import "strings"

func CountVowels(text string) int {
    count := 0; for _, r := range strings.ToLower(text) { if strings.ContainsRune("aeiou", r) { count++ } }; return count
}
