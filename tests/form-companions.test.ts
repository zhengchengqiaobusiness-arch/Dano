import test from "node:test";
import assert from "node:assert/strict";
import { fillEmptyCompanionNames } from "../src/browser/form-companions.js";

test("empty name companions copy from another same-id name on the form model", () => {
  const model = {
    deptId: 103,
    deptName: "",
    keeperId: 1,
    keeperName: "芋道源码",
    keeperDeptId: 103,
    keeperDeptName: "研发部门",
    companyId: 0,
    companyName: ""
  };
  assert.deepEqual(fillEmptyCompanionNames(model), ["deptName"]);
  assert.equal(model.deptName, "研发部门");
  assert.equal(model.companyName, "");
});

test("companion copy does not invent a name when no same-id sibling exists", () => {
  const model = { deptId: 103, deptName: "", companyId: 100, companyName: "宇擎源码" };
  assert.deepEqual(fillEmptyCompanionNames(model), []);
  assert.equal(model.deptName, "");
});
