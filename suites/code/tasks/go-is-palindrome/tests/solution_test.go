package task_test

import (
    task "task"
    "testing"
)

func TestContract(t *testing.T) { if !task.IsPalindrome("Never odd or even") || task.IsPalindrome("Arena") { t.Fatal("palindrome") } }
