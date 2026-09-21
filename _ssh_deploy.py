import paramiko, warnings, time
warnings.filterwarnings('ignore')

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('1.15.173.22', username='root', password='Zcq$$$2m..sd!#s1', timeout=15)

def run(cmd, timeout=30, show=True):
    """不用 PTY，避免 ANSI 控制码"""
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    if show:
        print(f"\n$ {cmd}")
        if out: print(out)
        if err: print(f"[stderr] {err}")
    return out

# ── 1. 新 tag：时间戳 ────────────────────────────────────────
new_tag = time.strftime('rel-%Y%m%d%H%M')   # e.g. rel-202609211135
ENV_FILE = '/opt/dano/deploy/.env'
BUILD_LOG = f'/tmp/dano-build-{new_tag}.log'

print(f"{'='*60}")
print(f"  新镜像 tag : dano-app:{new_tag}")
print(f"  构建日志   : {BUILD_LOG}")
print(f"{'='*60}")

# ── 2. 后台启动构建 ──────────────────────────────────────────
build_cmd = (
    f"cd /root/Dano-source && "
    f"nohup docker build "
    f"--build-arg NPM_REGISTRY=https://mirrors.cloud.tencent.com/npm/ "
    f"--progress=plain "
    f"-t dano-app:{new_tag} . "
    f"> {BUILD_LOG} 2>&1 & echo $!"
)
pid = run(build_cmd, timeout=15)
print(f"\n>>> 构建 PID: {pid}  （预计 5-15 分钟）")

# ── 3. 每 30s 轮询日志 ───────────────────────────────────────
for i in range(50):     # 最多等 25 分钟
    time.sleep(30)
    elapsed = (i + 1) * 30
    # 进程是否结束
    alive = run(f"kill -0 {pid} 2>/dev/null && echo alive || echo done", show=False)
    # 最新 5 行日志
    tail = run(f"tail -5 {BUILD_LOG} 2>/dev/null", show=False)
    print(f"\n[{elapsed}s] PID={pid} {alive}")
    print(tail)
    if alive == 'done':
        print(">>> 后台进程已结束")
        break

# ── 4. 确认镜像存在 ──────────────────────────────────────────
image_id = run(f"docker images -q dano-app:{new_tag}", timeout=10)
if not image_id:
    print(f"\n❌ 构建失败！最后 40 行日志：")
    print(run(f"tail -40 {BUILD_LOG}", timeout=10))
    client.close()
    raise SystemExit(1)
print(f"\n✅ 镜像构建成功: dano-app:{new_tag}  ID={image_id}")

# ── 5. 备份 .env & 更新 DANO_IMAGE ───────────────────────────
run(f"cp {ENV_FILE} {ENV_FILE}.bak-before-{new_tag}", timeout=10)
print(f"✅ .env 已备份: {ENV_FILE}.bak-before-{new_tag}")

run(f"sed -i 's|^DANO_IMAGE=.*|DANO_IMAGE=dano-app:{new_tag}|' {ENV_FILE}", timeout=10)
print("✅ DANO_IMAGE 已更新:")
print(run(f"grep DANO_IMAGE {ENV_FILE}", timeout=5))

# ── 6. 重启 app 容器 ─────────────────────────────────────────
print("\n>>> 重启 app 容器...")
print(run("cd /opt/dano/deploy && docker compose up -d app", timeout=60))

# ── 7. 等待健康检查 ──────────────────────────────────────────
print("\n>>> 等待健康...")
for i in range(18):     # 最多 3 分钟
    time.sleep(10)
    status = run("cd /opt/dano/deploy && docker compose ps app --format '{{.Status}}'", show=False)
    print(f"  [{(i+1)*10}s] {status}")
    if 'healthy' in status.lower():
        print("✅ 容器健康！")
        break

# ── 8. 最终状态 ──────────────────────────────────────────────
print("\n=== 容器状态 ===")
print(run("cd /opt/dano/deploy && docker compose ps", timeout=15))

# ── 9. 验证 Python 包 ─────────────────────────────────────────
print("\n=== 验证容器内 Python 包 ===")
verify = run(
    'docker exec $(docker ps -qf name=dano-app) '
    'python3 -c "'
    'import pandas, numpy, openpyxl, scipy, sklearn, matplotlib, seaborn, plotly, tabulate, httpx; '
    "print('ALL OK')"
    '"',
    timeout=30
)
if 'ALL OK' in verify:
    print("✅ 所有包导入成功")
else:
    print(f"⚠️ 验证结果:\n{verify}")

client.close()
print("\n=== 全部完成 ===")
