package task_test

import (
    task "task"
    "testing"
    "reflect"
)

func TestContract(t *testing.T) { got:=task.UniqueSorted([]int{3,1,3,-2}); want:=[]int{-2,1,3}; if !reflect.DeepEqual(got,want) { t.Fatal(got) } }
