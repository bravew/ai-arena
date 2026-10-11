package task_test

import (
    task "task"
    "testing"
)

func TestContract(t *testing.T) { if task.Factorial(0)!=1 || task.Factorial(5)!=120 { t.Fatal("factorial") }; defer func(){ if recover()==nil { t.Fatal("negative must panic") } }(); task.Factorial(-1) }
