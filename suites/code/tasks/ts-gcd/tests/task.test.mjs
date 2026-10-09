import test from "node:test";
import assert from "node:assert/strict";
import { gcd } from "../solution.ts";

test("gcd contract", () => { assert.equal(gcd(54, 24), 6); assert.equal(gcd(-8, 12), 4); assert.equal(gcd(0, 0), 0); });
