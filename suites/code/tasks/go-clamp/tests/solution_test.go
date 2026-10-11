package task_test

import (
    task "task"
    "testing"
)

func TestContract(t *testing.T) { if task.Clamp(-2,0,5)!=0 || task.Clamp(3,0,5)!=3 || task.Clamp(9,0,5)!=5 { t.Fatal("clamp") } }
