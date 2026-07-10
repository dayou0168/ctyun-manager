import io
import posixpath
import socket
import stat
import time
from typing import Any

import paramiko


class SSHManagerError(Exception):
    pass


def normalize_host(value: str) -> str:
    host = (value or "").strip()
    if not host:
        raise SSHManagerError("请填写服务器地址")
    if "://" in host or any(char.isspace() for char in host):
        raise SSHManagerError("服务器地址只填写 IP 或域名，不要包含协议或空格")
    return host.strip("[]")


def _private_key_from_text(private_key: str, passphrase: str | None = None) -> paramiko.PKey:
    errors: list[str] = []
    for key_class in (
        paramiko.RSAKey,
        paramiko.Ed25519Key,
        paramiko.ECDSAKey,
        paramiko.DSSKey,
    ):
        try:
            return key_class.from_private_key(io.StringIO(private_key), password=passphrase or None)
        except Exception as exc:
            errors.append(str(exc))
    raise SSHManagerError("私钥格式无法识别，请确认是 OpenSSH/PEM 私钥") from ValueError("; ".join(errors))


def ssh_fingerprint(client: paramiko.SSHClient) -> str:
    transport = client.get_transport()
    if not transport:
        return ""
    key = transport.get_remote_server_key()
    if not key:
        return ""
    return f"{key.get_name()} {key.get_base64()[:18]}..."


def connect_ssh(config: dict[str, Any], timeout: int = 10) -> paramiko.SSHClient:
    username = (config.get("username") or "").strip()
    if not username:
        raise SSHManagerError("请填写 SSH 登录账号")
    host = normalize_host(config.get("host") or "")
    port = int(config.get("port") or 22)
    if port < 1 or port > 65535:
        raise SSHManagerError("SSH 端口必须在 1-65535 之间")

    password = config.get("password") or None
    private_key = config.get("private_key") or ""
    passphrase = config.get("private_key_passphrase") or password
    pkey = _private_key_from_text(private_key, passphrase) if private_key else None
    if not password and not pkey:
        raise SSHManagerError("请填写 SSH 密码或私钥")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password if not pkey else None,
            pkey=pkey,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
        )
        return client
    except (paramiko.SSHException, socket.error, OSError) as exc:
        client.close()
        raise SSHManagerError(str(exc) or "SSH 连接失败") from exc


def run_ssh_command(config: dict[str, Any], command: str, timeout: int = 30) -> dict[str, Any]:
    command = (command or "").strip()
    if not command:
        raise SSHManagerError("请填写要执行的命令")
    timeout = max(3, min(int(timeout or 30), 300))
    client = connect_ssh(config, timeout=min(timeout, 15))
    channel = None
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    try:
        transport = client.get_transport()
        if not transport:
            raise SSHManagerError("SSH 连接未就绪")
        channel = transport.open_session(timeout=min(timeout, 15))
        channel.exec_command(command)
        deadline = time.monotonic() + timeout
        while True:
            while channel.recv_ready():
                stdout_chunks.append(channel.recv(65536))
            while channel.recv_stderr_ready():
                stderr_chunks.append(channel.recv_stderr(65536))
            if channel.exit_status_ready():
                break
            if time.monotonic() > deadline:
                channel.close()
                raise SSHManagerError(f"命令执行超过 {timeout} 秒，已中断")
            time.sleep(0.05)
        while channel.recv_ready():
            stdout_chunks.append(channel.recv(65536))
        while channel.recv_stderr_ready():
            stderr_chunks.append(channel.recv_stderr(65536))
        exit_status = channel.recv_exit_status()
        return {
            "exit_status": exit_status,
            "stdout": b"".join(stdout_chunks).decode("utf-8", errors="replace"),
            "stderr": b"".join(stderr_chunks).decode("utf-8", errors="replace"),
            "fingerprint": ssh_fingerprint(client),
        }
    finally:
        if channel:
            channel.close()
        client.close()


def test_ssh_connection(config: dict[str, Any]) -> dict[str, Any]:
    client = connect_ssh(config, timeout=10)
    try:
        return {
            "ok": True,
            "fingerprint": ssh_fingerprint(client),
        }
    finally:
        client.close()


def _join_remote_path(parent: str, name: str) -> str:
    base = parent if parent.startswith("/") else f"/{parent}"
    if base == "/":
        return f"/{name}"
    return f"{base.rstrip('/')}/{name}"


def _remote_parent(path: str) -> str:
    value = (path or "/").replace("\\", "/").rstrip("/") or "/"
    parent = posixpath.dirname(value)
    return parent or "/"


def list_remote_directory(config: dict[str, Any], path: str = ".") -> dict[str, Any]:
    client = connect_ssh(config, timeout=12)
    try:
        sftp = client.open_sftp()
        try:
            normalized = sftp.normalize((path or ".").replace("\\", "/"))
            attrs = sorted(sftp.listdir_attr(normalized), key=lambda item: (not stat.S_ISDIR(item.st_mode), item.filename.lower()))
            entries = []
            for item in attrs:
                is_dir = stat.S_ISDIR(item.st_mode)
                entries.append({
                    "name": item.filename,
                    "path": _join_remote_path(normalized, item.filename),
                    "type": "dir" if is_dir else "file",
                    "size": int(item.st_size or 0),
                    "mtime": int(item.st_mtime or 0),
                    "mode": oct(item.st_mode & 0o777),
                    "uid": int(item.st_uid or 0),
                    "gid": int(item.st_gid or 0),
                })
            return {
                "path": normalized,
                "parent": _remote_parent(normalized),
                "entries": entries,
                "fingerprint": ssh_fingerprint(client),
            }
        finally:
            sftp.close()
    except (paramiko.SSHException, OSError) as exc:
        raise SSHManagerError(str(exc) or "SFTP list failed") from exc
    finally:
        client.close()


def read_remote_file(config: dict[str, Any], path: str, max_bytes: int = 2_000_000) -> dict[str, Any]:
    remote_path = (path or "").replace("\\", "/").strip()
    if not remote_path:
        raise SSHManagerError("Remote file path is required")
    client = connect_ssh(config, timeout=12)
    try:
        sftp = client.open_sftp()
        try:
            normalized = sftp.normalize(remote_path)
            attrs = sftp.stat(normalized)
            size = int(attrs.st_size or 0)
            if size > max_bytes:
                raise SSHManagerError(f"Remote file is too large ({size} bytes)")
            with sftp.open(normalized, "rb") as handle:
                data = handle.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise SSHManagerError(f"Remote file is too large ({len(data)} bytes)")
            return {
                "path": normalized,
                "parent": _remote_parent(normalized),
                "content": data.decode("utf-8", errors="replace"),
                "size": size,
                "mtime": int(attrs.st_mtime or 0),
                "mode": oct(attrs.st_mode & 0o777),
                "fingerprint": ssh_fingerprint(client),
            }
        finally:
            sftp.close()
    except SSHManagerError:
        raise
    except (paramiko.SSHException, OSError) as exc:
        raise SSHManagerError(str(exc) or "SFTP read failed") from exc
    finally:
        client.close()


def write_remote_file(config: dict[str, Any], path: str, content: str, max_bytes: int = 2_000_000, mode: int | None = None) -> dict[str, Any]:
    remote_path = (path or "").replace("\\", "/").strip()
    if not remote_path:
        raise SSHManagerError("Remote file path is required")
    data = (content or "").encode("utf-8")
    if len(data) > max_bytes:
        raise SSHManagerError(f"Remote file content is too large ({len(data)} bytes)")
    client = connect_ssh(config, timeout=12)
    try:
        sftp = client.open_sftp()
        try:
            parent = sftp.normalize(posixpath.dirname(remote_path) or ".")
            normalized = _join_remote_path(parent, posixpath.basename(remote_path))
            with sftp.open(normalized, "wb") as handle:
                handle.write(data)
            if mode is not None:
                sftp.chmod(normalized, mode)
            attrs = sftp.stat(normalized)
            return {
                "path": normalized,
                "parent": _remote_parent(normalized),
                "size": int(attrs.st_size or 0),
                "mtime": int(attrs.st_mtime or 0),
                "mode": oct(attrs.st_mode & 0o777),
                "fingerprint": ssh_fingerprint(client),
            }
        finally:
            sftp.close()
    except (paramiko.SSHException, OSError) as exc:
        raise SSHManagerError(str(exc) or "SFTP write failed") from exc
    finally:
        client.close()
