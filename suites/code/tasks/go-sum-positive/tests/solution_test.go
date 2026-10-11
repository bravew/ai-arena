package task_test

import (
    task "task"
    "testing"
)

func TestContract(t *testing.T) { if task.SumPositive([]int{-2,0,3,4})!=7 || task.SumPositive(nil)!=0 { t.Fatal("sum") } }
