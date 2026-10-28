import { test } from "node:test";
import assert from "node:assert/strict";
import { clientAddress, safeNext } from "./session.ts";

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

test("the client is the address the nearest proxy saw", () => {
  // The browser forged the first entry; the reverse proxy appended what it really saw.
  assert.equal(clientAddress("6.6.6.6, 203.0.113.9", 1), "203.0.113.9");
  assert.equal(clientAddress("203.0.113.9", 1), "203.0.113.9"); // no proxy: Next sets it
  assert.equal(clientAddress("6.6.6.6, 203.0.113.9, 10.0.0.7", 2), "203.0.113.9"); // CDN, then nginx
  assert.equal(clientAddress(" 2001:db8::1 ", 1), "2001:db8::1");
});

test("no usable address is no address", () => {
  assert.equal(clientAddress(null, 1), null);
  assert.equal(clientAddress("", 1), null);
  assert.equal(clientAddress("203.0.113.9", 2), null); // fewer hops than configured
});
