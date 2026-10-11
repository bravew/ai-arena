package task_test

import (
    task "task"
    "testing"
)

func TestContract(t *testing.T) { if task.GCD(54,24)!=6 || task.GCD(-8,12)!=4 || task.GCD(0,0)!=0 { t.Fatal("gcd") } }
