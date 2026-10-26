import { strict as assert } from "node:assert";
import { test } from "node:test";
import { firstDifference } from "./trajectory.ts";

const lookup = { tool: "lookup_order", args: { order_id: "42", verbose: true } };
const refund = { tool: "issue_refund", args: { order_id: "42" } };
const escalate = { tool: "escalate" };

test("identical trajectories have no first difference", () => {
  assert.equal(firstDifference([lookup, refund], [lookup, refund]), null);
  assert.equal(firstDifference([], []), null);
});

test("argument order doesn't matter, but every argument does", () => {
  const reordered = { tool: "lookup_order", args: { verbose: true, order_id: "42" } };
  assert.equal(firstDifference([lookup], [reordered]), null);
  // An expected trajectory would match this (partial args); a recording doesn't.
  assert.equal(firstDifference([lookup], [{ tool: "lookup_order", args: { order_id: "42" } }]), 0);
  assert.equal(firstDifference([lookup], [{ tool: "lookup_order", args: { ...lookup.args, order_id: 42 } }]), 0);
});

test("a different tool is a difference", () => {
  assert.equal(firstDifference([lookup, refund], [lookup, escalate]), 1);
});

test("a prefix differs at the first missing step, on either side", () => {
  assert.equal(firstDifference([lookup, refund], [lookup]), 1);
  assert.equal(firstDifference([lookup], [lookup, escalate]), 1);
  assert.equal(firstDifference([], [escalate]), 0);
});

test("no arguments and empty arguments differ only when one side has some", () => {
  assert.equal(firstDifference([escalate], [{ tool: "escalate" }]), null);
  assert.equal(firstDifference([escalate], [{ tool: "escalate", args: { why: "x" } }]), 0);
});
