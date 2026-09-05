import test from "node:test";
import assert from "node:assert/strict";
import {
  applyFormCompanions,
  fillEmptyCompanionNames,
  fillMissingIdentitiesFromLinkedRecords
} from "../src/browser/form-companions.js";

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

test("empty identities copy from the selected record that shares filled ids and names", () => {
  const model = {
    sealId: 1,
    sealName: "公章",
    sealNo: "SZ0001",
    keeperId: 1,
    keeperName: "芋道源码",
    keeperDeptId: 103,
    keeperDeptName: "研发部门",
    deptId: 103,
    deptName: "",
    companyId: 0,
    companyName: ""
  };
  const seal = {
    id: 1,
    sealName: "公章",
    sealNo: "SZ0001",
    companyId: 101,
    companyName: "深圳总公司",
    keeperId: 1,
    keeperName: "芋道源码",
    keeperDeptId: 103,
    keeperDeptName: "研发部门"
  };
  const user = {
    id: 1,
    nickname: "擎天柱",
    companyId: 100,
    companyName: "宇擎源码"
  };
  assert.deepEqual(applyFormCompanions(model, [seal, user]), ["deptName", "companyName", "companyId"]);
  assert.equal(model.deptName, "研发部门");
  assert.equal(model.companyId, 101);
  assert.equal(model.companyName, "深圳总公司");
});

test("linked identity copy stays put when candidate records disagree", () => {
  const model = { sealId: 1, sealName: "公章", companyId: 0, companyName: "" };
  const left = { id: 1, sealName: "公章", companyId: 101, companyName: "深圳总公司" };
  const right = { id: 1, sealName: "公章", companyId: 100, companyName: "宇擎源码" };
  assert.deepEqual(fillMissingIdentitiesFromLinkedRecords(model, [left, right]), []);
  assert.equal(model.companyName, "");
  assert.equal(model.companyId, 0);
});
