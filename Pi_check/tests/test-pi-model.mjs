/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 */

import test from "node:test";
import assert from "node:assert/strict";
import { AuthStorage, ModelRegistry } from "@mariozechner/pi-coding-agent";
import { applyPiModelConfig, readPiModelEnv } from "../src/pi-model.mjs";

test("没有凭证时就是线上这次失败：没有可用的 PI 模型或凭证", () => {
  const auth = AuthStorage.inMemory();
  const registry = ModelRegistry.inMemory(auth);
  assert.throws(
    () => applyPiModelConfig(auth, registry, {}),
    /没有可用的 PI 模型或凭证/,
  );
});

test("DANO_PI_* 必须能注册 openai-compat 并解析出模型", () => {
  const auth = AuthStorage.inMemory();
  const registry = ModelRegistry.inMemory(auth);
  const resolved = applyPiModelConfig(auth, registry, {
    DANO_PI_API_KEY: "test-key",
    DANO_PI_BASE_URL: "https://token-plan-cn.xiaomimimo.com/v1",
    DANO_PI_MODEL: "mimo-v2.5",
  });
  assert.equal(resolved.model.id, "mimo-v2.5");
  assert.equal(resolved.provider, "openai-compat");
  assert.equal(resolved.keySet, true);
  assert.equal(readPiModelEnv({}).provider, "openai-compat");
});
