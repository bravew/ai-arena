import test from "node:test";
import assert from "node:assert/strict";
import { slugify } from "../solution.ts";

test("slugify contract", () => { assert.equal(slugify("Hello, Arena!"), "hello-arena"); assert.equal(slugify("---"), ""); });
