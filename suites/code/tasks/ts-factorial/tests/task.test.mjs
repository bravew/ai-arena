import test from "node:test";
import assert from "node:assert/strict";
import { factorial } from "../solution.ts";

test("factorial contract", () => { assert.equal(factorial(0), 1); assert.equal(factorial(5), 120); assert.throws(() => factorial(-1)); });
