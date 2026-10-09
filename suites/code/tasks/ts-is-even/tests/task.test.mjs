import test from "node:test";
import assert from "node:assert/strict";
import { isEven } from "../solution.ts";

test("isEven contract", () => { assert.equal(isEven(0), true); assert.equal(isEven(-4), true); assert.equal(isEven(7), false); });
