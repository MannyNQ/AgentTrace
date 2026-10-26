import { test } from "node:test";
import assert from "node:assert/strict";
import { safeNext } from "./session.ts";

test("a same-site path is a place to return to after signing in", () => {
  assert.equal(safeNext("/p/default/traces?replays=1"), "/p/default/traces?replays=1");
});

test("an encoded tab is just a path segment, and stays on the site", () => {
  assert.equal(safeNext("/%09/evil.example"), "/%09/evil.example");
});

test("anything that leaves the site is refused", () => {
  const tricks = [
    "//evil.example",
    "/\\evil.example",
    "/\t/evil.example", // browsers drop tabs and newlines before parsing
    "/\n/evil.example",
    "/\r\n/evil.example",
    "/\t\\evil.example",
    "https://evil.example",
    "evil",
    "",
    null,
    undefined,
  ];
  for (const next of tricks) {
    assert.equal(safeNext(next), null, String(next));
  }
});
