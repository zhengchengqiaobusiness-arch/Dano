import { describe, expect, it } from "vitest";
import { markdownDroppedObjectLiteralContent } from "./markdownFallback";

describe("markdownDroppedObjectLiteralContent", () => {
  it("detects unquoted object keys lost by markdown rendering", () => {
    const source = `2. id=module，选项为：
   - {id: "frontend", label: "前端", children: [
       {id: "web", label: "Web"}
     ]}
   default="frontend"`;
    const rendered = `2. id=module，选项为：
• ,
]}
• default="frontend"`;

    expect(markdownDroppedObjectLiteralContent(source, rendered)).toBe(true);
  });

  it("keeps markdown output when object keys remain visible", () => {
    const source = `配置：{id: "frontend", label: "前端"}`;
    const rendered = `配置：{id: "frontend", label: "前端"}`;

    expect(markdownDroppedObjectLiteralContent(source, rendered)).toBe(false);
  });

  it("ignores ordinary markdown without object literals", () => {
    expect(markdownDroppedObjectLiteralContent("**重点**", "重点")).toBe(false);
  });
});
