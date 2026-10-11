package task_test

import (
    task "task"
    "testing"
)

func TestContract(t *testing.T) { if !task.IsEven(0) || !task.IsEven(-4) || task.IsEven(7) { t.Fatal("is even") } }
