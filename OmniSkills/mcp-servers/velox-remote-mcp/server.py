"""
MCP server: SSH to a host and run Velox / Gluten+Velox compile scripts.

Configure via env (与 spark_remote_mcp 同源)：
  SSH_HOST、SSH_USER、SSH_PASSWORD、SSH_PORT、SSH_IDENTITY、SSH_TIMEOUT_SECONDS
  REMOTE_WORKDIR              远端脚本所在目录（默认 /path/to/incubator-gluten/dev）
  COMPILE_VELOX_SCRIPT        Velox 编译脚本文件名（默认 compile_velox.sh，相对 REMOTE_WORKDIR）
  COMPILE_GLUTEN_VELOX_SCRIPT Velox+Gluten 编译脚本文件名（默认 compile_gluten_velox.sh）
  COMPILE_VELOX_TIMEOUT_SECONDS / COMPILE_GLUTEN_VELOX_TIMEOUT_SECONDS
                              未设置时取 max(7200, SSH_TIMEOUT_SECONDS) 秒
  COMPILE_LOG_DIR             本机日志目录（默认 velox_remote_mcp/compile_logs/）
  E2E_VELOX_SCRIPT            远端 spark-sql 启动脚本（默认 /path/to/run_exec_velox.sh，
                              脚本本身不接受 SQL 参数，MCP 端从 stdin 喂 SQL）
  E2E_VELOX_TIMEOUT_SECONDS   e2e 默认超时秒数（未设置时由调用方 timeout_sec 决定，最低 600s）

若设置 SSH_PASSWORD，远程命令走 Paramiko；否则走系统 ssh（需配 SSH_IDENTITY）。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import posixpath
import re
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path

import paramiko
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "velox-remote-test",
    instructions=(
        "在配置的远程 Linux 主机上通过 SSH 编译 Velox / Gluten+Velox。"
        " 【工具】"
        " compile_velox：仅编译 velox（runs compile_velox.sh <git_repo_url> <branch>）；"
        " compile_gluten_velox：编译 velox + gluten 集成版本（runs compile_gluten_velox.sh <git_repo_url> <branch>）；"
        " run_e2e_sql_velox：在远端用 Gluten+Velox 后端跑 SQL，返回查询结果（用于正确性校验）；"
        " get_compile_log：读最新一次编译日志末尾若干行；"
        " velox_ssh_check：检查 SSH 连通性；"
        " read_remote_file：读远端文件末尾若干行（默认 100）。"
        " 可设置 SSH_PASSWORD 或 SSH_IDENTITY。"
    ),
)

logging.getLogger("paramiko").setLevel(logging.WARNING)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _remote_workdir() -> str:
    return _env("REMOTE_WORKDIR", "/path/to/incubator-gluten/dev")


def _resolve_remote_script(env_name: str, default_basename: str) -> tuple[str, str]:
    """返回 (远端目录, 脚本文件名)。
    若 env 值是绝对路径则按其目录/文件名拆分，否则按 REMOTE_WORKDIR + basename 处理。
    """
    raw = _env(env_name, default_basename)
    if not raw:
        raise ValueError(f"{env_name} 不能为空")
    if raw.startswith("/"):
        d = posixpath.dirname(raw) or "/"
        b = posixpath.basename(raw)
        if not b:
            raise ValueError(f"{env_name} 缺少文件名")
        return d, b
    return _remote_workdir(), raw


def _build_compile_velox_cmd(git_repo_url: str, branch: str) -> str:
    d, script = _resolve_remote_script("COMPILE_VELOX_SCRIPT", "compile_velox.sh")
    return (
        f"cd {shlex.quote(d)} && bash {shlex.quote('./' + script)} "
        f"{shlex.quote(git_repo_url)} {shlex.quote(branch)}"
    )


def _remote_velox_home() -> str:
    return _env(
        "REMOTE_VELOX_HOME",
        "/path/to/incubator-gluten/ep/build-velox/build/velox_ep",
    )


def _remote_velox_compile_config() -> str:
    return _env(
        "VELOX_COMPILE_CONFIG",
        "/path/to/incubator-gluten/dev/velox_compile_config.sh",
    )


def _velox_ut_jobs_expr() -> str:
    return '${VELOX_UT_JOBS:-$(nproc)}'


def _build_velox_full_ut_cmd(build_dir: str | None = None) -> str:
    velox_home = _remote_velox_home()
    compile_config = _remote_velox_compile_config()
    build_dir = (build_dir or _env("VELOX_UT_BUILD_DIR", "_build/release")).strip()
    if not build_dir:
        build_dir = "_build/release"

    jobs = _velox_ut_jobs_expr()
    return "\n".join(
        [
            "bash -lc " + shlex.quote(
                "\n".join(
                    [
                        "set -euo pipefail",
                        f"cd {shlex.quote(velox_home)}",
                        "mkdir -p _build/include",
                        (
                            "if [ -L _build/include/re2 ] || "
                            "[ ! -e _build/include/re2 ]; then "
                            "ln -sfn /path/to/re2-src/re2 _build/include/re2; fi"
                        ),
                        'export CXXFLAGS="${CXXFLAGS:-}"',
                        f"source {shlex.quote(compile_config)}",
                        f"cmake -S . -B {shlex.quote(build_dir)} -GNinja "
                        "-DCMAKE_BUILD_TYPE=Release "
                        "-DCMAKE_CXX_FLAGS='-Wno-missing-field-initializers' "
                        "-DVELOX_ENABLE_PARQUET=ON "
                        "-DVELOX_BUILD_TESTING=ON "
                        "-DVELOX_BUILD_TEST_UTILS=ON "
                        "-DVELOX_ENABLE_DUCKDB=ON "
                        "-DVELOX_MONO_LIBRARY=ON "
                        "-DVELOX_BUILD_RUNNER=OFF "
                        "-DVELOX_ENABLE_HDFS=ON",
                        f"cmake --build {shlex.quote(build_dir)} -j {jobs}",
                        f"cd {shlex.quote(build_dir)}",
                        f"ctest -j {jobs} --output-on-failure",
                    ]
                )
            )
        ]
    )


def _build_run_velox_ut_cmd(build_dir: str, test_regex: str) -> str:
    velox_home = _remote_velox_home()
    build_dir = (build_dir or _env("VELOX_UT_BUILD_DIR", "_build/release")).strip()
    if not build_dir:
        build_dir = "_build/release"

    jobs = _velox_ut_jobs_expr()
    ctest_cmd = f"ctest -j {jobs} --output-on-failure"
    if test_regex.strip():
        ctest_cmd += f" -R {shlex.quote(test_regex.strip())}"

    return "bash -lc " + shlex.quote(
        "\n".join(
            [
                "set -euo pipefail",
                f"cd {shlex.quote(velox_home)}",
                f"cd {shlex.quote(build_dir)}",
                ctest_cmd,
            ]
        )
    )


def _build_compile_gluten_velox_cmd(git_repo_url: str, branch: str) -> str:
    d, script = _resolve_remote_script(
        "COMPILE_GLUTEN_VELOX_SCRIPT", "compile_gluten_velox.sh"
    )
    return (
        f"cd {shlex.quote(d)} && bash {shlex.quote('./' + script)} "
        f"{shlex.quote(git_repo_url)} {shlex.quote(branch)}"
    )


def _ssh_argv(remote_cmd: str) -> list[str]:
    host = _env("SSH_HOST")
    if not host:
        raise ValueError("SSH_HOST 未配置：请通过环境变量设置远端主机地址。")
    user = _env("SSH_USER")
    port = _env("SSH_PORT", "22")
    identity = _env("SSH_IDENTITY")

    target = f"{user}@{host}" if user else host
    argv: list[str] = [
        "ssh",
        "-p", port,
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
    ]
    if identity:
        argv.extend(["-i", identity])
    argv.extend([target, "bash", "-lc", remote_cmd])
    return argv


def _paramiko_connect() -> paramiko.SSHClient:
    host = _env("SSH_HOST")
    if not host:
        raise ValueError("SSH_HOST 未配置：请通过环境变量设置远端主机地址。")
    user = _env("SSH_USER", "root")
    password = _env("SSH_PASSWORD")
    port_s = _env("SSH_PORT", "22") or "22"
    port = int(port_s)
    identity = _env("SSH_IDENTITY")
    timeout = int(_env("SSH_TIMEOUT_SECONDS", "3600") or "3600")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kw: dict = {
        "hostname": host,
        "port": port,
        "username": user,
        "timeout": min(60, max(10, timeout)),
        "banner_timeout": 30,
        "auth_timeout": 30,
    }
    if password:
        kw["password"] = password
    if identity:
        kw["key_filename"] = identity
    client.connect(**kw)
    return client


def _run_via_paramiko_password(remote_cmd: str) -> tuple[str, str, int, str]:
    host = _env("SSH_HOST")
    user = _env("SSH_USER", "root")
    port_s = _env("SSH_PORT", "22") or "22"
    port = int(port_s)
    meta = f"paramiko {user}@{host}:{port} bash -lc <remote>"

    client = _paramiko_connect()
    try:
        wrapped = f"bash -lc {shlex.quote(remote_cmd)}"
        stdin, stdout, stderr = client.exec_command(wrapped, get_pty=True)
        stdin.close()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        return out, err, code, meta
    finally:
        client.close()


def _default_compile_log_dir() -> str:
    return _env("COMPILE_LOG_DIR") or str(
        Path(__file__).resolve().parent / "compile_logs"
    )


def _new_compile_log_path(label: str) -> str:
    log_dir = _default_compile_log_dir()
    os.makedirs(log_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = os.path.join(log_dir, f"{label}_{stamp}.log")
    pointer = os.path.join(log_dir, "latest.log")
    try:
        with open(pointer, "w", encoding="utf-8") as f:
            f.write(log_path)
    except OSError:
        pass
    return log_path


def _run_via_paramiko_streaming(
    remote_cmd: str, log_path: str, timeout_sec: int
) -> tuple[str, int]:
    """Paramiko 流式执行：与 client.exec_command 同路径，保证远端 stdout 完整收齐。

    早期版本用 `chan.exit_status_ready()` + 单次 drain 在 spark-sql 这类长命令上会
    丢掉尾部 stdout。现在直接复用 `client.exec_command(get_pty=True)`，与同步路径
    `_run_via_paramiko_password` 完全一致的读取方式（stdout 是 paramiko ChannelFile），
    在主线程边读边写日志、边追加超时检测，最后阻塞拿 exit code。
    """
    client = _paramiko_connect()
    try:
        wrapped = f"bash -lc {shlex.quote(remote_cmd)}"
        stdin, stdout, stderr = client.exec_command(wrapped, get_pty=True)
        # PTY 下 stderr 已合到 stdout；我们仍写入 stdin EOF
        try:
            stdin.close()
        except Exception:
            pass

        chan = stdout.channel
        buf: list[str] = []
        deadline = time.monotonic() + timeout_sec
        timed_out = False
        with open(log_path, "w", encoding="utf-8", errors="replace") as f:
            # chunk read：与 _run_via_paramiko_password 的 stdout.read() 同一 API，
            # 阻塞读直到 channel close。spark-sql 的 prompt 没有 \n，readline 模式会
            # 错误触发 EOF；chunk 模式只有真 channel close 才返回空串。
            while True:
                chunk = stdout.read(8192)
                if not chunk:
                    break
                text = chunk if isinstance(chunk, str) else chunk.decode(
                    "utf-8", errors="replace"
                )
                buf.append(text)
                f.write(text)
                f.flush()
                if time.monotonic() > deadline:
                    timed_out = True
                    f.write(f"\n[MCP] 超时（{timeout_sec}s），强制断开。\n")
                    try:
                        chan.close()
                    except Exception:
                        pass
                    break

        try:
            code = chan.recv_exit_status()
        except Exception:
            code = -1
        if timed_out and code == -1:
            code = 124
        return "".join(buf), code
    finally:
        client.close()


def _format_run_result(proc_stdout: str, proc_stderr: str, code: int) -> str:
    out: list[str] = []
    if proc_stdout.strip():
        out.append("--- stdout ---\n" + proc_stdout.rstrip())
    if proc_stderr.strip():
        out.append("--- stderr ---\n" + proc_stderr.rstrip())
    out.append(f"--- exit code ---\n{code}")
    if out:
        return "\n\n".join(out)
    return f"(无输出)\n\n--- exit code ---\n{code}"


def _run_remote_with_exit(
    remote_cmd: str, *, timeout_sec: int | None = None
) -> tuple[int | None, str]:
    timeout = (
        timeout_sec
        if timeout_sec is not None
        else int(_env("SSH_TIMEOUT_SECONDS", "3600") or "3600")
    )

    if _env("SSH_PASSWORD"):
        meta = (
            f"paramiko {_env('SSH_USER', 'root')}@{_env('SSH_HOST')}:"
            f"{_env('SSH_PORT', '22') or '22'} bash -lc <remote>"
        )
        try:
            proc_stdout, proc_stderr, code, _ = _run_via_paramiko_password(remote_cmd)
        except paramiko.AuthenticationException:
            return None, f"SSH 认证失败（用户名或密码错误）。\n{meta}"
        except paramiko.SSHException as e:
            return None, f"SSH 错误: {e}\n{meta}"
        except OSError as e:
            return None, f"网络或连接错误: {e}\n{meta}"
        except Exception as e:
            return None, f"执行失败: {e}\n{meta}"
        return code, _format_run_result(proc_stdout, proc_stderr, code)

    argv = _ssh_argv(remote_cmd)
    meta = f"$ {' '.join(shlex.quote(a) for a in argv[:6])} ... bash -lc <remote>"

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, f"SSH 执行超时（{timeout} 秒）。\n命令概要: {meta}"
    except FileNotFoundError:
        return None, (
            "未找到 ssh 可执行文件。请在 Windows「可选功能」中安装 OpenSSH 客户端，"
            "或确保 ssh 在 PATH 中。\n"
            f"命令概要: {meta}"
        )
    except Exception as e:
        return None, f"执行失败: {e}\n命令概要: {meta}"

    return proc.returncode, _format_run_result(
        proc.stdout or "", proc.stderr or "", proc.returncode
    )


def _run_remote(remote_cmd: str, *, timeout_sec: int | None = None) -> str:
    _code, text = _run_remote_with_exit(remote_cmd, timeout_sec=timeout_sec)
    return text


def _run_remote_with_exit_and_log(
    remote_cmd: str, log_path: str, timeout_sec: int
) -> tuple[int | None, str]:
    """执行远端命令，将输出实时流式写入 log_path，返回 (exit_code, formatted_text)。"""
    if _env("SSH_PASSWORD"):
        meta = (
            f"paramiko {_env('SSH_USER', 'root')}@{_env('SSH_HOST')}:"
            f"{_env('SSH_PORT', '22') or '22'} [streaming → {log_path}]"
        )
        try:
            out, code = _run_via_paramiko_streaming(remote_cmd, log_path, timeout_sec)
        except paramiko.AuthenticationException:
            return None, f"SSH 认证失败（用户名或密码错误）。\n{meta}"
        except paramiko.SSHException as e:
            return None, f"SSH 错误: {e}\n{meta}"
        except OSError as e:
            return None, f"网络或连接错误: {e}\n{meta}"
        except Exception as e:
            return None, f"执行失败: {e}\n{meta}"
        return code, _format_run_result(out, "", code)

    argv = _ssh_argv(remote_cmd)
    meta = f"ssh [streaming → {log_path}]"
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    try:
        with open(log_path, "w", encoding="utf-8", errors="replace") as lf:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            buf: list[str] = []
            assert proc.stdout is not None
            deadline = time.monotonic() + timeout_sec
            for line in proc.stdout:
                buf.append(line)
                lf.write(line)
                lf.flush()
                if time.monotonic() > deadline:
                    proc.kill()
                    lf.write(f"\n[MCP] 超时（{timeout_sec}s），强制终止。\n")
                    break
            proc.wait()
        code = proc.returncode
        return code, _format_run_result("".join(buf), "", code)
    except FileNotFoundError:
        return None, (
            "未找到 ssh 可执行文件。请在 Windows「可选功能」中安装 OpenSSH 客户端，"
            f"或确保 ssh 在 PATH 中。\n命令概要: {meta}"
        )
    except Exception as e:
        return None, f"执行失败: {e}\n命令概要: {meta}"


def _compile_job_timeout_seconds(env_var: str) -> int:
    v = _env(env_var)
    if v:
        return int(v)
    ssh = _env("SSH_TIMEOUT_SECONDS", "3600") or "3600"
    n = int(ssh)
    return n if n >= 7200 else 7200


def _extract_compile_digest(log_text: str) -> str:
    """从编译日志中提取关键信息：关键错误行（去重，最多 30 条）+ 末尾 50 行。"""
    lines = log_text.splitlines()

    err_re = re.compile(
        r"(?i)(\berror\b|\bFATAL\b|make:\s+\*\*\*|ninja:\s+build\s+stopped"
        r"|CMake Error|BUILD FAILURE|undefined reference|cannot find -l"
        r"|fatal error:|\[\s*FAILED\s*\])"
    )
    error_lines = [l.rstrip() for l in lines if err_re.search(l)]
    seen: set[str] = set()
    deduped: list[str] = []
    for l in error_lines:
        k = l.strip()
        if k not in seen:
            seen.add(k)
            deduped.append(l)

    parts: list[str] = []
    if deduped:
        parts.append("─── 关键错误行（去重，最多 30 条）─────────────────")
        parts.extend(deduped[-30:])
        parts.append("")

    parts.append("─── 日志末尾 50 行 ─────────────────────────────────")
    parts.extend(lines[-50:])
    return "\n".join(parts)


def _summarize_named_compile_outcome(
    name_cn: str,
    fail_steps_cn: str,
    exit_code: int | None,
    detail: str,
) -> str:
    if exit_code is None:
        return (
            f"【{name_cn} 编译】未能完成远程执行（连接中断、超时或本机 SSH 异常等）。"
            "以下为本工具返回的说明与可能的报错输出。\n\n"
            + detail
        )
    if exit_code != 0:
        return (
            f"【{name_cn} 编译失败】远程整条命令已结束，**退出码为 {exit_code}（非 0）**。"
            f"失败可能发生在：{fail_steps_cn}。"
            "请重点查看下方编译错误摘要。\n\n"
            + detail
        )
    return (
        f"【{name_cn} 编译成功】远程整条命令退出码为 **0**。"
        "（若脚本内部仍有问题但未以非 0 退出，请以日志为准。）\n\n"
        + detail
    )


@mcp.tool()
async def velox_ssh_check() -> str:
    """快速检查能否 SSH 登录：远端执行 echo / hostname / pwd。exit code 为 0 且 stdout 含 VELOX_MCP_OK 即表示连上。"""
    check_cmd = _env(
        "SSH_CHECK_COMMAND",
        "echo VELOX_MCP_OK && hostname && pwd && ls " + shlex.quote(_remote_workdir()),
    )
    to = int(_env("SSH_CHECK_TIMEOUT", "30") or "30")
    return await asyncio.to_thread(_run_remote, check_cmd, timeout_sec=to)


@mcp.tool()
async def compile_velox(git_repo_url: str, branch: str) -> str:
    """在远端 REMOTE_WORKDIR 下执行 ./compile_velox.sh <git_repo_url> <branch>，仅编译 Velox。

    脚本默认位置：/path/to/incubator-gluten/dev/compile_velox.sh
    传入位置参数：$1 = 代码仓 URL，$2 = 分支名（例如 gluten-1.3.0）

    两个参数均为必填；缺任一则返回中文提示且不连接服务器。
    执行结束后按退出码给出【成功/失败/未完成】摘要；非 0 时包含关键编译错误行（去重）、日志末尾 50 行。
    可用 get_compile_log 读取完整日志。
    超时：COMPILE_VELOX_TIMEOUT_SECONDS（未设置时默认至少 7200 秒）。
    """
    gr = (git_repo_url or "").strip()
    br = (branch or "").strip()
    missing: list[str] = []
    if not gr:
        missing.append(
            "代码仓地址（参数 git_repo_url，例如 https://<git-host>/<org>/<repo>.git）"
        )
    if not br:
        missing.append("分支名（参数 branch，例如 gluten-1.3.0）")
    if missing:
        return (
            "无法执行 Velox 编译：缺少或未填写必填参数，请先向用户确认后再调用本工具。\n"
            "必须提供：\n- "
            + "\n- ".join(missing)
        )

    try:
        cmd = _build_compile_velox_cmd(gr, br)
    except ValueError as e:
        return f"配置错误: {e}"

    timeout = _compile_job_timeout_seconds("COMPILE_VELOX_TIMEOUT_SECONDS")
    log_path = _new_compile_log_path("compile_velox")

    def _go() -> str:
        code, _raw = _run_remote_with_exit_and_log(cmd, log_path, timeout)
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                log_text = f.read()
        except OSError:
            log_text = _raw
        digest = _extract_compile_digest(log_text)
        summary = _summarize_named_compile_outcome(
            "Velox",
            "compile_velox.sh 中的 git clone / cmake / ninja / make 步骤",
            code,
            digest,
        )
        return summary + f"\n\n日志已保存：{log_path}"

    return await asyncio.to_thread(_go)


@mcp.tool()
async def compile_gluten_velox(git_repo_url: str, branch: str) -> str:
    """在远端 REMOTE_WORKDIR 下执行 ./compile_gluten_velox.sh <git_repo_url> <branch>，编译 Velox + Gluten 集成版本。

    脚本默认位置：/path/to/incubator-gluten/dev/compile_gluten_velox.sh
    传入位置参数：$1 = 代码仓 URL，$2 = 分支名（例如 gluten-1.3.0）

    与 compile_velox 相同的代码仓与分支即可（脚本内部既会编译 Velox 也会编译 Gluten 集成层）。
    两个参数均为必填；缺任一则返回中文提示且不连接服务器。
    执行结束后按退出码给出【成功/失败/未完成】摘要；非 0 时包含关键编译错误行（去重）、日志末尾 50 行。
    可用 get_compile_log 读取完整日志。
    超时：COMPILE_GLUTEN_VELOX_TIMEOUT_SECONDS（未设置时默认至少 7200 秒）。
    """
    gr = (git_repo_url or "").strip()
    br = (branch or "").strip()
    missing: list[str] = []
    if not gr:
        missing.append(
            "代码仓地址（参数 git_repo_url，例如 https://<git-host>/<org>/<repo>.git）"
        )
    if not br:
        missing.append("分支名（参数 branch，例如 gluten-1.3.0）")
    if missing:
        return (
            "无法执行 Gluten+Velox 编译：缺少或未填写必填参数，请先向用户确认后再调用本工具。\n"
            "必须提供：\n- "
            + "\n- ".join(missing)
        )

    try:
        cmd = _build_compile_gluten_velox_cmd(gr, br)
    except ValueError as e:
        return f"配置错误: {e}"

    timeout = _compile_job_timeout_seconds("COMPILE_GLUTEN_VELOX_TIMEOUT_SECONDS")
    log_path = _new_compile_log_path("compile_gluten_velox")

    def _go() -> str:
        code, _raw = _run_remote_with_exit_and_log(cmd, log_path, timeout)
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                log_text = f.read()
        except OSError:
            log_text = _raw
        digest = _extract_compile_digest(log_text)
        summary = _summarize_named_compile_outcome(
            "Gluten+Velox",
            "compile_gluten_velox.sh 中的 git clone / Velox cmake / Gluten cmake / mvn package 步骤",
            code,
            digest,
        )
        return summary + f"\n\n日志已保存：{log_path}"

    return await asyncio.to_thread(_go)


@mcp.tool()
async def run_velox_ut(
    build_dir: str = "_build/release",
    test_regex: str = "",
    timeout_sec: int = 7200,
) -> str:
    """Run Velox unit tests from an existing CMake build directory on the remote host.

    By default this runs the full CTest suite from REMOTE_VELOX_HOME/_build/release.
    Pass test_regex to run a subset, e.g. ^velox_base_test$.
    """
    bd = (build_dir or "_build/release").strip()
    regex = (test_regex or "").strip()
    timeout = max(60, int(timeout_sec or 7200))
    cmd = _build_run_velox_ut_cmd(bd, regex)
    log_path = _new_compile_log_path("velox_ut")

    def _go() -> str:
        code, _raw = _run_remote_with_exit_and_log(cmd, log_path, timeout)
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                log_text = f.read()
        except OSError:
            log_text = _raw
        tail = "\n".join(log_text.splitlines()[-80:])
        status = "SUCCESS" if code == 0 else "FAILED"
        return (
            f"Velox UT {status} (exit code={code})\n"
            f"build_dir={bd}\n"
            f"test_regex={regex or '<all>'}\n\n"
            f"Last log lines:\n{tail}\n\n"
            f"日志已保存：{log_path}"
        )

    return await asyncio.to_thread(_go)


@mcp.tool()
async def get_compile_log(tail_lines: int = 80) -> str:
    """读取最新一次编译（compile_velox 或 compile_gluten_velox）的实时日志末尾若干行（默认 80 行）。

    编译进行中可随时调用以查看当前进度；编译结束后同样可用。
    日志目录：COMPILE_LOG_DIR（未设置则为 velox_remote_mcp/compile_logs/）。
    """
    def _read() -> str:
        log_dir = _default_compile_log_dir()
        pointer = os.path.join(log_dir, "latest.log")
        log_path: str | None = None

        if os.path.exists(pointer):
            try:
                with open(pointer, "r", encoding="utf-8") as f:
                    candidate = f.read().strip()
                if candidate and os.path.exists(candidate):
                    log_path = candidate
            except OSError:
                pass

        if not log_path:
            try:
                candidates = sorted(
                    (p for p in Path(log_dir).glob("*.log") if p.name != "latest.log"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if candidates:
                    log_path = str(candidates[0])
            except OSError:
                pass

        if not log_path:
            return f"暂无编译日志。日志目录：{log_dir}"

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError as e:
            return f"读取日志失败：{e}\n路径：{log_path}"

        n = max(1, tail_lines)
        tail = lines[-n:] if len(lines) > n else lines
        header = (
            f"【编译日志】{log_path}\n"
            f"共 {len(lines)} 行，显示末尾 {len(tail)} 行\n"
            f"{'─' * 60}\n"
        )
        return header + "".join(tail)

    return await asyncio.to_thread(_read)


def _sftp_put_text(remote_path: str, content: str) -> None:
    """通过 paramiko SFTP 把文本写到远端文件。

    为什么用 SFTP 而不是命令行 base64：99 条 SQL 拼起来上百 KB，base64 后作为
    `printf '%s' <b64>` 命令行参数会超过 ARG_MAX，报 `/bin/bash: Argument list too long`。
    SFTP 直接走文件流，不受命令行长度限制。
    """
    client = _paramiko_connect()
    try:
        sftp = client.open_sftp()
        try:
            with sftp.open(remote_path, "w") as f:
                f.write(content)
        finally:
            sftp.close()
    finally:
        client.close()


def _remote_tmp_sql_path(tag: str) -> str:
    import uuid
    return f"/tmp/mcp_velox_{tag}_{uuid.uuid4().hex[:12]}.sql"


def _default_remote_tpcds_dir() -> str:
    return _env("REMOTE_TPCDS_QUERY_DIR", "/path/to/spark-queries-tpcds")


def _build_run_cmd(remote_sql_path: str, database: str = "") -> str:
    """跑 run_exec_velox.sh <sqlfile> <database>。

    run_exec_velox.sh 已照 omni run_e2e_sql.sh 改成吃 $1=sql 文件、$2=database
    （末尾 `$DB_ARGS -f "$SQL_FILE"`），所以这里直接调，不再需要 wrapper/sed hack。
    2>&1 合并 stderr；跑完删除临时 sql 文件。
    """
    script_path = _env("E2E_VELOX_SCRIPT", "/path/to/run_exec_velox.sh")
    sp = shlex.quote(script_path)
    sf = shlex.quote(remote_sql_path)
    db = shlex.quote(database) if database else "''"
    return f"bash {sp} {sf} {db} 2>&1; _ec=$?; rm -f {sf}; exit $_ec"


_QSTART = "===MCP_QSTART"
_QEND = "===MCP_QEND"


def _build_99_per_query_cmd(database: str, src_dir: str) -> str:
    """逐条独立进程跑：远端 for 循环，每条 query 单独起一个 spark-sql 进程。

    为什么逐条而不是拼一个大文件单 session：Velox 1.3 baseline 在部分 query 上会
    native crash（如 q6 SIGSEGV）。单 session 下任何一条 crash 整个进程死、后面全不跑，
    且 native crash 无法用 SQL 层 hive.cli.errors.ignore 容错。逐条独立进程则一条 crash
    只杀那条，for 循环继续下一条 —— 这是可靠筛选「哪些 query 跑不通」的唯一办法。

    每条 query 临时文件 = join 兼容 SET（规避 MergeJoin NOT_IMPLEMENTED）+ 原 query，
    用 run_exec_velox.sh（yarn）跑。yarn 模式 HDFS 正常（local 模式 driver 直连 HDFS 会
    在 libhdfs.so JNI 崩，已验证不可用）。

    输出用 `===MCP_QSTART_<qid>===` / `===MCP_QEND_<qid>_EXIT_<code>===` 标记每条边界与
    退出码，供 _split 切分判定 OK / crash / fail。
    """
    script_path = _env("E2E_VELOX_SCRIPT", "/path/to/run_exec_velox.sh")
    sp = shlex.quote(script_path)
    src = shlex.quote(src_dir)
    db = shlex.quote(database) if database else "''"
    setup = (
        "SET spark.gluten.sql.columnar.forceShuffledHashJoin=true;\\n"
        "SET spark.sql.join.preferSortMergeJoin=false;\\n"
    )
    return (
        f"for f in $(ls {src}/q*.sql | sort -V); do "
        f"qid=$(basename \"$f\" .sql); "
        f"TMP=/tmp/mcp_q_$qid.sql; "
        f"printf '{setup}' > \"$TMP\"; cat \"$f\" >> \"$TMP\"; "
        f"echo \"{_QSTART}_$qid===\"; "
        f"bash {sp} \"$TMP\" {db} 2>&1; "
        f"echo \"{_QEND}_${{qid}}_EXIT_$?===\"; "
        f"rm -f \"$TMP\"; "
        f"done"
    )


def _list_remote_tpcds_qids(src_dir: str) -> list[str]:
    """SFTP 列出远端 src_dir 下的 q*.sql，返回去 .sql 的 qid 列表（自然序）。"""
    client = _paramiko_connect()
    try:
        sftp = client.open_sftp()
        try:
            names = [
                n[:-4] for n in sftp.listdir(src_dir)
                if n.startswith("q") and n.endswith(".sql")
            ]
        finally:
            sftp.close()
    finally:
        client.close()
    names.sort(key=_natural_query_key)
    return names


def _parse_velox_e2e_output(merged_output: str) -> str:
    """从 spark-sql 输出中提取 App ID、耗时、Fallback 告警、查询结果。"""
    lines = merged_output.splitlines()

    log_re = re.compile(r"^\d{2}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}|^(?:INFO|WARN|ERROR|DEBUG) ")
    log_lines = [l for l in lines if log_re.match(l)]
    result_lines = [l for l in lines if not log_re.match(l) and l.strip()]

    app_id = "未找到"
    for line in lines:
        m = re.search(r"(local-\d+|application_\d{13}_\d{4,})", line)
        if m:
            app_id = m.group(1)
            break

    time_taken = "未知"
    query_failed = False
    for line in reversed(lines):
        m = re.search(r"Time taken:\s*([\d.]+)\s*seconds.*Fetched", line)
        if m:
            time_taken = m.group(1) + "s"
            break
    if time_taken == "未知":
        error_kws = (
            "VeloxException", "Exception in thread", "FAILED",
            "Error running query", "SparkException", "VeloxRuntimeError",
            "VeloxUserError",
        )
        for line in lines:
            if any(k in line for k in error_kws):
                query_failed = True
                break
        if query_failed:
            time_taken = "查询失败（未完成）"
        else:
            for line in reversed(lines):
                m = re.search(r"Time taken:\s*([\d.]+)\s*seconds", line)
                if m:
                    time_taken = m.group(1) + "s"
                    break

    fallback_kws = (
        "FallbackNode", "Validation failed", "native validation failed",
        "GlutenFallbackReporter", "fell back to", "fallback to vanilla",
    )
    fallback_lines = [
        l for l in log_lines if any(k.lower() in l.lower() for k in fallback_kws)
    ]

    velox_ops: dict[str, int] = {}
    op_pattern = re.compile(r"(Velox\w+(?:Exec|Transformer\w*)|Columnar\w*Exec)")
    for line in log_lines:
        for m in op_pattern.finditer(line):
            name = m.group(1)
            velox_ops[name] = velox_ops.get(name, 0) + 1

    parts: list[str] = []
    parts.append(f"App ID : {app_id}  |  耗时 : {time_taken}")
    parts.append("")

    parts.append("─── Velox / Gluten 算子（日志扫描）──────────")
    if velox_ops:
        for name, cnt in sorted(velox_ops.items()):
            parts.append(f"  ✅ {name}  ×{cnt}")
    else:
        parts.append("  ⚠️  未检测到 Velox/Gluten 算子（可能全部 Fallback 或日志未打印 plan）")
    parts.append("")

    parts.append(f"─── Fallback 告警（{len(fallback_lines)} 条）────────────────")
