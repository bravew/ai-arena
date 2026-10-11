import test from "node:test";
import assert from "node:assert/strict";
import { isPalindrome } from "../solution.ts";

test("isPalindrome contract", () => { assert.equal(isPalindrome("Never odd or even"), true); assert.equal(isPalindrome("Arena"), false); });
