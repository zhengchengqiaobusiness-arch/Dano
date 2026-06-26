# Skill 调用渲染优化实现计划

Issue: https://github.com/zhengchengqiaobusiness-arch/Dano/issues/8

## 需求

参考 pi TUI 的 skill 渲染机制，优化 Dano Web 页面的 skill 显示。

## 三个渲染点

### 1. `read` 工具检测 SKILL.md（主要）

当 `read` 工具读取 SKILL.md 文件时，tool call 显示应从 `read /path/to/SKILL.md` 变为 `图标 skill-name`。

**pi 参考**: `read.js` 中的 `getCompactReadClassification` 函数

```javascript
if (fileName === "SKILL.md") {
    return { kind: "skill", label: basename(dirname(absolutePath)) || fileName };
}
```

**实现位置**: `ChatTranscript.svelte` 中渲染 tool call 时，检测 `read` 工具的参数

### 2. `parseSkillBlock` — 解析用户消息中的 `<skill>` 块

用户通过 `/skill:name` 命令调用 skill 时，消息内容包含 `<skill name="..." location="...">...</skill>` 格式的块。

**实现位置**: `transcript.ts` 中的 `contentBlocks()` 函数 ✅ 已实现

### 3. `SkillInvocationMessageComponent` — 渲染组件

显示 `图标 skill-name` 格式，只折叠显示。

**实现位置**: `SkillInvocationCard.svelte` ✅ 已实现

## 文件变更清单

| 文件 | 操作 | 状态 |
|------|------|------|
| `apps/dano/web/src/assets/skill-icon.png` | 新增 | ✅ |
| `apps/dano/web/src/assets/skill-icon.svg` | 新增 | ✅ |
| `apps/dano/web/src/utils/transcript.ts` | 添加 SkillContentBlock | ✅ |
| `apps/dano/web/src/components/SkillInvocationCard.svelte` | 新增 | ✅ |
| `apps/dano/web/src/components/ChatTranscript.svelte` | 集成渲染 + read 工具检测 | ✅ |

## 验证结果

1. `pnpm run check:web` — ✅ 0 errors
2. `pnpm run build:web` — ✅ 构建成功
3. 浏览器验证 — ✅ `read` 工具读取 SKILL.md 时显示为 `图标 curl-proxy`
