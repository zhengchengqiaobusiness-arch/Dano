# Dano 统一部署

发布包内含 `deploy.sh`、实例配置和环境配置示例、Compose/nginx 模板、部署说明、发布清单及校验值。离线包还包含 `images.tar`。应通过可信渠道取得整个版本包及其外部校验值。包内清单校验文件完整性。

## 环境要求

目标机需使用 Linux x86-64 或 ARM64，运行本机 Docker Engine 和 Docker Compose v2.24.4 或更新版本，安装 Python 3.8+。运行账户需能访问 Docker 并拥有 Deploy Control Directory、Runtime Data Directory 的父目录和 `/var/lock/dano-production-deploy.lock` 的写权限。Docker 应支持 Dano 所需的容器沙箱和用户命名空间。为两个发布版本的镜像、运行数据和上传配额留足磁盘空间。目标机可解析公开域名，并可访问已配置的 OA 和模型服务。离线镜像方式无需访问镜像仓库。

准备独立的 Compose 项目名、目标目录和公开端口。HTTPS 需要已配置的 DNS、有效证书及私钥，证书链须受客户端与运行镜像信任。HTTP 模式只适用于服务端 OAuth 策略允许的本地回调，面向用户的生产入口使用 HTTPS。

## 首次安装

1. 解压可信发布包。将 `instance.example.json` 和 `dano.env.example` 复制到包外同一目录，命名为 `instance.json` 和 `dano.env`，执行 `chmod 600 dano.env`。
2. 填写实例配置。选用未使用的项目名和目录，填入可从目标机和用户浏览器访问的 `baseUrl`。按曝光方式设置端口、证书和 OA 回调，准备现有 OA 客户端。设置模型提供商凭据。OA 回调入口通常是公开地址下的 `/api/auth/callback`，与 OA 端登记值一致。
3. 运行包内脚本。默认使用归档。联网时可指定 `--source registry`，已在 Docker 中准备并校验所需镜像时可指定 `--source local`。

```sh
./deploy.sh --verify-package
./deploy.sh --config /绝对路径/instance.json --source archive
```

实例配置字段如下。

| 字段 | 用途 |
| --- | --- |
| `project` | 必填，唯一 Compose 项目名。以小写字母或数字开头，可含小写字母、数字、下划线、连字符 |
| `controlDirectory` | 必填，绝对路径，存放部署配置、证书挂载定义、部署状态、回滚点和收据 |
| `runtimeDirectory` | 必填，独立的绝对路径，用于持久运行数据。Agent Config Directory 通过项目专属 Compose 卷持久化 |
| `baseUrl` | 必填，公开 HTTP(S) 源地址，用于健康和 HTTP/SSE 冒烟 |
| `exposure` | 必填，`https` 或 `http`，与公开地址协议一致 |
| `environmentFile` | 首次安装时读取的 Compose dotenv 文件，默认 `dano.env`，相对路径从实例配置所在目录解析 |
| `healthTimeoutSeconds` | 健康等待秒数，默认 180，范围 1–3600 |
| `existingComposeFiles` | 已有实例首次接入时填写，按运行时 Compose 顺序列出控制目录内的相对路径 |

`dano.env` 使用 Compose dotenv 格式。HTTPS 使用 `DANO_HTTPS_PORT`（默认 443）、`DANO_TLS_CERT_PATH` 和 `DANO_TLS_KEY_PATH`。HTTP 使用 `DANO_NGINX_PORT`（默认 80）。填写示例中所有必填 OA 变量。可用完整的 `DANO_OAUTH_CREDENTIAL_KEY` 和 `DANO_OAUTH_CREDENTIAL_KEY_VERSION` 复用已有密钥配对，两个值均未指定时由脚本生成并保存一次。将模型凭据填入部署环境文件的对应提供商变量。实例建立后可在受保护的控制目录 `.secrets` 下放置模型密钥文件，并按提供商要求设置 `*_API_KEY_FILE` 为容器内 `/run/secrets` 下的路径。

## 更新、重跑与回滚

在新版本发布包目录执行原命令，沿用实例配置。脚本依据已管理实例的镜像识别更新或重跑。已有生产实例通过 `existingComposeFiles` 接入，保持其实际的项目名、控制目录、运行目录以及公开入口，先验证当前 Dano 容器健康和配置归属。接入后继续使用同一份实例配置。

通过 `--environment /绝对路径/overrides.env` 可显式更改模型凭据和支持的应用设置。文件权限需为 0600。统一入口使用当前拓扑并保留端口、挂载、OAuth 配置和持久数据。显式回滚使用上一成功版本的镜像与记录的环境文件。保留该镜像及其回滚文件至确认无需恢复。

```sh
./deploy.sh --config /绝对路径/instance.json --source registry
./deploy.sh --config /绝对路径/instance.json --rollback
```

## 核验与故障恢复

成功命令返回零并打印收据路径。检查收据中的 `status`、`operation`、`runningImageId`、`version`、`baseUrl`、`health` 和 `smoke`，并访问实际服务。真实 OA 登录回跳和登录后对话在浏览器中独立核验。

首次启动失败时修正错误后用同一命令重试。更新切换或机器检查失败时脚本恢复旧服务。若回滚失败，保留控制目录、回滚文件和镜像，排除镜像缺失或运行环境故障后再次执行以恢复中断事务。若机器冒烟已通过但收据写入失败，检查控制目录可写性后重跑。服务日志可用 Docker 的 `docker logs` 查看相应容器，避免把原始生产日志或控制目录备份共享给他人。

## 发布维护者

在构建端准备与发布版本和提交匹配的 Dano 镜像与 nginx 镜像。填写镜像清单中的 Linux 架构、Dano 提交、两个各自的镜像 ID 和按摘要固定的 registry 引用。通过 `docker image inspect` 获取镜像 ID 和 `RepoDigests`，离线包用 `docker save` 保存上述两个镜像。使用与 Dano 同一版本的源码运行下列打包命令，将输出目录压缩分发。仅分发联网包时省略 `--archive`。

```sh
pnpm deploy:package --images release-images.json --archive images.tar --output dano-deployment
```
