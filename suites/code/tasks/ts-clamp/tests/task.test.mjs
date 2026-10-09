import test from "node:test";
import assert from "node:assert/strict";
import { clamp } from "../solution.ts";

test("clamp contract", () => { assert.equal(clamp(-2, 0, 5), 0); assert.equal(clamp(3, 0, 5), 3); assert.equal(clamp(9, 0, 5), 5); });
