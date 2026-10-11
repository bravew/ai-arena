package task

import "strings"

func Slugify(text string) string {
    text = strings.ToLower(text); var b strings.Builder; dash := false; for _, r := range text { if r >= 'a' && r <= 'z' || r >= '0' && r <= '9' { if dash && b.Len() > 0 { b.WriteByte('-') }; b.WriteRune(r); dash = false } else { dash = true } }; return b.String()
}
