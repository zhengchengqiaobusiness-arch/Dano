# 测试环境与入口

## 本机开发

```sh
pnpm test:prepare
pnpm test
# 仅验证相关文件或用例
pnpm test apps/dano/src/bridge/__tests__/provider-python.test.ts
```

`test:prepare` 用当前 `python3` 创建独立 venv（默认位于系统临时目录的
`dano-test-python-<uid>`，可通过 `DANO_TEST_PYTHON_DIR` 指定绝对目录），只安装
测试需要的 httpx 及其依赖。httpx 版本直接读取 `deploy/python-requirements.txt`，
与生产镜像保持一致，不重复维护版本，不向系统 Python 安装包。

`pnpm test` 先检查 Python/httpx，再启动 Vitest。优先使用准备好的 venv，
系统 Python 已满足依赖时也可复用。缺少依赖时在测试开始前退出，并给出准备命令，
不会把六个缺依赖用例逐个跑成失败，也不会静默跳过这些用例。

每个测试文件使用自己的临时 `PI_CODING_AGENT_DIR`，结束后清理。
测试不读取开发者的全局 Pi 包、技能或认证配置，避免意外 npm 下载及清理竞态。
有意验证配置加载的测试仍可在自身 fixture 中显式设置该变量。

## Linux 容器

在装有项目源码、Node/pnpm 开发依赖以及 Dano 运行依赖的 Linux Docker/Podman
测试容器内执行（源码和测试依赖必须与被测提交匹配）：

```sh
pnpm test:container
```

该命令检查 Linux 容器标记、Python/httpx 和 bubblewrap，然后运行可移植测试及
`*.container.test.ts`。后者实际执行 Linux namespace/bubblewrap；本机 profile
不会收集这些文件。`pnpm test` 也会识别容器环境并自动选择 container profile。
日志会明确输出 profile、依赖准备情况和是否包含容器专属测试。
不要仅为了通过预检而在宿主机伪造容器标记或关闭 sandbox 保护。

环境要求按能力分类，而不是按曾经失败与否分类：

| 测试 | 运行条件 |
| --- | --- |
| 纯逻辑、组件、HTTP fixture、Compose 命令模拟 | 本机或容器；无需真实 OA/生产服务 |
| Python/urllib/httpx fixture | 两种环境都支持；独立 Python 依赖预检 |
| 不支持平台的 fail-closed 行为 | 非 Linux；Vitest runIf 明确标记，不用提前 return 假装通过 |
| `*.container.test.ts` 真实 bubblewrap | Linux 容器、运行镜像依赖与所需 namespace 权限 |
| 真实部署冒烟及浏览器验收 | 部署目标服务；单独按部署工作流执行，不由 pnpm test 访问生产服务 |

Compose 发布 fixture 会串行启动多个 Node/Git 子进程，单用例预算为 30 秒；
普通逻辑测试继续使用 Vitest 默认超时，不因部署 fixture 放宽全局标准。

可移植测试失败仍需定位断言或实现；容器环境不保证能消除代码错误。
