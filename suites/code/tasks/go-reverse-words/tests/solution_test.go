package task_test

import (
    task "task"
    "testing"
)

func TestContract(t *testing.T) { if task.ReverseWords(" one  two ")!="two one" || task.ReverseWords("")!="" { t.Fatal("reverse words") } }
