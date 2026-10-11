import test from "node:test";
import assert from "node:assert/strict";
import { countVowels } from "../solution.ts";

test("countVowels contract", () => { assert.equal(countVowels("Arena"), 3); assert.equal(countVowels("rhythm"), 0); });
