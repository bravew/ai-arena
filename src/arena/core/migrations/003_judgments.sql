-- One pairwise judgment per ordered (trial A, trial B, task, judge version). The order-swap
-- verdicts are the judge's raw positional answers in the original and the swapped order.
-- A verdict that changes with position is a flip: it is recorded as position_bias and the
-- winner is then a tie. A tie is winner = 'tie'.
CREATE TABLE judgments (
    trial_a_id TEXT NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
    trial_b_id TEXT NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL,
    judge_id TEXT NOT NULL,
    judge_version TEXT NOT NULL,
    winner TEXT NOT NULL CHECK (winner IN ('trial_a', 'trial_b', 'tie')),
    first_order_winner TEXT NOT NULL CHECK (first_order_winner IN ('a', 'b', 'tie')),
    swapped_order_winner TEXT NOT NULL CHECK (swapped_order_winner IN ('a', 'b', 'tie')),
    position_bias INTEGER NOT NULL CHECK (position_bias IN (0, 1)),
    rationale TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (trial_a_id, trial_b_id, task_id, judge_id, judge_version),
    CHECK (trial_a_id <> trial_b_id),
    CHECK ((position_bias = 1) = (first_order_winner <> CASE swapped_order_winner
        WHEN 'a' THEN 'b' WHEN 'b' THEN 'a' ELSE 'tie' END)),
    CHECK (position_bias = 1 OR winner = CASE first_order_winner
        WHEN 'a' THEN 'trial_a' WHEN 'b' THEN 'trial_b' ELSE 'tie' END),
    CHECK (position_bias = 0 OR winner = 'tie')
);

CREATE INDEX judgments_by_trial_b ON judgments(trial_b_id);
CREATE INDEX judgments_by_task ON judgments(task_id, judge_id, judge_version);
