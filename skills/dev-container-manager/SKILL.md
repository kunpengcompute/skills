---
name: dev-container-manager
description: >-
  Manage Docker-based dev containers on remote Linux servers. Use this skill
  whenever the user wants to create, query, or delete a dev container on a
  remote host, or mentions phrases like "create a dev container", "setup a
  development environment on server X", "I need a container with N cores and M
  gigs of memory on IP", "check my dev containers", "list containers on
  server", "show resources on the remote box", "delete my dev container". This
  skill handles the full lifecycle: resource detection, NUMA-aware CPU
  allocation, SSH key generation, container creation with OpenEuler, and
  multi-user coexistence on shared servers. Trigger even if the user does not
  say "container" explicitly but describes allocating isolated CPU/memory on a
  remote Linux machine for development work.
---

# Dev Container Manager

Create, query, and manage Docker-based development containers on remote Linux
servers with NUMA-aware resource allocation and multi-user support.

## Core principles

- **Multi-user safe**: Every container is named `dev_container_<username>`.
  CPU ranges, memory allocations, and SSH ports are tracked per container so
  users never conflict.
- **NUMA-aware**: Consecutive CPU cores are always allocated from a single
  NUMA node to avoid cross-socket latency.
- **Key-based SSH only**: Password authentication is disabled. Each container
  gets a fresh ed25519 key pair; the private key stays in the current project
  directory.
- **Verify before create**: Always check remote resources first. If the
  server cannot satisfy the request, tell the user immediately with specifics
  (how many cores free, how much memory available, which NUMA nodes are
  options).

## Workflow: Creating a dev container

### Step 1: Gather requirements

From the user's natural-language request, extract:

| Parameter | Default | Notes |
|-----------|---------|-------|
| `server` | **required** | IP or hostname |
| `user` | `root` | SSH user for the remote host |
| `cores` | `16` | Must be consecutive, same NUMA node |
| `memory` | `32g` | In GiB |
| `image` | `openeuler/openeuler:24.03-lts-sp3` | Any Docker image |
| `username` | derived from local `$USER` or `whoami` | Used for container naming |
| `ssh_port` | auto-assigned from 2222 upward | Host port mapped to container:22 |

If any critical parameter is missing, ask the user. For obvious defaults
(cores, memory, image), assume the default and mention it in passing; only
stop to ask if the user's request is genuinely ambiguous.

### Step 2: SSH connection precheck

Before running any remote commands, verify that the agent can SSH to the
server silently (key-pair auth, no password prompt). Password-based SSH
does not work for automated/non-interactive use — the agent cannot type a
password.

**Check silently:**

```bash
ssh -o PasswordAuthentication=no -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new <user>@<server> 'echo ssh_ok' 2>&1
```

| Output | Meaning | Action |
|--------|---------|--------|
| `ssh_ok` | Key-pair auth works | Skip to Step 3. |
| `Permission denied (publickey)` | Server requires a key, local key not authorized | **Run setup script below.** |
| Other error / timeout | Network or server-config issue | Report the raw error and stop. |

**If key auth fails, present the user with ONE command:**

```
SSH 密钥认证未配置，请在终端执行以下命令完成配置（只需输入一次远程服务器密码）：

    bash <skill-path>/scripts/setup_ssh_key.sh <user>@<server>
```

The bundled script `scripts/setup_ssh_key.sh` handles the full flow:
local key check, key generation if needed, `ssh-copy-id` (or manual SCP
fallback), and final verification — with clear per-step output.

After the user confirms success, retry the silent check. If it still
fails, the script's output will already have diagnostics; report them.

**Edge case — both sides locked:** If `PasswordAuthentication` is
disabled on the server AND the key isn't authorized yet, the script
can't copy the key. Read the local public key (`cat ~/.ssh/id_ed25519.pub`)
and tell the user:

> 服务器已禁用密码登录，且本机公钥未授权。请联系管理员将以下公钥添加到服务器的 ~/.ssh/authorized_keys：
> ```
> <public key content>
> ```

### Step 3: Environment precheck

Before allocating resources, verify the Docker environment is healthy. Run
the bundled precheck script:

```bash
ssh <user>@<server> 'bash -s' < <skill-path>/scripts/env_precheck.sh
```

This outputs JSON with two sections:

#### 2a: Docker Hub accessibility

The script tests connectivity to `registry-1.docker.io`, `hub.docker.com`,
and `index.docker.io`. Two outcomes:

**Hub reachable** — No action needed, proceed to Step 4.

**Hub unreachable** — Check if registry mirrors already exist in
`/etc/docker/daemon.json`. If none, auto-configure mirrors:

1. Report to the user: "Docker Hub 不可访问，正在添加镜像加速器..."
2. Use the known working mirror list:
   - `https://docker.1ms.run`
   - `https://dockerproxy.net`
   - `https://proxy.vvvv.ee`
   - `https://dockerproxy.link`
3. Update daemon.json. If the file already has other config (like
   `data-root`), merge the mirrors into it — never overwrite existing keys.
   If the file doesn't exist, create it with just the mirrors.
4. Restart Docker: `systemctl restart docker` (or `service docker restart`).
5. Verify Docker is back up and retry the hub check. If still unreachable,
   warn the user but continue (the mirrors may still work for cached images).

Merge logic example:
```bash
ssh <user>@<server> 'python3 -c "
import json, os
cfg = {}
if os.path.exists(\"/etc/docker/daemon.json\"):
    with open(\"/etc/docker/daemon.json\") as f:
        cfg = json.load(f)
cfg[\"registry-mirrors\"] = [
    \"https://docker.1ms.run\",
    \"https://dockerproxy.net\",
    \"https://proxy.vvvv.ee\",
    \"https://dockerproxy.link\"
]
with open(\"/etc/docker/daemon.json\", \"w\") as f:
    json.dump(cfg, f, indent=2)
" && systemctl restart docker'
```

#### 2b: Disk space check

The script checks free space on three paths:

1. **docker_root** — Actual configured Docker data root (from `daemon.json`'s
   `data-root`, or default `/var/lib/docker`). This is where images and
   container layers live.
2. **var_lib_docker** — The default `/var/lib/docker` path. Always checked
   even when `data-root` is customized, because the system partition
   hosting this path may still be tight for Docker logs, temporary overlays,
   or fallback operations.
3. **tmp** — `/tmp`, used by container builds and image layer extraction.

Threshold: **128 GB**. If any of the three paths has less than 128 GB free:

1. Present a warning table:
   ```
   === 磁盘空间警告 ===
   Docker 数据目录:  /data0/docker_image  剩余 3478G (充足)
   /var/lib/docker:  / (系统根分区)       剩余 30G   (< 128G!)
   /tmp 临时目录:    /tmp                 剩余 251G  (充足)

   警告: /var/lib/docker 所在分区空间不足。
   推荐将 Docker 数据目录保持在 /data0，无需迁移。
   ```

   Note: if `docker_root` and `var_lib_docker` share the same mount point,
   merge them into one row to avoid confusion.

2. List alternative paths from the precheck output (mount points with
   >128 GB free). If only `var_lib_docker_low_space` is warned but
   `docker_root` already has ample space, the Docker data directory is
   already on a large partition — mention this and suggest no migration
   is needed for Docker, but note the system partition is tight for other
   uses.

3. If `docker_data_root_low_space` is in warnings, ask the user: "是否将
   Docker 数据目录迁移到 <推荐路径>？"

4. If user agrees to migrate, update the Docker data directory:
   ```bash
   ssh <user>@<server> 'python3 -c "
   import json, os
   cfg = {}
   if os.path.exists(\"/etc/docker/daemon.json\"):
       with open(\"/etc/docker/daemon.json\") as f:
           cfg = json.load(f)
   cfg[\"data-root\"] = \"<new_path>/docker_image\"
   with open(\"/etc/docker/daemon.json\", \"w\") as f:
       json.dump(cfg, f, indent=2)
   " && mkdir -p <new_path>/docker_image && systemctl restart docker'
   ```

5. If user declines, note the risk and continue (image pulls may fail).

If all three paths have >128 GB free, report "磁盘空间充足" and proceed.

### Step 4: Check remote resources

Run the bundled resource check script on the remote server. This collects:
CPU topology (NUMA nodes, core lists, free cores per node), total/free
memory, existing dev containers and their resource allocations, Docker
status, and used ports.

The script is at `scripts/check_resources.sh`. Read it, then pipe it to the
remote host:

```bash
ssh <user>@<server> 'bash -s' < <skill-path>/scripts/check_resources.sh
```

Present a summary table to the user:

```
=== 服务器资源: <server> ===
CPU: <model>, <total_cores> 核, <sockets> 路
内存: <total> total, <free> 可用
NUMA 节点:
  node0: CPU 0-63,  空闲核心 <N>,  空闲内存 <X>G
  node1: CPU 64-127, 空闲核心 <N>,  空闲内存 <X>G
  ...

=== 已有开发容器 ===
| 容器名 | 用户 | CPU | 内存 | 端口 | 状态 |
|--------|------|-----|------|------|------|
| dev_container_xxx | xxx | 0-15 | 32G | 2222 | running |
...
```

If resources are insufficient, report the shortfall clearly and stop. Example:
"该服务器仅有 8 个空闲核心（分布于不同 NUMA 节点），无法分配连续 16 核。"

### Step 5: Allocate resources

Pick resources that don't conflict with existing containers.

**CPU allocation strategy:**
1. List all NUMA nodes. For each node, determine which core ranges are
   already taken by existing `dev_container_*` containers.
2. Within the node with the most free cores, find a contiguous block of
   `cores` cores. Prefer higher-numbered cores (less likely to conflict with
   system processes). Example: if node0 has cores 0-63 and 0-31 are free,
   allocate 48-63 (the last 16) rather than 0-15.
3. If no single NUMA node has enough consecutive free cores, report which
   nodes have how many and let the user decide.

**Memory allocation strategy:**
- Check `free -h` output. If requested memory exceeds available, report it.
- Docker `--memory` flag provides hard limit; also set `--memory-swap` to
  the same value (no swap).

**Port allocation strategy:**
- Scan from 2222 upward. The first port not used by any existing container
  and not bound on the host wins.
- Command: `for port in $(seq 2222 2300); do ss -tlnp | grep -q ":$port " || { echo $port; break; }; done`

### Step 6: Generate SSH key pair

Generate locally in the current project directory:

```bash
ssh-keygen -t ed25519 -f dev_container_<username>_key -N "" -C "dev_container_<username>@<server>"
```

Store the key filename for the connection info output.

### Step 7: Create the container

Use the bundled `scripts/create_container.sh` script. Read it first, then
customize and pipe it to the remote host. The script handles:

1. Checking if the image exists locally; only pulling if absent
2. Creating the container with `--cpuset-cpus`, `--memory`, `--memory-swap`,
   `--cpus`, `--hostname`, `--restart=unless-stopped`, port mapping
3. Starting the container
4. Checking if `sshd` is already present; installing `openssh-server` only if missing
5. Optionally installing dev tools (gcc, g++, python3, git, vim, make, cmake,
   golang) — controlled by `INSTALL_TOOLS=true`. Default is to skip dev tools.
   Each tool is checked individually and only installed if missing.
6. Injecting the public key into `/root/.ssh/authorized_keys`
7. Configuring sshd (key-only auth, no passwords)
8. Committing the container as `dev-<username>:latest` with an entrypoint
   script that auto-starts sshd
9. Recreating the container from the committed image

The script accepts environment variables for all parameters:

```bash
ssh <user>@<server> 'CONTAINER_NAME=dev_container_<username> \
  IMAGE=openeuler/openeuler:24.03-lts-sp3 \
  CPUSET_CPUS=48-63 \
  MEMORY=32g \
  HOST_PORT=2222 \
  USERNAME=<username> \
  PUBKEY="<public-key-content>" \
  INSTALL_TOOLS=true \
  bash -s' < <skill-path>/scripts/create_container.sh
```

After container creation, ask the user if they want to install dev tools.
If yes, run the script again with `INSTALL_TOOLS=true` (it will skip steps
that are already done and only install missing tools).

If the image is openeuler-based, installs via `dnf`.
If ubuntu/debian-based, via `apt-get`. Auto-detected.

### Step 8: Report connection info

After creation succeeds, present the user with a clear summary:

```
=== 开发容器已就绪 ===
SSH 连接: ssh -i "$(pwd)/dev_container_<username>_key" -p <port> root@<server>
主机名:   dev-container-<username>
CPU:      <range> (独占 <N> 核, NUMA node <X>)
内存:     <M>G
镜像:     <image>
私钥:     ./dev_container_<username>_key
```

Also mention: "可使用 '查看开发容器' 随时查询容器状态。"

## Workflow: Querying containers

When the user asks to see existing containers ("查看开发容器", "list dev
containers", "what containers are on server X", "show my containers"):

```bash
ssh <user>@<server> 'bash -s' < <skill-path>/scripts/list_containers.sh
```

Present results as a table:

```
=== 开发容器列表: <server> ===
| 容器名 | 用户 | CPU 范围 | NUMA节点 | 内存 | 端口 | 状态 | 镜像 |
|--------|------|----------|----------|------|------|------|------|
| dev_container_xxx | xxx | 48-63 | node0 | 32G | 2222 | Up | dev-xxx:latest |
...

服务器资源总览:
  CPU: 16/256 已分配
  内存: 32G/502G 已分配
```

For a single user's container, also show the SSH connection command using the
absolute path to the key file, wrapped in quotes:

```
ssh -i "$(pwd)/dev_container_<username>_key" -p <port> root@<server>
```

This format works when copy-pasted from any directory — the `$(pwd)` captures
the current working directory at the time the command was generated.

## Workflow: Deleting a container

When the user asks to delete a container:

1. Identify the container by username or container name
2. Stop and remove it: `docker rm -f dev_container_<username>`
3. Remove the committed image: `docker rmi dev-<username>:latest`
4. Confirm deletion and note that the local private key file still exists
   (user can delete it manually if desired)

Ask for confirmation before deleting unless the user's intent is unambiguous.

## Multi-user coexistence rules

1. **Naming**: Every container is `dev_container_<username>`. The committed
   image is `dev-<username>:latest`. Entrypoint script is
   `/entrypoint_<username>.sh`.
2. **CPU**: Ranges must not overlap. Always compute free ranges by
   subtracting all existing `--cpuset-cpus` allocations from each NUMA node's
   full range.
3. **Memory**: Sum of all `--memory` allocations must not exceed host total.
   Leave at least 4 GiB for the host.
4. **Ports**: Each container gets a unique host port. Track all currently
   mapped ports from `docker ps` output.
5. **Keys**: Key filenames include the username:
   `dev_container_<username>_key` and `dev_container_<username>_key.pub`.
   This prevents overwrites when managing multiple containers from the same
   local directory.

## Resource allocation reference

### NUMA node core range calculation

Given NUMA node output like `node 0 cpus: 0 1 2 ... 63` and existing
container CPU bindings, compute free cores per node:

- Parse each NUMA node's CPU list into a set
- Parse each container's `--cpuset-cpus` range into a set
- Subtract allocated cores from each node's set
- Find consecutive runs in the remaining set

A run of N consecutive integers is valid for an N-core request.

### Port allocation reference

Reserved ports to skip: 0-1023 (system), 2375-2376 (Docker API), 3306
(MySQL), 5432 (PostgreSQL), 6379 (Redis), 8000-9000 (common web services).

Start scanning from 2222; cap at 65535. If no port is available below 10000,
warn the user.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Docker Hub unreachable | Network restriction / firewall | Auto-add registry mirrors via `env_precheck.sh`; if mirrors also fail, user may need proxy configured in `~/.docker/config.json` |
| Docker pull timeout | Mirror not responding | Try removing failing mirrors from `daemon.json` and restart Docker; or add additional mirrors |
| Disk space low (< 128G) | Default `/var/lib/docker` on small system disk | Run disk check from `env_precheck.sh`; migrate `data-root` to larger partition |
| Container exits immediately | `/sbin/init` missing | Use `tail -f /dev/null` or `sleep infinity` as init; commit with entrypoint script |
| sshd not running after restart | No auto-start mechanism | Commit container with entrypoint script that launches sshd before the idle loop |
| `Permission denied (publickey)` | Public key not in authorized_keys or wrong key | Verify key injection via `docker exec`; check `~/.ssh/authorized_keys` permissions (600) and `~/.ssh/` (700) |
| `Cannot allocate memory` | Memory limit too low for dnf/apt | Install tools before setting hard memory limit, or temporarily raise limit during setup |
| Port already in use | Another container or host service | Pick next available port |
| NUMA node has gaps in free cores | Other containers fragmenting the node | Try allocating from a less-used NUMA node, or report to user |
| Docker fails to start after daemon.json change | JSON syntax error or path doesn't exist | Validate JSON with `python3 -m json.tool /etc/docker/daemon.json`; ensure data-root path exists and is writable |

## Bundled scripts

Four scripts live under `scripts/`. Read them before use so you understand
their inputs/outputs. Do not blindly pipe them — customize the environment
variables first.

- `env_precheck.sh` — Runs on remote host. Checks Docker Hub accessibility,
  registry mirror configuration, and disk space. Outputs JSON. Run this
  before any container creation.
- `check_resources.sh` — Runs on remote host. Collects CPU/NUMA/memory/port
  info and existing container allocations. Outputs JSON for parsing.
- `create_container.sh` — Runs on remote host. Expects env vars:
  `CONTAINER_NAME`, `IMAGE`, `CPUSET_CPUS`, `MEMORY`, `HOST_PORT`,
  `USERNAME`, `PUBKEY`. Optional: `INSTALL_TOOLS=true` to install dev tools.
  Handles the full create-install-configure-commit pipeline. Skips image pull,
  sshd install, and dev tool install if already present.
- `list_containers.sh` — Runs on remote host. Outputs formatted table of all
  `dev_container_*` containers with resource details and SSH connection
  commands.
