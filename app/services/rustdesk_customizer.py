import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


RUSTDESK_REPO_URL = "https://github.com/rustdesk/rustdesk.git"
GITHUB_API = "https://api.github.com"
SENSITIVE_WORKFLOW_KEYS = {
    "RENDEZVOUS_SERVER",
    "RELAY_SERVER",
    "API_SERVER",
    "RS_PUB_KEY",
}


class RustDeskCustomizeError(RuntimeError):
    pass


@dataclass
class GitHubRepo:
    owner: str
    name: str
    clone_url: str
    html_url: str
    default_branch: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


def normalize_repo(value: str) -> tuple[str, str]:
    raw = (value or "").strip()
    if not raw:
        raise RustDeskCustomizeError("请填写 GitHub 公开仓库地址")
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) < 2 or parsed.netloc.lower() not in {"github.com", "www.github.com"}:
            raise RustDeskCustomizeError("GitHub 仓库地址格式应为 https://github.com/owner/repo")
        owner, name = parts[0], parts[1]
    else:
        parts = [part for part in raw.strip("/").split("/") if part]
        if len(parts) != 2:
            raise RustDeskCustomizeError("GitHub 仓库地址格式应为 owner/repo")
        owner, name = parts
    name = re.sub(r"\.git$", "", name)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise RustDeskCustomizeError("GitHub 仓库 owner/repo 包含非法字符")
    return owner, name


def _http_json(url: str, token: str) -> tuple[dict[str, Any], dict[str, str]]:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ctyun-manager-rustdesk-customizer",
        },
    )
    try:
        with urlopen(request, timeout=25) as response:
            body = response.read().decode("utf-8")
            headers = {key.lower(): value for key, value in response.headers.items()}
            return json.loads(body), headers
    except HTTPError as exc:
        message = exc.read().decode("utf-8", errors="ignore")
        if exc.code == 401:
            raise RustDeskCustomizeError("GitHub token 无效或已过期") from exc
        if exc.code == 404:
            raise RustDeskCustomizeError("无法访问目标仓库，请确认仓库存在且 token 有 public_repo 权限") from exc
        raise RustDeskCustomizeError(f"GitHub API 请求失败：HTTP {exc.code} {message[:160]}") from exc
    except URLError as exc:
        raise RustDeskCustomizeError(f"GitHub API 网络请求失败：{exc.reason}") from exc


def validate_github_repo(repo_value: str, token: str) -> GitHubRepo:
    if not token or not token.strip():
        raise RustDeskCustomizeError("请填写 Personal access token classic")
    owner, name = normalize_repo(repo_value)
    data, headers = _http_json(f"{GITHUB_API}/repos/{owner}/{name}", token.strip())
    scopes = {
        scope.strip()
        for scope in (headers.get("x-oauth-scopes") or "").split(",")
        if scope.strip()
    }
    if scopes:
        if "repo" not in scopes:
            missing = [scope for scope in ("public_repo", "workflow") if scope not in scopes]
            if missing:
                raise RustDeskCustomizeError(f"token 缺少权限：{', '.join(missing)}")
        elif "workflow" not in scopes:
            raise RustDeskCustomizeError("token 缺少权限：workflow")
    if data.get("private"):
        raise RustDeskCustomizeError("目标仓库必须是公开仓库，当前仓库是 Private")
    clone_url = str(data.get("clone_url") or f"https://github.com/{owner}/{name}.git")
    return GitHubRepo(
        owner=owner,
        name=name,
        clone_url=clone_url,
        html_url=str(data.get("html_url") or f"https://github.com/{owner}/{name}"),
        default_branch=str(data.get("default_branch") or "main"),
    )


def _rust_string(value: str) -> str:
    return json.dumps(value or "", ensure_ascii=False)


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def run_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    progress: Callable[[str], None],
    redact: list[str] | None = None,
    timeout: int | None = None,
) -> str:
    redactions = [value for value in (redact or []) if value]
    display = " ".join(args)
    for value in redactions:
        display = display.replace(value, "***")
    progress(f"$ {display}")
    process = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    output = "\n".join(part for part in (process.stdout.strip(), process.stderr.strip()) if part)
    for value in redactions:
        output = output.replace(value, "***")
    if process.returncode != 0:
        if output:
            progress(output[-1800:])
        raise RustDeskCustomizeError(f"命令执行失败：{args[0]} {args[1] if len(args) > 1 else ''}".strip())
    if output:
        progress(output[-1800:])
    return output


def token_redactions(token: str) -> list[str]:
    encoded = quote(token or "", safe="")
    return [value for value in [token, encoded] if value]


def authenticated_clone_url(repo: GitHubRepo, token: str) -> str:
    encoded = quote(token, safe="")
    return f"https://x-access-token:{encoded}@github.com/{repo.owner}/{repo.name}.git"


def git_noninteractive_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def validate_rustdesk_tag(version: str, progress: Callable[[str], None]) -> str:
    tag = (version or "").strip()
    if tag.startswith("v"):
        raise RustDeskCustomizeError("RustDesk 版本请填写官方 tag，例如 1.4.7，不要填写 v1.4.7-8")
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9._-]+)?", tag):
        raise RustDeskCustomizeError("RustDesk 官方版本格式不正确，例如 1.4.7")
    ref = f"refs/tags/{tag}"
    output = run_command(
        ["git", "ls-remote", "--tags", RUSTDESK_REPO_URL, ref],
        progress=progress,
        timeout=60,
    )
    if not output.strip():
        raise RustDeskCustomizeError(f"官方 RustDesk 仓库没有找到 tag：{tag}")
    return tag


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def localize_submodules(source_dir: Path, progress: Callable[[str], None]) -> None:
    progress("正在本地化 RustDesk 子模块...")
    try:
        output = run_command(
            ["git", "submodule", "status", "--recursive"],
            cwd=source_dir,
            progress=progress,
            timeout=120,
        )
    except RustDeskCustomizeError:
        output = ""
    submodule_paths: list[Path] = []
    for line in output.splitlines():
        match = re.match(r"^[ +-U]?[0-9a-fA-F]+ ([^ ]+)", line.strip())
        if match:
            submodule_paths.append(source_dir / match.group(1))
    for path in submodule_paths:
        git_marker = path / ".git"
        if git_marker.exists():
            _remove_path(git_marker)
    gitmodules = source_dir / ".gitmodules"
    if gitmodules.exists():
        gitmodules.unlink()
    progress(f"子模块已本地化：{len(submodule_paths)} 个")


def _replace_regex(text: str, pattern: str, repl: str, label: str) -> str:
    new_text, count = re.subn(pattern, repl, text, count=1, flags=re.S)
    if count != 1:
        raise RustDeskCustomizeError(f"未能定位 RustDesk 源码位置：{label}")
    return new_text


def patch_config_rs(source_dir: Path, payload: dict[str, Any], progress: Callable[[str], None]) -> None:
    path = source_dir / "libs" / "hbb_common" / "src" / "config.rs"
    if not path.exists():
        raise RustDeskCustomizeError("未找到 libs/hbb_common/src/config.rs，可能是 RustDesk 版本结构变化")
    id_server = payload["id_server"].strip()
    relay_server = (payload.get("relay_server") or "").strip()
    servers = [id_server]
    if relay_server and relay_server != id_server:
        servers.append(relay_server)
    server_expr = "&[" + ", ".join(_rust_string(server) for server in servers) + "]"
    text = path.read_text(encoding="utf-8", errors="replace")
    text = _replace_regex(
        text,
        r"pub\s+const\s+RENDEZVOUS_SERVERS\s*:\s*&\s*\[\s*&str\s*\]\s*=\s*&\s*\[.*?\]\s*;",
        f"pub const RENDEZVOUS_SERVERS: &[&str] = {server_expr};",
        "RENDEZVOUS_SERVERS",
    )
    text = _replace_regex(
        text,
        r"pub\s+const\s+RS_PUB_KEY\s*:\s*&str\s*=\s*\".*?\"\s*;",
        f"pub const RS_PUB_KEY: &str = {_rust_string(payload['rs_pub_key'].strip())};",
        "RS_PUB_KEY",
    )
    _write_text(path, text)
    progress("已写入 ID 服务器和 RS_PUB_KEY 到 hbb_common/config.rs")


def hard_settings_entries(payload: dict[str, Any]) -> list[tuple[str, str]]:
    api_server = (payload.get("api_server") or "").strip()
    if not api_server:
        id_server = payload["id_server"].strip()
        api_server = f"http://{id_server}:21114"
    entries = [
        ("api-server", api_server),
        ("allow-remote-config-modification", "Y" if payload.get("allow_remote_config_modification", True) else "N"),
        ("allow-hide-cm", "Y" if payload.get("hide_cm", True) else "N"),
    ]
    password = (payload.get("default_password") or "").strip()
    if password:
        entries.append(("password", password))
        entries.append(("verification-method", "use-permanent-password"))
    relay_server = (payload.get("relay_server") or "").strip()
    if relay_server:
        entries.append(("relay-server", relay_server))
    return entries


def patch_common_rs(source_dir: Path, payload: dict[str, Any], progress: Callable[[str], None]) -> None:
    path = source_dir / "src" / "common.rs"
    if not path.exists():
        raise RustDeskCustomizeError("未找到 src/common.rs，可能是 RustDesk 版本结构变化")
    text = path.read_text(encoding="utf-8", errors="replace")
    entries = hard_settings_entries(payload)
    rust_entries = ",\n    ".join(f"({_rust_string(key)}, {_rust_string(value)})" for key, value in entries)
    custom_block = (
        "// ctyun-manager custom client hard settings\n"
        "pub const CTYUN_MANAGER_HARD_SETTINGS: &[(&str, &str)] = &[\n"
        f"    {rust_entries}\n"
        "];\n"
    )
    marker = "pub const CTYUN_MANAGER_HARD_SETTINGS"
    if marker in text:
        text = re.sub(
            r"// ctyun-manager custom client hard settings\npub const CTYUN_MANAGER_HARD_SETTINGS:.*?\n\];\n",
            custom_block,
            text,
            flags=re.S,
        )
    else:
        text += "\n\n" + custom_block
    injector = (
        "    for (key, value) in CTYUN_MANAGER_HARD_SETTINGS {\n"
        "        config::HARD_SETTINGS\n"
        "            .write()\n"
        "            .unwrap()\n"
        "            .insert((*key).to_string(), (*value).to_string());\n"
        "    }\n"
    )
    if "for (key, value) in CTYUN_MANAGER_HARD_SETTINGS" not in text:
        text = _replace_regex(
            text,
            r"pub\s+fn\s+load_custom_client\s*\(\)\s*\{\n",
            "pub fn load_custom_client() {\n" + injector,
            "load_custom_client",
        )
    _write_text(path, text)
    progress("已写入 API fallback、默认密码和客户端选项到 src/common.rs")


def patch_about_pages(source_dir: Path, payload: dict[str, Any], progress: Callable[[str], None]) -> None:
    about = payload.get("about") or {}
    values = {key: str(value or "").strip() for key, value in about.items() if str(value or "").strip()}
    if not values:
        return
    note_lines = [
        "# Custom About Page",
        "",
        "本文件由天翼云管理台 RustDesk 定制工具生成，用于记录关于页定制文案。",
        "",
    ]
    for key, value in values.items():
        note_lines.append(f"- {key}: {value}")
    _write_text(source_dir / "CUSTOM_ABOUT.md", "\n".join(note_lines) + "\n")
    progress("已写入关于页定制记录 CUSTOM_ABOUT.md")


def patch_ui_hide_links(source_dir: Path, payload: dict[str, Any], progress: Callable[[str], None]) -> None:
    if not payload.get("hide_cm", True):
        return
    touched = 0
    candidates = [
        source_dir / "flutter" / "lib" / "desktop" / "pages" / "desktop_setting_page.dart",
        source_dir / "flutter" / "lib" / "mobile" / "pages" / "settings_page.dart",
        source_dir / "src" / "ui" / "index.tis",
    ]
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        original = text
        text = re.sub(r"https://rustdesk\.com/docs[^\"')\s]*", "", text)
        text = text.replace("自建服务器", "服务器配置")
        text = text.replace("Self-host", "Server")
        if text != original:
            _write_text(path, text)
            touched += 1
    progress(f"已处理自建服务器跳转入口：{touched} 个文件")


def remove_bundled_server_settings(source_dir: Path, progress: Callable[[str], None]) -> None:
    for rel in [
        ("res", "local_custom_client.json"),
        (".github", "scripts", "apply-bundled-server-settings.py"),
    ]:
        path = source_dir.joinpath(*rel)
        if path.exists():
            _remove_path(path)
            progress(f"已删除 {path.relative_to(source_dir).as_posix()}")
    workflows_dir = source_dir / ".github" / "workflows"
    if not workflows_dir.exists():
        return
    for path in list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml")):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        new_lines: list[str] = []
        skipping_apply_step = False
        skip_indent = 0
        changed = False
        for line in lines:
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))
            if skipping_apply_step:
                if stripped.startswith("- name:") and indent <= skip_indent:
                    skipping_apply_step = False
                else:
                    changed = True
                    continue
            if re.match(r"-\s+name\s*:\s*.*Apply bundled server settings", stripped, re.I):
                skipping_apply_step = True
                skip_indent = indent
                changed = True
                continue
            if any(key in line for key in SENSITIVE_WORKFLOW_KEYS):
                changed = True
                continue
            if "submodules:" in line and "false" not in stripped:
                line = re.sub(r"submodules\s*:.*", "submodules: false", line)
                changed = True
            new_lines.append(line)
        if changed:
            _write_text(path, "\n".join(new_lines) + "\n")
    progress("已禁用 local_custom_client/default-settings 相关 workflow 逻辑")


def write_custom_notes(source_dir: Path, payload: dict[str, Any]) -> None:
    values = {
        "rustdesk_version": payload["rustdesk_version"],
        "id_server": payload["id_server"],
        "relay_server": payload.get("relay_server") or "",
        "api_server": payload.get("api_server") or f"http://{payload['id_server']}:21114",
        "default_password": "已设置" if payload.get("default_password") else "未设置",
        "allow_remote_config_modification": bool(payload.get("allow_remote_config_modification", True)),
        "hide_cm": bool(payload.get("hide_cm", True)),
    }
    _write_text(
        source_dir / "CUSTOM_CLIENT.md",
        "# RustDesk Custom Client\n\n"
        "本仓库由天翼云管理台 RustDesk 定制工具生成。\n\n"
        "## 配置摘要\n\n"
        + "\n".join(f"- {key}: {value}" for key, value in values.items())
        + "\n\n服务器信息写入源码常量，不使用 `res/local_custom_client.json`。\n",
    )


def apply_customizations(source_dir: Path, payload: dict[str, Any], progress: Callable[[str], None]) -> None:
    remove_bundled_server_settings(source_dir, progress)
    patch_config_rs(source_dir, payload, progress)
    patch_common_rs(source_dir, payload, progress)
    patch_ui_hide_links(source_dir, payload, progress)
    patch_about_pages(source_dir, payload, progress)
    write_custom_notes(source_dir, payload)


def clear_target_checkout(target_dir: Path) -> None:
    for item in target_dir.iterdir():
        if item.name == ".git":
            continue
        _remove_path(item)


def copy_source_to_target(source_dir: Path, target_dir: Path) -> None:
    clear_target_checkout(target_dir)
    ignored = {".git"}
    for item in source_dir.iterdir():
        if item.name in ignored:
            continue
        dest = target_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest, symlinks=True)
        else:
            shutil.copy2(item, dest)


def commit_and_push_target(
    repo: GitHubRepo,
    target_dir: Path,
    token: str,
    payload: dict[str, Any],
    progress: Callable[[str], None],
) -> dict[str, Any]:
    env = git_noninteractive_env()
    redactions = token_redactions(token)
    branch = payload.get("target_branch") or repo.default_branch or "main"
    run_command(["git", "checkout", "-B", branch], cwd=target_dir, env=env, progress=progress, redact=redactions, timeout=60)
    run_command(["git", "add", "-A"], cwd=target_dir, env=env, progress=progress, redact=redactions, timeout=300)
    status = run_command(["git", "status", "--porcelain"], cwd=target_dir, env=env, progress=progress, redact=redactions, timeout=60)
    if not status.strip():
        progress("目标仓库没有产生新的文件差异")
        return {"pushed": False, "branch": branch, "url": repo.html_url}
    run_command(
        ["git", "config", "user.name", "ctyun-manager-bot"],
        cwd=target_dir,
        env=env,
        progress=progress,
        redact=redactions,
        timeout=30,
    )
    run_command(
        ["git", "config", "user.email", "ctyun-manager-bot@users.noreply.github.com"],
        cwd=target_dir,
        env=env,
        progress=progress,
        redact=redactions,
        timeout=30,
    )
    message = (payload.get("commit_message") or "").strip() or f"Customize RustDesk {payload['rustdesk_version']}"
    run_command(["git", "commit", "-m", message], cwd=target_dir, env=env, progress=progress, redact=redactions, timeout=300)
    run_command(
        ["git", "remote", "set-url", "origin", authenticated_clone_url(repo, token)],
        cwd=target_dir,
        env=env,
        progress=progress,
        redact=redactions,
        timeout=60,
    )
    run_command(["git", "push", "origin", branch], cwd=target_dir, env=env, progress=progress, redact=redactions, timeout=600)
    return {"pushed": True, "branch": branch, "url": repo.html_url}


def customize_rustdesk(payload: dict[str, Any], progress: Callable[[str], None]) -> dict[str, Any]:
    token = (payload.get("token") or "").strip()
    payload = dict(payload)
    payload["id_server"] = (payload.get("id_server") or "").strip()
    payload["rs_pub_key"] = (payload.get("rs_pub_key") or "").strip()
    required = {
        "GitHub 公开仓库": payload.get("repo"),
        "RustDesk 官方版本": payload.get("rustdesk_version"),
        "ID 服务器": payload.get("id_server"),
        "RS_PUB_KEY": payload.get("rs_pub_key"),
    }
    missing = [label for label, value in required.items() if not str(value or "").strip()]
    if missing:
        raise RustDeskCustomizeError(f"请填写：{'、'.join(missing)}")

    progress("正在校验 GitHub 仓库和 token 权限...")
    repo = validate_github_repo(str(payload.get("repo") or ""), token)
    progress(f"目标仓库：{repo.full_name}（公开仓库）")

    tag = validate_rustdesk_tag(str(payload.get("rustdesk_version") or ""), progress)
    payload["rustdesk_version"] = tag

    with tempfile.TemporaryDirectory(prefix="ctyun-rustdesk-") as tmp:
        tmp_path = Path(tmp)
        source_dir = tmp_path / "rustdesk-source"
        target_dir = tmp_path / "target-repo"
        env = git_noninteractive_env()
        redactions = token_redactions(token)

        progress(f"正在拉取 RustDesk 官方源码 tag {tag}...")
        run_command(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                tag,
                "--recurse-submodules",
                "--shallow-submodules",
                RUSTDESK_REPO_URL,
                str(source_dir),
            ],
            progress=progress,
            timeout=1800,
        )
        localize_submodules(source_dir, progress)
        apply_customizations(source_dir, payload, progress)

        progress("正在拉取目标公开仓库...")
        run_command(
            ["git", "clone", authenticated_clone_url(repo, token), str(target_dir)],
            env=env,
            progress=progress,
            redact=redactions,
            timeout=600,
        )
        progress("正在写入定制后的 RustDesk 源码...")
        copy_source_to_target(source_dir, target_dir)
        result = commit_and_push_target(repo, target_dir, token, payload, progress)
        progress("RustDesk 定制仓库写入完成")
        return {
            **result,
            "repo": repo.full_name,
            "rustdesk_version": tag,
            "actions_url": f"{repo.html_url}/actions",
        }
