import test from "node:test";
import assert from "node:assert/strict";
import { sumPositive } from "../solution.ts";

test("sumPositive contract", () => { assert.equal(sumPositive([-2, 0, 3, 4]), 7); assert.equal(sumPositive([]), 0); });
