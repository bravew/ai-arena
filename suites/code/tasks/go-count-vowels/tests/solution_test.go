package task_test

import (
    task "task"
    "testing"
)

func TestContract(t *testing.T) { if task.CountVowels("Arena")!=3 || task.CountVowels("rhythm")!=0 { t.Fatal("vowels") } }
