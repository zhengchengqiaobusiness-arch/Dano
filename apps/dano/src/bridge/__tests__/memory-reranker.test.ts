import { afterEach, expect, it, vi } from "vitest";
import { MemoryReranker } from "../memory-reranker.js";

const config = { url: "http://reranker:8080/v1/rerank", model: "synthetic-reranker",
  minimumLogit: 0, timeoutMs: 500, maxInputBytes: 4096, maxDocumentBytes: 2048, maxCandidates: 2 };
const candidates = [
  { uri: "viking://user/alice/memories/relevant.md", text: "用户的项目代号是青岚41", score: 0.42 },
  { uri: "viking://user/alice/memories/irrelevant.md", text: "用户喜欢绿色图表", score: 0.65 },
];

afterEach(() => vi.unstubAllGlobals());

it("keeps only positively reranked owner-bound candidates in model order", async () => {
  const fetch = vi.fn(async (_input: string | URL | Request, _init?: RequestInit) => Response.json({ results: [
    { index: 1, relevance_score: -8.2 }, { index: 0, relevance_score: 2.4 },
  ] }));
  vi.stubGlobal("fetch", fetch);
  const result = await new MemoryReranker(config).filter("我的项目代号？", candidates);
  expect(result).toHaveLength(1);
  expect(result[0]?.uri).toBe(candidates[0]?.uri);
  expect(result[0]?.score).toBeGreaterThan(0.5);
  expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body))).toMatchObject({
    model: "synthetic-reranker", documents: candidates.map(item => item.text),
  });
});

it("suppresses every candidate when the reranker is unavailable or returns an incomplete mapping", async () => {
  const reranker = new MemoryReranker(config);
  vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("private endpoint unavailable"); }));
  expect(await reranker.filter("query", candidates)).toEqual([]);
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ results: [{ index: 0, relevance_score: 4 }] })));
  expect(await reranker.filter("query", candidates)).toEqual([]);
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ results: [
    { index: 0, relevance_score: 4 }, { index: 0, relevance_score: 3 },
  ] })));
  expect(await reranker.filter("query", candidates)).toEqual([]);
});

it("does not send oversized content or continue after cancellation", async () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  const reranker = new MemoryReranker({ ...config, maxDocumentBytes: 8 });
  expect(await reranker.filter("query", candidates)).toEqual([]);
  const cancelled = new AbortController();
  cancelled.abort();
  expect(await new MemoryReranker(config).filter("query", candidates, cancelled.signal)).toEqual([]);
  expect(fetch).not.toHaveBeenCalled();
});

it("never forwards more candidates than its configured inference budget", async () => {
  const fetch = vi.fn(async (_input: string | URL | Request, _init?: RequestInit) => Response.json({ results: [
    { index: 0, relevance_score: 2 }, { index: 1, relevance_score: -2 },
  ] }));
  vi.stubGlobal("fetch", fetch);
  const result = await new MemoryReranker(config).filter("项目代号", [...candidates,
    { uri: "viking://user/alice/memories/third.md", text: "第三条", score: 0.9 }]);
  expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body)).documents).toHaveLength(2);
  expect(result).toHaveLength(1);
});
