import test from "node:test";
import assert from "node:assert/strict";
import { reverseWords } from "../solution.ts";

test("reverseWords contract", () => { assert.equal(reverseWords(" one  two "), "two one"); assert.equal(reverseWords(""), ""); });
