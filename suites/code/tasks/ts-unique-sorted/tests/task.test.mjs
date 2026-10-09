import test from "node:test";
import assert from "node:assert/strict";
import { uniqueSorted } from "../solution.ts";

test("uniqueSorted contract", () => { assert.deepEqual(uniqueSorted([3, 1, 3, -2]), [-2, 1, 3]); });
