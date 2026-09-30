# #495：记忆权限隔离与生产沙箱兼容性

## 调用链与信任边界

HTTP 身份解析建立 User Context，服务端凭据代理和记忆服务根据该身份绑定 owner。
浏览器、模型参数、Client ID 和工作区路径不能选择其他 owner。记忆正文在 OpenViking，
管理密钥、用户凭据、投递与恢复状态在 HTTP 宿主的私有目录。

容器 root supervisor 只负责受保护安装、目录与 UID 分配、进程生命周期和限额 IPC。
HTTP 宿主以非 root UID 运行并保留 `no_new_privs=1`。每个 User 的工具进程使用不同 UID/GID，
环境只有显式允许的工具变量，没有模型或记忆凭据。宿主与其他 User 的目录依靠 Linux DAC
隔离；容器 procfs 使用 `hidepid=2`，工具进程不属于豁免组。

工具进程仅运行镜像内固定 Pi/Heimdall 代码。关闭工作区扩展、Skill、prompt 与主题发现，
不提供任意模块加载或宿主命令 RPC。`.pi` 与 `.pi/agent` 由宿主拥有，工作区 sticky bit
阻止替换该目录；因此 Pi 优先查找的 `agent/bin/rg`、`fd` 也不能被模型植入。
宿主侧扩展及依赖仍属于可信计算基，不宣称可安全运行任意恶意宿主扩展。

模型的 read/write/edit/grep/find/ls 经 Heimdall hook 后由独立 UID 执行。
模型 bash 与 user_bash 都使用 Heimdall 注册的 Bubblewrap operations。模型执行的代码
只进入该沙箱，不在可信工具进程中执行。记忆操作留在宿主，用 owner-bound API 执行，
不能由文件权限替代其服务端授权。

## 比较与选择

| 方案 | 私有文件与跨用户文件 | 生产 bash | 结论 |
| --- | --- | --- | --- |
| 同 UID，只复用 Heimdall 路径策略 | 0.2.17 拦截绝对路径，但工作区符号链接绕过普通路径匹配；原生文件工具在宿主权限下读取目标 | 旧 setuid 模式可运行 | 拒绝。单补 realpath 仍需处理检查与使用之间的竞争，并不能保护任意同进程扩展 |
| 原 supervisor → broker → worker，全链提前设置 no_new_privs | UID、私有目录和 procfs 边界保留 | 当前主机失败，即使 Heimdall 的 userNamespace=false | 不作为当前部署方案；需要另行支持并授权的宿主 namespace 条件 |
| supervisor → 固定工具进程 → Heimdall/setuid bwrap | 保留每用户 UID、宿主私有目录、不可变工具配置与 owner 授权 | 在 bwrap 获取设置沙箱所需权限后，由 bwrap 设置 no_new_privs 再执行模型命令 | 采用。去掉中间 broker 进程及重复 IPC，不引入自制特权 shell 执行器 |

新工具进程是可信启动器，其 `NoNewPrivs=0` 不是给予模型 Shell 的权限。
Bubblewrap 自身完成提权、挂载、降权与 no_new_privs；模型命令实际运行时必须是分配的
非 root UID/GID、零 capabilities、`NoNewPrivs=1`，且看不到容器进程树。
这些条件由每次工具进程启动时调用**真实 Heimdall bash**检查，失败不会发出 ready。

成熟实现依据：[Bubblewrap 0.8.0 源码](https://github.com/containers/bubblewrap/blob/v0.8.0/bubblewrap.c)
的 `acquire_privs` 与 main：先取得 setuid 权限，再调用 PR_SET_NO_NEW_PRIVS；当既无特权又是
非 root 时会自动启用 user namespace。这解释了为什么没有显式 `--unshare-user` 也会失败。
本方案复用该实现，不编写 setuid helper，也不把 namespace 探针作为实际工具验收。

## 复现与当前证据

2026-09-30，在生产主机的临时 Docker 容器中使用现存 0.2.69 镜像、Pi 0.85.1、
Heimdall 0.2.17。未挂载生产 runtime，未修改宿主 sysctl。

- 生产旧容器：UID/GID 1000，CapEff/CapPrm/CapAmb=0，NoNewPrivs=0；bwrap root:root 4755；
  Compose CapAdd=ALL、seccomp=unconfined；宿主 `user.max_user_namespaces=0`。
- 同镜像真实注册 bash、userNamespace=false、UID/GID 10001、零有效 capabilities：
  NoNewPrivs=1 时返回 `Creating new namespace failed ... bwrap must be installed setuid`；
  仅移除提前设置的 no_new_privs 后返回预期标记。
- `fixtures/heimdall-memory-boundary.mjs`：绝对路径 blocked=true/exposed=false；
  symlink blocked=false/exposed=true。全部内容为临时合成标记。
- 修复构建产物的 `sandbox-preflight.js` 已在同一生产主机的隔离镜像运行：普通 bash、
  命令内 no_new_privs/零 capabilities、宿主私有文件及跨用户绝对/符号链接读写编辑拒绝通过。

## 部署前置条件与发布检查

支持现有 Linux Docker 的 setuid Bubblewrap 路径，包括 user.max_user_namespaces=0。
需要镜像提供 root-owned 4755 bwrap、setpriv、Python，容器提供现有 Compose 的 namespace/mount
权限与 procfs hidepid 能力。不能给整个容器预设 no-new-privileges，也不能把 bwrap 所在挂载设为
nosuid 后继续宣称该配置可用。没有静默降级到无沙箱 Shell 的路径。

`deploy:release` 在配置检查后、SYSTEM 同步与切换服务前，以同一 Compose 配置运行
`node ./dist/server/bridge/sandbox-preflight.js`。它在容器 /tmp 创建两个合成 User，执行真实工具
和文件攻击检查，清理自己的临时目录；不会使用或改写生产会话、记忆和身份注册表。
每个实际 worker 还会对其真正的 Runtime Workspace 做启动检查，覆盖挂载差异。

管理员若选择其他 namespace/capability 配置，必须重新运行这些检查。任何宿主全局内核配置
变更都需要单独授权；本次没有变更。进程隔离不能防御内核或 Bubblewrap 自身漏洞，受保护
安装中的任意原生代码执行漏洞也不在路径 hook 能证明的范围内。

## 验收记录

- 本地 `pnpm run check`：通过，Svelte 0 errors / 0 warnings。
- 空白 PI_CODING_AGENT_DIR 下 `pnpm test -- --maxWorkers=4`：150 files，1774 passed，1 skipped。
  首轮受全局 Pi 扩展影响的超时和新增发布步骤断言失败均保留在工作日志；发布测试明确清空
  PI_CODING_AGENT_DIR，以免外部配置改变其被测默认路径。
- `pnpm run build` 与真实分块构建产物入口检查：通过。
- HTTPS 隔离站点 `smoke:deploy`：页面、健康检查、匿名会话和 SSE 真实响应通过；不替代浏览器验收。
- 生产主机隔离 Compose 的发布前检查通过；对同一镜像强制
  `no-new-privileges` 后检查按预期失败，不能继续切换服务。
- 实际 OpenViking 合成 Alice/Bob：Bob 读取、改写和删除 Alice 记忆均返回 403；
  Alice 原内容不变。复现入口为 `fixtures/openviking-user-boundary.py`。
- 内置浏览器通过 HTTPS 隧道访问隔离站点：OAuth 登录、文本聊天、模型调用
  `bash ls`、保存合成偏好、另一会话召回、管理界面精确纠正及另一会话召回纠正内容通过。
  截图见 `evidence/issue495/recall.png` 和 `corrected-recall.png`。
- 首次保存因隔离配置继承的重试上限 5 而进入 `MEMORY_RECONCILIATION_LIMIT`；
  仅将隔离配置上限调整为 90 后自动恢复。模型纠正曾返回 `MEMORY_TARGET_NOT_FOUND` /
  `MEMORY_SOURCE_MISMATCH`；上述纠正通过结论只适用于精确原文管理界面，不能替代模型纠正。
- 2026-09-30 用户确认后，通过管理界面遗忘合成标题；新会话查询明确回复没有该标题记录，
  未召回纠正前后标题。截图见 `evidence/issue495/forgotten-recall.jpg`。
- 同次授权后实际上传无敏感合成图片，模型读取附件并正确描述蓝色背景、黄色圆形，
  截图见 `evidence/issue495/image-read.jpg`。浏览器点击未触发文件选择器，改用同一
  按钮的键盘激活后成功；只上传一次，未用 API 上传替代浏览器路径。

隔离镜像在已有 `dano-app:4e502f749b17` 上复制本次编译产物和产品版本，依赖保持不变；
这不是重新下载依赖的全量镜像构建证明。首次启动另发现 workspaces 卷顶层属于 root，
本次将其顶层初始化为 HTTP 宿主身份，未递归更改任何用户工作区。
生产 `dano-app-1` 未替换，生产记忆、会话、TLS、nginx 和相邻服务未切换。
验收后已删除 `dano495` 的三个测试容器、三个独立卷、独立网络、两个临时镜像标签及
`/tmp/dano495-acceptance`，停止本地 SSH 隧道并清理本地上传素材和配置副本。
生产应用容器 ID、镜像及 StartedAt 与清理前一致，健康检查正常；持久 localhost 证书保留。

本次不改变 ask_user_question 的能力、参数、提示或结果投影，无需更新其 Skill 生成器指南。

## 后续界面调整

按用户要求隐藏左上角菜单的记忆管理入口：App 不再向 AppHeader 传入可选回调。
本地开发服务器验证菜单只显示主题色及账号入口；`check` 和 AppHeader 的 8 项既有测试通过。
截图见 `evidence/issue495/menu-memory-hidden.jpg`。验证后已停止开发服务并删除本轮临时 runtime。
