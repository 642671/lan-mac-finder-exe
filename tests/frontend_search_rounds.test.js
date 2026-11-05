"use strict";

const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");

const source = fs.readFileSync(
  path.join(__dirname, "..", "src", "frontend", "app.js"),
  "utf8"
);
const context = {
  console,
  location: { pathname: "/" },
  navigator: {},
  window: {
    addEventListener: () => undefined,
    clearTimeout,
    setTimeout
  },
  document: {
    addEventListener: () => undefined,
    readyState: "loading"
  }
};

vm.createContext(context);
vm.runInContext(source, context, { filename: "src/frontend/app.js" });

test("device web links are limited to valid IPv4 addresses", () => {
  assert.equal(context.deviceWebUrl("192.0.2.19"), "http://192.0.2.19/");
  assert.equal(context.deviceWebUrl(" 198.51.100.1 "), "http://198.51.100.1/");
  assert.equal(context.deviceWebUrl("192.0.2.256"), "");
  assert.equal(context.deviceWebUrl("192.0.2"), "");
  assert.equal(context.deviceWebUrl("http://192.0.2.1/"), "");
  assert.equal(context.deviceWebUrl(""), "");
});

test("polling search stops after ten consecutive no-match rounds", () => {
  context.state.searchActive = true;
  context.state.searchRounds = 0;
  context.state.lastRoundJobId = "";
  context.state.activeSuffix = "ABCD";

  for (let round = 1; round <= 10; round += 1) {
    const outcome = context.registerSearchRound("job-" + round, 0);
    assert.equal(outcome.changed, true);
    assert.equal(outcome.rounds, round);
    assert.equal(outcome.exhausted, round === 10);
  }
  assert.equal(context.state.searchRounds, 10);
  assert.equal(context.searchExhaustedMessage().includes("10 轮"), true);
  assert.equal(context.searchExhaustedMessage().includes("ABCD"), true);

  context.state.searchActive = false;
  context.state.searchRounds = 0;
  context.state.lastRoundJobId = "";
  context.state.activeSuffix = "";
});

test("matches end the session and repeated job ids count once", () => {
  context.state.searchActive = true;
  context.state.searchRounds = 0;
  context.state.lastRoundJobId = "";
  context.state.activeSuffix = "ABCD";

  let outcome = context.registerSearchRound("job-a", 0);
  assert.equal(outcome.rounds, 1);
  outcome = context.registerSearchRound("job-a", 0);
  assert.equal(outcome.changed, false);
  assert.equal(context.state.searchRounds, 1);

  outcome = context.registerSearchRound("job-b", 2);
  assert.equal(outcome.found, true);
  assert.equal(outcome.exhausted, false);

  context.state.searchActive = false;
  outcome = context.registerSearchRound("job-c", 0);
  assert.equal(outcome.changed, false);
  assert.equal(context.state.searchRounds, 1);

  context.state.searchRounds = 0;
  context.state.lastRoundJobId = "";
  context.state.activeSuffix = "";
});
