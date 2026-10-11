package task_test

import (
    task "task"
    "testing"
)

func TestContract(t *testing.T) { if task.Slugify("Hello, Arena!")!="hello-arena" || task.Slugify("---")!="" { t.Fatal("slug") } }
