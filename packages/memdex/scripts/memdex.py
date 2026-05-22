#!/usr/bin/env python3
"""Project-level semantic retrieval helper.

This script intentionally depends only on Python stdlib for the control plane.
It shells out to `npx repomix`, `notebooklm`, `git`, and `rg` when needed.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import datetime as dt
import errno
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any


CONFIG_DIR = ".memdex"
CONFIG_JSON = "config.json"
STATE_JSON = "state.local.json"
PENDING_UPLOAD_JSON = "pending-upload.local.json"
DEFAULT_NOTEBOOK_TITLE_PREFIX = "memdex"
SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_CMD_ENV = "MEMDEX_CMD"
LEGACY_SCRIPT_CMD_ENV = "CODEBASE_RETRIEVE_CMD"
NOTEBOOKLM_PACKAGE = "git+https://github.com/teng-lin/notebooklm-py.git"
NOTEBOOKLM_BIN_ENV = "NOTEBOOKLM_BIN"


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(ts: dt.datetime | None = None) -> str:
    return (ts or now_utc()).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def die(message: str, code: int = 2) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def script_cmd() -> list[str]:
    override = os.environ.get(SCRIPT_CMD_ENV, "").strip()
    if not override:
        override = os.environ.get(LEGACY_SCRIPT_CMD_ENV, "").strip()
    if override:
        return shlex.split(override)
    return [sys.executable or "python3", str(SCRIPT_PATH)]


def command_line(repo: Path, command: str, *parts: str) -> str:
    rendered = [*script_cmd(), command, "--repo", str(repo), *parts]
    return " ".join(shlex.quote(part) for part in rendered)


def missing_config_message(repo: Path, config_file: Path, command: str = "") -> str:
    init_create = command_line(repo, "init", "--create-notebook")
    init_reuse = command_line(repo, "init", "--reuse-existing-notebook")
    ask = command_line(repo, "ask", "your question")
    ask_yes = command_line(repo, "ask", "--yes", "your question")
    locate = command_line(repo, "locate", "thing to find")
    lines = [
        f"project is not initialized for project retrieval: {config_file}",
        "",
        "Initialize this repo first:",
        f"  {init_create}",
        "",
        "Or reuse an existing NotebookLM notebook with the expected title:",
        f"  {init_reuse}",
        "",
        "Then ask or locate directly; both commands run freshness preflight:",
        f"  {ask}",
        f"  {locate}",
        "",
        "If this is the first broad upload and you already approve it:",
        f"  {ask_yes}",
    ]
    if command:
        lines.insert(1, f"Command `{command}` needs `.memdex/config.json` before it can run.")
    return "\n".join(lines)


def uninitialized_status(repo: Path, config_file: Path) -> dict[str, Any]:
    return {
        "status": "not-initialized",
        "initialized": False,
        "config": str(config_file),
        "message": "project is not initialized for project retrieval",
        "next": {
            "createNotebook": command_line(repo, "init", "--create-notebook"),
            "reuseExistingNotebook": command_line(repo, "init", "--reuse-existing-notebook"),
            "ask": command_line(repo, "ask", "your question"),
            "locate": command_line(repo, "locate", "thing to find"),
            "askWithFirstUploadApproval": command_line(repo, "ask", "--yes", "your question"),
        },
    }


@contextlib.contextmanager
def repo_lock(repo: Path, *, timeout_seconds: float = 300.0):
    lock_path = repo / CONFIG_DIR / ".lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    fd: int | None = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"pid={os.getpid()}\ncreatedAt={iso()}\n".encode("utf-8"))
            break
        except OSError as error:
            if error.errno != errno.EEXIST:
                raise
            if time.monotonic() - start > timeout_seconds:
                die(f"timed out waiting for lock: {lock_path}")
            time.sleep(0.2)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def run(argv: list[str], cwd: Path, *, input_text: str | None = None, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd),
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        die(f"required tool not found on PATH: {name}")


def notebooklm_cmd() -> list[str]:
    override = os.environ.get(NOTEBOOKLM_BIN_ENV, "").strip()
    if override:
        return shlex.split(override)
    found = shutil.which("notebooklm")
    if found:
        return [found]
    die(
        "required tool not found on PATH: notebooklm\n"
        f"Install persistently: uv tool install {NOTEBOOKLM_PACKAGE}\n"
        f"Or set {NOTEBOOKLM_BIN_ENV}='uvx --from {NOTEBOOKLM_PACKAGE} notebooklm'"
    )


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_text(data: str) -> str:
    return sha256_bytes(data.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def remove_file_quiet(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def default_include() -> list[str]:
    return [
        "src",
        "crates",
        "packages",
        "apps",
        "bins",
        "docs",
        "scripts",
        "tests",
        "xtask",
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        "Cargo.toml",
        "package.json",
        "justfile",
    ]


def default_groups() -> list[dict[str, Any]]:
    return [
        {"id": "docs", "include": ["AGENTS.md", "CLAUDE.md", "README.md", "docs/**"]},
        {"id": "apps", "include": ["apps/**"]},
        {"id": "packages", "include": ["packages/**"]},
        {"id": "src", "include": ["src/**", "crates/**", "bins/**", "xtask/**"]},
        {"id": "tests", "include": ["tests/**", "testdata/**"]},
        {"id": "scripts", "include": ["scripts/**"]},
    ]


def slugify(value: str) -> str:
    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", lowered)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "repo"


def default_notebook_title(project_name: str, title_prefix: str = DEFAULT_NOTEBOOK_TITLE_PREFIX) -> str:
    return f"{title_prefix}:{project_name}"


def default_source_title_prefix(project_name: str, title_prefix: str = DEFAULT_NOTEBOOK_TITLE_PREFIX) -> str:
    return f"{slugify(title_prefix)}-{slugify(project_name)}-repo"


def default_short_source_title_prefix() -> str:
    return "memdex"


def default_config(
    repo: Path,
    notebook_id: str = "",
    *,
    project_name: str | None = None,
    notebook_title_prefix: str = DEFAULT_NOTEBOOK_TITLE_PREFIX,
    notebook_title: str | None = None,
) -> dict[str, Any]:
    project = project_name or repo.name
    title = notebook_title or default_notebook_title(project, notebook_title_prefix)
    return {
        "version": 1,
        "project": {
            "name": project,
        },
        "provider": "notebooklm",
        "notebooklm": {
            "notebook_id": notebook_id,
            "notebook_title_prefix": notebook_title_prefix,
            "notebook_title": title,
            "source_title_prefix": default_short_source_title_prefix(),
            "wait_after_upload": True,
            "upload_parallelism": 4,
            "wait_parallelism": 8,
            "delete_parallelism": 4,
        },
        "bundle": {
            "tool": "repomix",
            "mode": "chunked",
            "include": default_include(),
            "output": f"{CONFIG_DIR}/cache/{{prefix}}-{{timestamp}}.txt",
            "style": "",
            "compress": False,
            "target_chunk_bytes": 716800,
            "max_chunk_bytes": 900000,
            "source_title_template": "{prefix}--{set}--{group}--{chunk}--{hash}.md",
            "groups": default_groups(),
            "default_group": {"enabled": True, "id": "misc"},
        },
        "refresh": {
            "auto": True,
            "mode": "replace",
            "check_ttl_seconds": 300,
            "min_upload_interval_seconds": 900,
            "max_staleness_seconds": 86400,
            "keep_previous_sources": 0,
            "delete_previous_after_success": True,
        },
        "safety": {
            "require_user_approval_first_upload": True,
            "never_upload": [
                ".env*",
                "**/.env*",
                ".git/**",
                "**/.git/**",
                "node_modules/**",
                "**/node_modules/**",
                "target/**",
                "**/target/**",
                "dist/**",
                "**/dist/**",
                "build/**",
                "**/build/**",
                "coverage/**",
                "**/coverage/**",
                ".next/**",
                "**/.next/**",
                ".generated/**",
                "**/.generated/**",
                "public/**",
                "**/public/**",
                "*.png",
                "**/*.png",
                "*.jpg",
                "**/*.jpg",
                "*.jpeg",
                "**/*.jpeg",
                "*.gif",
                "**/*.gif",
                "*.webp",
                "**/*.webp",
                "*.svg",
                "**/*.svg",
                "*.ico",
                "**/*.ico",
                "*.otf",
                "**/*.otf",
                "*.ttf",
                "**/*.ttf",
                "*.woff",
                "**/*.woff",
                "*.woff2",
                "**/*.woff2",
                "*.mp4",
                "**/*.mp4",
                "*.mov",
                "**/*.mov",
                "*.zip",
                "**/*.zip",
                "*.tar",
                "**/*.tar",
                "*.gz",
                "**/*.gz",
            ],
        },
        "retrieval": {
            "line_numbers_require_local_verify": True,
            "max_local_matches": 80,
        },
    }


def config_path(repo: Path) -> Path:
    candidates = [
        repo / CONFIG_DIR / CONFIG_JSON,
        repo / CONFIG_DIR / "config.yaml",
        repo / CONFIG_DIR / "config.yml",
        repo / ".notebooklm" / CONFIG_JSON,
        repo / ".notebooklm" / "config.yaml",
        repo / ".notebooklm" / "config.yml",
    ]
    for path in candidates:
        if path.exists():
            return path
    return repo / CONFIG_DIR / CONFIG_JSON


def load_config(repo: Path, *, command: str = "") -> tuple[dict[str, Any], Path]:
    path = config_path(repo)
    if not path.exists():
        die(missing_config_message(repo, path, command))
    if path.suffix == ".json":
        return json.loads(path.read_text()), path
    try:
        import yaml  # type: ignore
    except Exception as error:  # pragma: no cover - depends on host env
        die(f"YAML config requires PyYAML or use JSON config instead: {error}")
    return yaml.safe_load(path.read_text()), path


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def load_state(config_file: Path) -> tuple[dict[str, Any], Path]:
    state_path = config_file.parent / STATE_JSON
    if state_path.exists():
        return json.loads(state_path.read_text()), state_path
    return {"sources": []}, state_path


def include_specs(config: dict[str, Any]) -> list[str]:
    include = config.get("bundle", {}).get("include") or default_include()
    return [str(item).strip().strip("/") for item in include if str(item).strip()]


def group_specs(group: dict[str, Any]) -> list[str]:
    include = group.get("include") or []
    return [str(item).strip().strip("/") for item in include if str(item).strip()]


def never_upload_specs(config: dict[str, Any]) -> list[str]:
    built_in = [
        ".git/**",
        "**/.git/**",
        ".env*",
        "**/.env*",
        "node_modules/**",
        "**/node_modules/**",
        ".next/**",
        "**/.next/**",
        "dist/**",
        "**/dist/**",
        "build/**",
        "**/build/**",
        "coverage/**",
        "**/coverage/**",
        ".generated/**",
        "**/.generated/**",
        "public/**",
        "**/public/**",
        "*.png",
        "**/*.png",
        "*.jpg",
        "**/*.jpg",
        "*.jpeg",
        "**/*.jpeg",
        "*.gif",
        "**/*.gif",
        "*.webp",
        "**/*.webp",
        "*.svg",
        "**/*.svg",
        "*.ico",
        "**/*.ico",
        "*.otf",
        "**/*.otf",
        "*.ttf",
        "**/*.ttf",
        "*.woff",
        "**/*.woff",
        "*.woff2",
        "**/*.woff2",
        "*.mp4",
        "**/*.mp4",
        "*.mov",
        "**/*.mov",
        "*.zip",
        "**/*.zip",
        "*.tar",
        "**/*.tar",
        "*.gz",
        "**/*.gz",
    ]
    never_upload = config.get("safety", {}).get("never_upload") or []
    return [str(item).strip() for item in [*built_in, *never_upload] if str(item).strip()]


def path_matches_spec(path: str, spec: str) -> bool:
    clean = path.strip().lstrip("./")
    pattern = spec.strip().lstrip("./")
    if not pattern:
        return False
    if pattern in {".", "*"}:
        return True
    if clean == pattern or clean.startswith(pattern.rstrip("/") + "/"):
        return True
    return fnmatch.fnmatch(clean, pattern) or fnmatch.fnmatch("./" + clean, pattern)


def path_is_included(path: str, includes: list[str]) -> bool:
    for spec in includes:
        if path_matches_spec(path, spec):
            return True
    return False


def path_is_ignored(path: str, ignores: list[str]) -> bool:
    return any(path_matches_spec(path, spec) for spec in ignores)


def bundle_mode(config: dict[str, Any]) -> str:
    return str(config.get("bundle", {}).get("mode") or "chunked")


def parse_size_bytes(value: Any, fallback: int) -> int:
    if isinstance(value, int):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return fallback
    match = re.fullmatch(r"(\d+)(?:\s*(b|kb|kib|mb|mib))?", text)
    if not match:
        return fallback
    amount = int(match.group(1))
    unit = match.group(2) or "b"
    if unit in {"kb", "kib"}:
        return amount * 1024
    if unit in {"mb", "mib"}:
        return amount * 1024 * 1024
    return amount


def positive_int(value: Any, fallback: int, *, minimum: int = 1, maximum: int = 32) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def list_git_files(repo: Path) -> list[str]:
    result = run(["git", "ls-files", "-co", "--exclude-standard"], repo)
    if result.returncode != 0:
        files: list[str] = []
        for path in repo.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(repo).as_posix()
            if rel.startswith(".git/"):
                continue
            files.append(rel)
        return sorted(files)
    return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())


def collect_bundle_files(repo: Path, config: dict[str, Any]) -> list[str]:
    includes = include_specs(config)
    ignores = never_upload_specs(config)
    files: list[str] = []
    for path in list_git_files(repo):
        if not path_is_included(path, includes):
            continue
        if path_is_ignored(path, ignores):
            continue
        full = repo / path
        if not full.is_file() or full.is_symlink():
            continue
        files.append(path)
    return sorted(set(files))


def chunk_file_size(repo: Path, path: str) -> int:
    full = repo / path
    return full.stat().st_size + len(path.encode("utf-8")) + 64


def file_bucket(path: str) -> str:
    parts = path.split("/")
    if len(parts) >= 3 and parts[0] in {"apps", "packages", "crates"}:
        return "/".join(parts[:3])
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0]


def source_title_for_chunk(config: dict[str, Any], *, set_id: str, group: str, index: int, chunk_hash: str) -> str:
    configured = str(config.get("notebooklm", {}).get("source_title_prefix") or "").strip()
    legacy = configured.startswith("codebase-retrieve-")
    prefix = default_short_source_title_prefix() if legacy or not configured else configured
    template = str(
        config.get("bundle", {}).get("source_title_template")
        or "{prefix}--{set}--{group}--{chunk}--{hash}.md"
    )
    return template.format(
        prefix=slugify(prefix),
        set=set_id,
        set_id=set_id,
        group=slugify(group),
        chunk=f"{index:03d}",
        idx=f"{index:03d}",
        hash=chunk_hash[:8],
    )


def chunk_hash_for_files(repo: Path, files: list[str]) -> str:
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        full = repo / path
        if full.is_file():
            with full.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
        digest.update(b"\0")
    return digest.hexdigest()


def assign_files_to_groups(files: list[str], config: dict[str, Any]) -> list[tuple[str, str]]:
    bundle = config.get("bundle", {})
    groups = bundle.get("groups") if "groups" in bundle else default_groups()
    groups = groups or []
    assigned: list[tuple[str, str]] = []
    seen: set[str] = set()
    for group in groups:
        gid = slugify(str(group.get("id") or "group"))
        specs = group_specs(group)
        for path in files:
            if path in seen:
                continue
            if specs and path_is_included(path, specs):
                assigned.append((gid, path))
                seen.add(path)
    default_group = bundle.get("default_group") if "default_group" in bundle else {"enabled": True, "id": "misc"}
    default_group = default_group or {}
    if default_group.get("enabled"):
        gid = slugify(str(default_group.get("id") or "misc"))
        for path in files:
            if path not in seen:
                assigned.append((gid, path))
                seen.add(path)
    elif not groups:
        for path in files:
            assigned.append(("repo", path))
    return assigned


def flush_chunk(chunks: list[dict[str, Any]], repo: Path, config: dict[str, Any], set_id: str, group: str, index: int, files: list[str], total: int) -> None:
    if not files:
        return
    digest = chunk_hash_for_files(repo, files)
    chunks.append(
        {
            "group": group,
            "chunk": f"{index:03d}",
            "index": index,
            "files": files[:],
            "estimatedBytes": total,
            "sha256": "sha256:" + digest,
            "title": source_title_for_chunk(config, set_id=set_id, group=group, index=index, chunk_hash=digest),
        }
    )


def active_chunk_file_members(state: dict[str, Any] | None, group: str) -> list[list[str]]:
    if not state:
        return []
    members: list[tuple[int, list[str]]] = []
    for source in active_sources(state):
        if str(source.get("group") or "") != group:
            continue
        files = source.get("files")
        if not isinstance(files, list) or not files:
            continue
        chunk = str(source.get("chunk") or "0")
        try:
            index = int(chunk)
        except ValueError:
            index = 0
        clean_files = [str(path) for path in files if str(path)]
        if clean_files:
            members.append((index, clean_files))
    return [files for _, files in sorted(members, key=lambda item: item[0])]


def append_greedy_chunks(
    chunks: list[dict[str, Any]],
    repo: Path,
    config: dict[str, Any],
    *,
    set_id: str,
    group: str,
    start_index: int,
    files: list[str],
    target: int,
    max_bytes: int,
) -> int:
    current: list[str] = []
    current_size = 0
    index = start_index
    for path in files:
        size = chunk_file_size(repo, path)
        if size > max_bytes:
            die(f"file exceeds max chunk size ({max_bytes} bytes): {path} ({size} bytes)")
        if current and current_size + size > target:
            flush_chunk(chunks, repo, config, set_id, group, index, current, current_size)
            current = []
            current_size = 0
            index += 1
        current.append(path)
        current_size += size
    if current:
        flush_chunk(chunks, repo, config, set_id, group, index, current, current_size)
        index += 1
    return index


def plan_group_chunks(
    chunks: list[dict[str, Any]],
    repo: Path,
    config: dict[str, Any],
    *,
    set_id: str,
    group: str,
    files: list[str],
    target: int,
    max_bytes: int,
    state: dict[str, Any] | None,
) -> None:
    ordered = sorted(files, key=lambda path: (file_bucket(path), path))
    available = set(ordered)
    kept: list[list[str]] = []
    for previous_files in active_chunk_file_members(state, group):
        retained = [path for path in previous_files if path in available]
        if not retained:
            continue
        total = sum(chunk_file_size(repo, path) for path in retained)
        if any(chunk_file_size(repo, path) > max_bytes for path in retained):
            for path in retained:
                size = chunk_file_size(repo, path)
                if size > max_bytes:
                    die(f"file exceeds max chunk size ({max_bytes} bytes): {path} ({size} bytes)")
        if total <= max_bytes:
            kept.append(retained)
            for path in retained:
                available.discard(path)

    index = 1
    for files_in_chunk in kept:
        total = sum(chunk_file_size(repo, path) for path in files_in_chunk)
        flush_chunk(chunks, repo, config, set_id, group, index, files_in_chunk, total)
        index += 1

    remaining = [path for path in ordered if path in available]
    append_greedy_chunks(
        chunks,
        repo,
        config,
        set_id=set_id,
        group=group,
        start_index=index,
        files=remaining,
        target=target,
        max_bytes=max_bytes,
    )


def plan_bundle_chunks(repo: Path, config: dict[str, Any], *, set_id: str, state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    bundle = config.get("bundle", {})
    target = parse_size_bytes(bundle.get("target_chunk_bytes"), 716800)
    max_bytes = parse_size_bytes(bundle.get("max_chunk_bytes"), 900000)
    if target > max_bytes:
        target = max_bytes
    assigned = assign_files_to_groups(collect_bundle_files(repo, config), config)
    by_group: dict[str, list[str]] = {}
    for group, path in assigned:
        by_group.setdefault(group, []).append(path)
    chunks: list[dict[str, Any]] = []
    for group in sorted(by_group):
        plan_group_chunks(
            chunks,
            repo,
            config,
            set_id=set_id,
            group=group,
            files=by_group[group],
            target=target,
            max_bytes=max_bytes,
            state=state,
        )
    return chunks


def git_head(repo: Path) -> str:
    result = run(["git", "rev-parse", "HEAD"], repo)
    if result.returncode != 0:
        return "no-git-head"
    return result.stdout.strip()


def git_status_records(repo: Path) -> list[tuple[str, str]]:
    result = run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], repo)
    if result.returncode != 0:
        return []
    raw = [part for part in result.stdout.split("\0") if part]
    records: list[tuple[str, str]] = []
    skip_next = False
    for item in raw:
        if skip_next:
            skip_next = False
            continue
        status = item[:2]
        path = item[3:]
        if status.startswith("R") or status.startswith("C"):
            skip_next = True
        records.append((status, path))
    return records


def fast_fingerprint(repo: Path, config: dict[str, Any], config_file: Path) -> tuple[str, list[str]]:
    includes = include_specs(config)
    ignores = never_upload_specs(config)
    parts = [f"head={git_head(repo)}", f"config={sha256_file(config_file)}"]
    relevant_paths: list[str] = []
    for status, path in git_status_records(repo):
        if not path_is_included(path, includes) or path_is_ignored(path, ignores):
            continue
        relevant_paths.append(path)
        full = repo / path
        if full.is_file():
            content_hash = sha256_file(full)
        elif full.exists():
            content_hash = "dir"
        else:
            content_hash = "missing"
        parts.append(f"{status} {path} {content_hash}")
    return sha256_text("\n".join(parts)), relevant_paths


def seconds_since(value: str | None) -> float | None:
    parsed = parse_iso(value)
    if not parsed:
        return None
    return (now_utc() - parsed).total_seconds()


def state_uploaded_fingerprint(state: dict[str, Any]) -> str | None:
    return state.get("lastUploadedFastFingerprint")


def expand_bundle_path(repo: Path, config: dict[str, Any]) -> Path:
    prefix = config.get("notebooklm", {}).get("source_title_prefix") or f"{repo.name}-repo"
    timestamp = now_utc().strftime("%Y%m%dT%H%M%SZ")
    template = config.get("bundle", {}).get("output") or f"{CONFIG_DIR}/cache/{{prefix}}-{{timestamp}}.txt"
    rel = template.format(prefix=prefix, timestamp=timestamp)
    return repo / rel


def expand_chunk_path(repo: Path, config: dict[str, Any], title: str) -> Path:
    template = config.get("bundle", {}).get("output") or f"{CONFIG_DIR}/cache/{{title}}"
    if "{title}" in template:
        rel = template.format(title=title, prefix=config.get("notebooklm", {}).get("source_title_prefix") or default_short_source_title_prefix(), timestamp=now_utc().strftime("%Y%m%dT%H%M%SZ"))
        return repo / rel
    base = repo / template
    return base.parent / title


def repomix_cmd() -> list[str]:
    found = shutil.which("repomix")
    if found:
        return [found]
    if shutil.which("npx"):
        return ["npx", "repomix"]
    die("required tool not found on PATH: repomix or npx")


def repomix_base_argv(config: dict[str, Any]) -> list[str]:
    argv = repomix_cmd()
    bundle = config.get("bundle", {})
    style = str(bundle.get("style") or "").strip()
    if style:
        argv.extend(["--style", style])
    if bundle.get("compress"):
        argv.append("--compress")
    ignore = ",".join(never_upload_specs(config))
    if ignore:
        argv.extend(["--ignore", ignore])
    return argv


def build_bundle(repo: Path, config: dict[str, Any]) -> Path:
    out = expand_bundle_path(repo, config)
    out.parent.mkdir(parents=True, exist_ok=True)
    include = ",".join(include_specs(config))
    argv = [*repomix_base_argv(config), "--include", include, "--output", str(out)]
    result = run(argv, repo, timeout=600)
    if result.returncode != 0:
        die(f"repomix failed:\n{result.stdout}\n{result.stderr}")
    return out


def build_bundle_set(repo: Path, config: dict[str, Any], *, set_id: str, state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    max_bytes = parse_size_bytes(config.get("bundle", {}).get("max_chunk_bytes"), 900000)
    chunks = plan_bundle_chunks(repo, config, set_id=set_id, state=state)
    bundles: list[dict[str, Any]] = []
    try:
        for chunk in chunks:
            title = str(chunk["title"])
            out = expand_chunk_path(repo, config, title)
            out.parent.mkdir(parents=True, exist_ok=True)
            input_text = "\n".join(str(path) for path in chunk["files"]) + "\n"
            argv = [*repomix_base_argv(config), "--stdin", "--output", str(out)]
            result = run(argv, repo, input_text=input_text, timeout=600)
            if result.returncode != 0:
                die(f"repomix failed for chunk {title}:\n{result.stdout}\n{result.stderr}")
            actual_size = out.stat().st_size
            if actual_size > max_bytes:
                die(f"rendered chunk exceeds max size ({max_bytes} bytes): {title} ({actual_size} bytes)")
            item = dict(chunk)
            item["path"] = str(out)
            item["bundleSha256"] = sha256_file(out)
            item["contentSha256"] = item["bundleSha256"]
            item["fileListSha256"] = item.get("sha256")
            item["actualBytes"] = actual_size
            item["fileCount"] = len(chunk["files"])
            bundles.append(item)
    except BaseException:
        for bundle in bundles:
            if bundle.get("path"):
                remove_file_quiet(Path(str(bundle["path"])))
        raise
    return bundles


def notebook_id(config: dict[str, Any]) -> str:
    value = config.get("notebooklm", {}).get("notebook_id", "")
    if not value:
        die("notebooklm.notebook_id missing in config")
    return str(value)


def notebook_title(config: dict[str, Any]) -> str:
    project = str(config.get("project", {}).get("name") or "repo")
    prefix = str(config.get("notebooklm", {}).get("notebook_title_prefix") or DEFAULT_NOTEBOOK_TITLE_PREFIX)
    return str(config.get("notebooklm", {}).get("notebook_title") or default_notebook_title(project, prefix))


def parse_notebook_json(stdout: str, fallback_title: str) -> dict[str, Any] | None:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    candidates = [data]
    if isinstance(data, dict):
        for key in ("notebook", "data", "result"):
            value = data.get(key)
            if isinstance(value, dict):
                candidates.append(value)
    for item in candidates:
        if not isinstance(item, dict):
            continue
        nid = item.get("id") or item.get("notebook_id") or item.get("notebookId")
        title = item.get("title") or item.get("name") or fallback_title
        if nid:
            return {"id": str(nid), "title": str(title)}
    return None


def list_notebooks(repo: Path) -> list[dict[str, Any]]:
    result = run([*notebooklm_cmd(), "list", "--json"], repo, timeout=120)
    if result.returncode != 0:
        die(f"notebooklm list failed:\n{result.stdout}\n{result.stderr}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        die(f"notebooklm list returned invalid JSON: {error}")
    notebooks = data.get("notebooks", data if isinstance(data, list) else [])
    return [item for item in notebooks if isinstance(item, dict)]


def find_notebook_by_title(repo: Path, title: str) -> dict[str, Any] | None:
    matches = [item for item in list_notebooks(repo) if str(item.get("title", "")) == title]
    if len(matches) > 1:
        ids = ", ".join(str(item.get("id", "")) for item in matches)
        die(f"multiple notebooks found with title {title!r}: {ids}")
    if not matches:
        return None
    item = matches[0]
    return {"id": str(item.get("id", "")), "title": str(item.get("title", title))}


def create_notebook(repo: Path, title: str) -> dict[str, Any]:
    result = run([*notebooklm_cmd(), "create", title, "--json"], repo, timeout=180)
    if result.returncode != 0:
        die(f"notebooklm create failed:\n{result.stdout}\n{result.stderr}")
    notebook = parse_notebook_json(result.stdout, title)
    if notebook:
        return notebook
    found = find_notebook_by_title(repo, title)
    if found:
        return found
    die(f"created notebook but could not resolve notebook id for title {title!r}")


def list_sources(repo: Path, nbid: str) -> list[dict[str, Any]]:
    result = run([*notebooklm_cmd(), "source", "list", "-n", nbid, "--json"], repo, timeout=120)
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    sources = data.get("sources", data if isinstance(data, list) else [])
    return [src for src in sources if isinstance(src, dict)]


def find_source_by_title(repo: Path, nbid: str, title: str) -> dict[str, Any] | None:
    for src in list_sources(repo, nbid):
        if str(src.get("title", "")) != title:
            continue
        sid = src.get("id")
        if sid:
            return {"id": str(sid), "title": title}
    return None


def find_uploaded_source(before: list[dict[str, Any]], after: list[dict[str, Any]], bundle: Path, prefix: str, title_hint: str | None = None) -> dict[str, Any]:
    before_ids = {str(src.get("id")) for src in before if src.get("id")}
    basename = title_hint or bundle.name
    for src in after:
        title = str(src.get("title", ""))
        sid = str(src.get("id", ""))
        if sid and sid not in before_ids and (title == basename or title.startswith(prefix)):
            return {"id": sid, "title": title or basename}
    for src in after:
        title = str(src.get("title", ""))
        sid = str(src.get("id", ""))
        if sid and (title == basename or title.startswith(prefix)):
            return {"id": sid, "title": title or basename}
    return {"id": "", "title": basename}


def source_from_add_json(stdout: str, bundle: Path, title_hint: str | None = None) -> dict[str, Any] | None:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    candidates = [data]
    if isinstance(data, dict):
        for key in ("source", "data", "result"):
            value = data.get(key)
            if isinstance(value, dict):
                candidates.append(value)
    for item in candidates:
        if not isinstance(item, dict):
            continue
        sid = item.get("id") or item.get("source_id") or item.get("sourceId")
        title = item.get("title") or item.get("name") or title_hint or bundle.name
        if sid:
            return {"id": str(sid), "title": str(title)}
    return None


def upload_bundle(repo: Path, config: dict[str, Any], state: dict[str, Any], bundle: Path, bundle_hash: str) -> dict[str, Any]:
    nbid = notebook_id(config)
    prefix = str(config.get("notebooklm", {}).get("source_title_prefix") or bundle.stem)
    before = list_sources(repo, nbid)
    result = run([*notebooklm_cmd(), "source", "add", str(bundle), "-n", nbid, "--json"], repo, timeout=600)
    if result.returncode != 0:
        die(f"notebooklm source add failed:\n{result.stdout}\n{result.stderr}")
    after = list_sources(repo, nbid)
    source = source_from_add_json(result.stdout, bundle) or find_uploaded_source(before, after, bundle, prefix)
    source.update({"bundleSha256": bundle_hash, "uploadedAt": iso()})

    if config.get("notebooklm", {}).get("wait_after_upload") and source.get("id"):
        wait = run([*notebooklm_cmd(), "source", "wait", str(source["id"]), "-n", nbid], repo, timeout=600)
        if wait.returncode != 0:
            print(f"warning: source wait failed for {source['id']}", file=sys.stderr)

    if config.get("refresh", {}).get("mode", "replace") == "replace":
        pruned_ids = prune_sources(repo, config, state, source)
        if pruned_ids:
            source["_prunedSourceIds"] = pruned_ids
    return source


def upload_file_source(repo: Path, config: dict[str, Any], path: Path, title: str) -> dict[str, Any]:
    nbid = notebook_id(config)
    result = run([*notebooklm_cmd(), "source", "add", str(path), "-n", nbid, "--title", title, "--json"], repo, timeout=600)
    if result.returncode != 0:
        die(f"notebooklm source add failed for {title}:\n{result.stdout}\n{result.stderr}")
    source = source_from_add_json(result.stdout, path, title) or find_source_by_title(repo, nbid, title)
    if not source or not source.get("id"):
        die(f"uploaded source but could not resolve source id for {title}")
    return source


def wait_source_ready(repo: Path, nbid: str, source_id: str) -> bool:
    wait = run([*notebooklm_cmd(), "source", "wait", source_id, "-n", nbid], repo, timeout=600)
    return wait.returncode == 0


def source_content_sha(value: dict[str, Any]) -> str:
    return str(value.get("contentSha256") or value.get("chunkSha256") or value.get("bundleSha256") or "")


def source_file_list_sha(value: dict[str, Any]) -> str:
    return str(value.get("fileListSha256") or value.get("sha256") or "")


def chunk_key(value: dict[str, Any]) -> str:
    return f"{value.get('group')}/{value.get('chunk')}"


def temp_source_prefix(config: dict[str, Any]) -> str:
    prefix = str(config.get("notebooklm", {}).get("temporary_source_title_prefix") or "").strip()
    if prefix:
        return slugify(prefix)
    return f"{str(config.get('notebooklm', {}).get('source_title_prefix') or default_short_source_title_prefix()).strip()}tmp"


def temp_source_title(config: dict[str, Any], *, set_id: str, kind: str, title: str, content_sha: str) -> str:
    digest = content_sha.split(":", 1)[-1]
    return f"{temp_source_prefix(config)}--{set_id}--{slugify(kind)}--{slugify(title)}--{digest[:8]}.md"


def stage_temp_source_file(repo: Path, title: str, source_path: Path) -> Path:
    staged = repo / CONFIG_DIR / "cache" / title
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, staged)
    return staged


def source_with_chunk_metadata(source: dict[str, Any], bundle: dict[str, Any], *, status: str, reused: bool = False) -> dict[str, Any]:
    item = dict(source)
    item.update(
        {
            "group": bundle.get("group"),
            "chunk": bundle.get("chunk"),
            "chunkKey": chunk_key(bundle),
            "chunkSha256": bundle.get("bundleSha256"),
            "contentSha256": bundle.get("contentSha256") or bundle.get("bundleSha256"),
            "fileListSha256": bundle.get("fileListSha256") or bundle.get("sha256"),
            "fileCount": bundle.get("fileCount"),
            "files": list(bundle.get("files", [])),
            "status": status,
        }
    )
    if reused:
        item["reused"] = True
        item["reusedAt"] = iso()
    else:
        item["uploadedAt"] = iso()
    return item


def upload_one_chunk(repo: Path, config: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    nbid = notebook_id(config)
    path = Path(str(bundle["path"]))
    title = str(bundle["title"])
    result = run([*notebooklm_cmd(), "source", "add", str(path), "-n", nbid, "--title", title, "--json"], repo, timeout=600)
    if result.returncode != 0:
        die(f"notebooklm source add failed for chunk {title}:\n{result.stdout}\n{result.stderr}")
    source = source_from_add_json(result.stdout, path, title) or find_source_by_title(repo, nbid, title)
    if not source or not source.get("id"):
        die(f"uploaded chunk but could not resolve source id for {title}")
    return source_with_chunk_metadata(source, bundle, status="uploaded")


def source_set_hash(bundles: list[dict[str, Any]]) -> str:
    parts = [
        f"{bundle.get('group')} {bundle.get('chunk')} {source_content_sha(bundle)} {source_file_list_sha(bundle)}"
        for bundle in bundles
    ]
    return sha256_text("\n".join(parts))


def active_sources(state: dict[str, Any]) -> list[dict[str, Any]]:
    source_set = state.get("activeSourceSet")
    if isinstance(source_set, dict):
        sources = source_set.get("sources")
        if isinstance(sources, list):
            return [src for src in sources if isinstance(src, dict)]
    return [src for src in state.get("sources", []) if isinstance(src, dict)]


def active_ready_source_ids(state: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for src in active_sources(state):
        sid = str(src.get("id") or "")
        if not sid:
            continue
        status = str(src.get("status") or "ready")
        if status == "ready":
            ids.append(sid)
    return ids


def cleanup_pending_source_ids(state: dict[str, Any]) -> list[str]:
    raw = state.get("cleanupPendingSourceIds")
    if not isinstance(raw, list):
        return []
    return [sid for sid in dict.fromkeys(str(item) for item in raw if str(item))]


def queue_cleanup_source_ids(state: dict[str, Any], source_ids: list[str]) -> list[str]:
    active_ids = {str(src.get("id") or "") for src in active_sources(state) if src.get("id")}
    merged = [sid for sid in dict.fromkeys([*cleanup_pending_source_ids(state), *source_ids]) if sid and sid not in active_ids]
    if merged:
        state["cleanupPendingSourceIds"] = merged
    else:
        state.pop("cleanupPendingSourceIds", None)
    return merged


def pending_upload_path(repo: Path) -> Path:
    return repo / CONFIG_DIR / PENDING_UPLOAD_JSON


def clear_pending_upload(repo: Path) -> None:
    remove_file_quiet(pending_upload_path(repo))


def write_pending_upload(repo: Path, value: dict[str, Any]) -> None:
    write_json(pending_upload_path(repo), value)


def read_pending_upload(repo: Path) -> dict[str, Any] | None:
    path = pending_upload_path(repo)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"sources": []}
    return data if isinstance(data, dict) else {"sources": []}


def delete_source_ids_parallel(repo: Path, nbid: str, source_ids: list[str], *, parallelism: int) -> list[str]:
    ids = [sid for sid in dict.fromkeys(source_ids) if sid]
    if not ids:
        return []
    workers = min(len(ids), max(1, parallelism))

    def delete_one(sid: str) -> str | None:
        result = run([*notebooklm_cmd(), "source", "delete", sid, "-n", nbid, "--yes"], repo, timeout=120)
        if result.returncode != 0:
            print(f"warning: failed to delete source {sid}", file=sys.stderr)
            return None
        return sid

    deleted: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(delete_one, sid) for sid in ids]
        for future in concurrent.futures.as_completed(futures):
            sid = future.result()
            if sid:
                deleted.append(sid)
                print(f"cleanup {len(deleted)}/{len(ids)}", file=sys.stderr)
    return deleted


def recover_pending_cleanup(repo: Path, config: dict[str, Any], state: dict[str, Any], state_path: Path) -> list[str]:
    pending_ids = cleanup_pending_source_ids(state)
    if not pending_ids:
        return []
    active_ids = {str(src.get("id") or "") for src in active_sources(state) if src.get("id")}
    delete_ids = [sid for sid in pending_ids if sid not in active_ids]
    if not delete_ids:
        state.pop("cleanupPendingSourceIds", None)
        write_json(state_path, state)
        return []
    deleted = delete_source_ids_parallel(
        repo,
        notebook_id(config),
        delete_ids,
        parallelism=positive_int(config.get("notebooklm", {}).get("delete_parallelism"), 4),
    )
    deleted_set = set(deleted)
    remaining = [sid for sid in pending_ids if sid not in deleted_set and sid not in active_ids]
    if remaining:
        state["cleanupPendingSourceIds"] = remaining
    else:
        state.pop("cleanupPendingSourceIds", None)
    write_json(state_path, state)
    return deleted


def recover_pending_upload(repo: Path, config: dict[str, Any], state: dict[str, Any] | None = None) -> list[str]:
    pending = read_pending_upload(repo)
    if not pending:
        return []
    sources = pending.get("sources")
    if not isinstance(sources, list):
        clear_pending_upload(repo)
        return []
    active_ids = {str(src.get("id")) for src in active_sources(state or {}) if src.get("id")}
    ids = [str(src.get("id")) for src in sources if isinstance(src, dict) and src.get("id")]
    if ids and active_ids and all(sid in active_ids for sid in ids):
        clear_pending_upload(repo)
        return []
    nbid = str(pending.get("notebookId") or notebook_id(config))
    delete_ids = [sid for sid in ids if sid not in active_ids]
    deleted = delete_source_ids_parallel(
        repo,
        nbid,
        delete_ids,
        parallelism=positive_int(config.get("notebooklm", {}).get("delete_parallelism"), 4),
    )
    remaining = [src for src in sources if isinstance(src, dict) and str(src.get("id") or "") not in set(deleted)]
    if remaining:
        pending["sources"] = remaining
        write_pending_upload(repo, pending)
    else:
        clear_pending_upload(repo)
    return deleted


def append_pending_source(repo: Path, journal: dict[str, Any], source: dict[str, Any], lock: threading.Lock) -> None:
    with lock:
        sources = journal.setdefault("sources", [])
        if isinstance(sources, list):
            sources.append({"id": source.get("id"), "title": source.get("title")})
        write_pending_upload(repo, journal)


def find_reusable_source(bundle: dict[str, Any], previous_sources: list[dict[str, Any]], used_ids: set[str]) -> dict[str, Any] | None:
    wanted = source_content_sha(bundle)
    if not wanted:
        return None
    for source in previous_sources:
        sid = str(source.get("id") or "")
        if not sid or sid in used_ids:
            continue
        if str(source.get("status") or "ready") != "ready":
            continue
        if source_content_sha(source) == wanted:
            used_ids.add(sid)
            return source
    return None


def upload_chunks_parallel(repo: Path, config: dict[str, Any], bundles: list[tuple[int, dict[str, Any]]], *, set_id: str) -> list[tuple[int, dict[str, Any]]]:
    if not bundles:
        return []
    nbid = notebook_id(config)
    workers = min(
        len(bundles),
        positive_int(config.get("notebooklm", {}).get("upload_parallelism"), 4),
    )
    journal: dict[str, Any] = {
        "version": 1,
        "setId": set_id,
        "notebookId": nbid,
        "startedAt": iso(),
        "sources": [],
    }
    write_pending_upload(repo, journal)
    journal_lock = threading.Lock()
    uploaded: list[tuple[int, dict[str, Any]]] = []
    errors: list[BaseException] = []

    def upload_pair(pair: tuple[int, dict[str, Any]]) -> tuple[int, dict[str, Any]]:
        index, bundle = pair
        source = upload_one_chunk(repo, config, bundle)
        append_pending_source(repo, journal, source, journal_lock)
        return index, source

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(upload_pair, pair) for pair in bundles]
        for future in concurrent.futures.as_completed(futures):
            try:
                item = future.result()
                uploaded.append(item)
                print(f"upload {len(uploaded)}/{len(bundles)}", file=sys.stderr)
            except BaseException as error:
                errors.append(error)

    if errors:
        delete_source_ids_parallel(
            repo,
            nbid,
            [str(source.get("id") or "") for _, source in uploaded],
            parallelism=positive_int(config.get("notebooklm", {}).get("delete_parallelism"), 4),
        )
        clear_pending_upload(repo)
        raise errors[0]
    return sorted(uploaded, key=lambda item: item[0])


def wait_uploaded_sources_parallel(repo: Path, config: dict[str, Any], sources: list[tuple[int, dict[str, Any]]]) -> list[tuple[int, dict[str, Any]]]:
    if not sources or not config.get("notebooklm", {}).get("wait_after_upload", True):
        return sources
    nbid = notebook_id(config)
    workers = min(
        len(sources),
        positive_int(config.get("notebooklm", {}).get("wait_parallelism"), 8),
    )
    ready: list[tuple[int, dict[str, Any]]] = []
    errors: list[str] = []

    def wait_one(pair: tuple[int, dict[str, Any]]) -> tuple[int, dict[str, Any]]:
        index, source = pair
        sid = str(source.get("id") or "")
        if not sid:
            raise RuntimeError(f"missing source id for {source.get('title')}")
        if not wait_source_ready(repo, nbid, sid):
            raise RuntimeError(f"source processing failed for chunk {source.get('title')}: {sid}")
        item = dict(source)
        item["status"] = "ready"
        return index, item

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(wait_one, pair) for pair in sources]
        for future in concurrent.futures.as_completed(futures):
            try:
                item = future.result()
                ready.append(item)
                print(f"wait {len(ready)}/{len(sources)}", file=sys.stderr)
            except Exception as error:
                errors.append(str(error))
    if errors:
        die("\n".join(errors))
    return sorted(ready, key=lambda item: item[0])


def upload_bundle_set(repo: Path, config: dict[str, Any], state: dict[str, Any], bundles: list[dict[str, Any]], *, set_id: str) -> dict[str, Any]:
    nbid = notebook_id(config)
    recover_pending_upload(repo, config, state)
    previous_sources = active_sources(state)
    used_reuse_ids: set[str] = set()
    sources_by_index: list[dict[str, Any] | None] = [None] * len(bundles)
    upload_pairs: list[tuple[int, dict[str, Any]]] = []
    for index, bundle in enumerate(bundles):
        reusable = find_reusable_source(bundle, previous_sources, used_reuse_ids)
        if reusable:
            sources_by_index[index] = source_with_chunk_metadata(reusable, bundle, status="ready", reused=True)
        else:
            upload_pairs.append((index, bundle))
    uploaded_sources = upload_chunks_parallel(repo, config, upload_pairs, set_id=set_id)
    try:
        ready_sources = wait_uploaded_sources_parallel(repo, config, uploaded_sources)
    except BaseException:
        delete_source_ids_parallel(
            repo,
            nbid,
            [str(source.get("id") or "") for _, source in uploaded_sources],
            parallelism=positive_int(config.get("notebooklm", {}).get("delete_parallelism"), 4),
        )
        clear_pending_upload(repo)
        raise
    for index, source in ready_sources:
        sources_by_index[index] = source
    sources = [source for source in sources_by_index if isinstance(source, dict)]
    active_ids = {str(src.get("id")) for src in sources if src.get("id")}
    previous_ids = [str(src.get("id")) for src in previous_sources if src.get("id")]
    keep_previous = int(config.get("refresh", {}).get("keep_previous_sources", 0))
    keep_ids = set(previous_ids[-keep_previous:]) if keep_previous > 0 else set()
    retired_ids = [sid for sid in previous_ids if sid not in active_ids and sid not in keep_ids]
    source_set = {
        "id": set_id,
        "prefix": str(config.get("notebooklm", {}).get("source_title_prefix") or default_short_source_title_prefix()),
        "bundleSetSha256": source_set_hash(bundles),
        "uploadedAt": iso(),
        "sources": sources,
    }
    if config.get("refresh", {}).get("mode", "replace") == "replace" and config.get("refresh", {}).get("delete_previous_after_success", True):
        source_set["_retiredSourceIds"] = retired_ids
    return source_set


def prune_sources(repo: Path, config: dict[str, Any], state: dict[str, Any], new_source: dict[str, Any]) -> list[str]:
    refresh = config.get("refresh", {})
    if not refresh.get("delete_previous_after_success", True):
        return []
    keep_previous = int(refresh.get("keep_previous_sources", 1))
    recorded = [src for src in state.get("sources", []) if src.get("id")]
    keep_ids = {str(src.get("id")) for src in recorded[-keep_previous:]} if keep_previous > 0 else set()
    keep_ids.add(str(new_source.get("id", "")))
    nbid = notebook_id(config)
    pruned_ids: list[str] = []
    for src in recorded:
        sid = str(src.get("id", ""))
        if not sid or sid in keep_ids:
            continue
        delete = run([*notebooklm_cmd(), "source", "delete", sid, "-n", nbid, "--yes"], repo, timeout=120)
        if delete.returncode != 0:
            print(f"warning: failed to delete old source {sid}", file=sys.stderr)
        else:
            pruned_ids.append(sid)
    return pruned_ids


def ensure_index(
    repo: Path,
    *,
    force: bool = False,
    yes: bool = False,
    json_output: bool = False,
    command: str = "ensure",
    return_uninitialized: bool = False,
) -> dict[str, Any]:
    config_file = config_path(repo)
    if not config_file.exists():
        if json_output or return_uninitialized:
            return uninitialized_status(repo, config_file)
        die(missing_config_message(repo, config_file, command))
    with repo_lock(repo):
        return ensure_index_locked(repo, force=force, yes=yes, json_output=json_output, command=command)


def ensure_index_locked(repo: Path, *, force: bool = False, yes: bool = False, json_output: bool = False, command: str = "ensure") -> dict[str, Any]:
    config, cfg_path = load_config(repo, command=command)
    state, state_path = load_state(cfg_path)
    recover_pending_upload(repo, config, state)
    recover_pending_cleanup(repo, config, state, state_path)
    fast_hash, relevant_paths = fast_fingerprint(repo, config, cfg_path)
    refresh = config.get("refresh", {})
    check_ttl = int(refresh.get("check_ttl_seconds", 300))
    min_interval = int(refresh.get("min_upload_interval_seconds", 900))
    max_staleness = int(refresh.get("max_staleness_seconds", 86400))
    checked_age = seconds_since(state.get("lastCheckedAt"))
    uploaded_age = seconds_since(state.get("lastUploadedAt"))
    uploaded_fingerprint = state_uploaded_fingerprint(state)

    result: dict[str, Any] = {
        "status": "unknown",
        "config": str(cfg_path),
        "state": str(state_path),
        "relevant_changed_paths": relevant_paths,
        "fast_fingerprint": fast_hash,
    }

    if not force and checked_age is not None and checked_age < check_ttl and uploaded_fingerprint == fast_hash:
        state["lastCheckedAt"] = iso()
        state["lastCheckedFastFingerprint"] = fast_hash
        state["lastBundlePath"] = None
        write_json(state_path, state)
        result.update({"status": "fresh-ttl", "checked_age_seconds": checked_age})
        return result

    if not force and uploaded_fingerprint == fast_hash and state.get("lastUploadedAt"):
        state["lastCheckedAt"] = iso()
        state["lastCheckedFastFingerprint"] = fast_hash
        state["lastBundlePath"] = None
        write_json(state_path, state)
        result.update({"status": "fresh-fingerprint"})
        return result

    first_upload = not active_sources(state)
    if first_upload and config.get("safety", {}).get("require_user_approval_first_upload", True) and not yes and not force:
        result.update({"status": "needs-first-upload-approval"})
        return result

    if not force and uploaded_age is not None and uploaded_age < min_interval and uploaded_age < max_staleness:
        state["lastCheckedAt"] = iso()
        state["lastCheckedFastFingerprint"] = fast_hash
        state["lastBundlePath"] = None
        write_json(state_path, state)
        result.update({"status": "stale-throttled", "uploaded_age_seconds": uploaded_age})
        return result

    if not refresh.get("auto", True) and not force:
        state["lastCheckedAt"] = iso()
        state["lastCheckedFastFingerprint"] = fast_hash
        state["lastBundlePath"] = None
        write_json(state_path, state)
        result.update({"status": "auto-refresh-disabled"})
        return result

    if bundle_mode(config) == "chunked":
        set_id = now_utc().strftime("%y%m%d%H%M")
        bundles = build_bundle_set(repo, config, set_id=set_id, state=state)
        try:
            bundle_set_sha = source_set_hash(bundles)
            if not force and state.get("lastBundleSetSha256") == bundle_set_sha:
                state.update({
                    "lastCheckedAt": iso(),
                    "lastCheckedFastFingerprint": fast_hash,
                    "lastBundlePath": None,
                })
                write_json(state_path, state)
                result.update({"status": "fresh-bundle-hash", "bundleSetSha256": bundle_set_sha, "bundleDeleted": True})
                return result

            source_set = upload_bundle_set(repo, config, state, bundles, set_id=set_id)
            retired_ids = [str(sid) for sid in source_set.pop("_retiredSourceIds", []) if str(sid)]
            state.update({
                "lastCheckedAt": iso(),
                "lastUploadedAt": iso(),
                "lastConfigSha256": sha256_file(cfg_path),
                "lastCheckedFastFingerprint": fast_hash,
                "lastUploadedFastFingerprint": fast_hash,
                "lastFastFingerprint": fast_hash,
                "lastBundleSetSha256": bundle_set_sha,
                "lastBundleSha256": bundle_set_sha,
                "lastBundlePath": None,
                "activeSourceSet": source_set,
                "sources": [src for src in source_set.get("sources", []) if isinstance(src, dict)],
            })
            cleanup_pending_ids = queue_cleanup_source_ids(state, retired_ids)
            write_json(state_path, state)
            clear_pending_upload(repo)
            result.update(
                {
                    "status": "uploaded",
                    "bundleSetSha256": bundle_set_sha,
                    "bundleDeleted": True,
                    "sourceSet": source_set,
                    "cleanupPendingSourceIds": cleanup_pending_ids,
                }
            )
            return result
        finally:
            for bundle in bundles:
                if bundle.get("path"):
                    remove_file_quiet(Path(str(bundle["path"])))

    bundle = build_bundle(repo, config)
    try:
        bundle_hash = sha256_file(bundle)
        if not force and state.get("lastBundleSha256") == bundle_hash:
            state.update({
                "lastCheckedAt": iso(),
                "lastCheckedFastFingerprint": fast_hash,
                "lastBundlePath": None,
            })
            write_json(state_path, state)
            result.update({"status": "fresh-bundle-hash", "bundleSha256": bundle_hash, "bundleDeleted": True})
            return result

        source = upload_bundle(repo, config, state, bundle, bundle_hash)
        pruned_ids = set(source.pop("_prunedSourceIds", []))
        sources = [src for src in state.get("sources", []) if str(src.get("id", "")) not in pruned_ids]
        if source.get("id") or source.get("title"):
            sources.append(source)
        state.update({
            "lastCheckedAt": iso(),
            "lastUploadedAt": iso(),
            "lastConfigSha256": sha256_file(cfg_path),
            "lastCheckedFastFingerprint": fast_hash,
            "lastUploadedFastFingerprint": fast_hash,
            "lastFastFingerprint": fast_hash,
            "lastBundleSha256": bundle_hash,
            "lastBundlePath": None,
            "sources": sources,
        })
        write_json(state_path, state)
        result.update({"status": "uploaded", "bundleSha256": bundle_hash, "bundleDeleted": True, "source": source})
        return result
    finally:
        remove_file_quiet(bundle)


def ask_provider(repo: Path, question: str) -> dict[str, Any]:
    config, cfg_path = load_config(repo, command="ask")
    state, _ = load_state(cfg_path)
    nbid = notebook_id(config)
    argv = [*notebooklm_cmd(), "ask", question, "-n", nbid]
    for source_id in active_ready_source_ids(state):
        argv.extend(["-s", source_id])
    argv.append("--json")
    result = run(argv, repo, timeout=180)
    if result.returncode != 0:
        return {"error": True, "stdout": result.stdout, "stderr": result.stderr}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"answer": result.stdout}


PATH_RE = re.compile(r"(?:(?:[\w.-]+/)+[\w.@+-]+\.(?:rs|ts|tsx|js|jsx|py|go|java|kt|md|toml|yaml|yml|json|sh|sql|css|scss|html))")
TERM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}|[A-Za-z0-9][A-Za-z0-9_-]{4,}")
STOP_TERMS = {
    "agent",
    "authority",
    "btreemap",
    "bundle",
    "codex",
    "command",
    "docs",
    "fixture",
    "gate",
    "justfile",
    "keywords",
    "local",
    "names",
    "paths",
    "postgres",
    "postgresql",
    "real",
    "refs",
    "repo",
    "shell",
    "test",
    "trigger",
    "where",
    "which",
    "what",
    "when",
    "implemented",
    "implementation",
    "function",
    "tests",
    "files",
    "return",
    "likely",
    "line",
    "numbers",
    "source",
    "notebooklm",
}


def answer_text(data: dict[str, Any]) -> str:
    value = data.get("answer")
    if isinstance(value, str):
        return value
    return json.dumps(data, ensure_ascii=False)


def active_sources_by_id(repo: Path) -> dict[str, dict[str, Any]]:
    _, config_file = load_config(repo, command="ask")
    state, _ = load_state(config_file)
    by_id: dict[str, dict[str, Any]] = {}
    for source in active_sources(state):
        sid = str(source.get("id") or "")
        if sid:
            by_id[sid] = source
    return by_id


def reference_path_candidates(repo: Path, source: dict[str, Any], text: str) -> list[tuple[str, int | None]]:
    files = [str(path) for path in source.get("files", []) if str(path)]
    file_set = set(files)
    matches: list[tuple[str, int | None]] = []

    for raw in PATH_RE.findall(text):
        path = raw.strip("`'\".,;:()[]{}<>")
        if path in file_set and (repo / path).is_file():
            matches.append((path, None))

    if matches:
        return sorted(set(matches))[:5]

    snippet = " ".join(text.split())
    if len(snippet) < 4 or len(snippet) > 240 or "<directory_structure>" in text:
        return []

    for path in files:
        full = repo / path
        if not full.is_file() or full.stat().st_size > 2_000_000:
            continue
        try:
            content = full.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        line_no: int | None = None
        index = content.find(text)
        if index >= 0:
            line_no = content.count("\n", 0, index) + 1
        elif snippet not in " ".join(content.split()):
            continue
        matches.append((path, line_no))
        if len(matches) >= 5:
            break
    return matches


def format_reference_paths(paths: list[tuple[str, int | None]]) -> str:
    rendered = [f"{path}:{line}" if line else path for path, line in paths[:3]]
    suffix = "" if len(paths) <= 3 else f", ...(+{len(paths) - 3})"
    return ", ".join(rendered) + suffix


def print_compact_references(repo: Path, answer: dict[str, Any]) -> None:
    references = answer.get("references")
    if not isinstance(references, list) or not references:
        return

    sources = active_sources_by_id(repo)
    rows: list[str] = []
    seen_numbers: set[str] = set()
    for ref in references:
        if not isinstance(ref, dict):
            continue
        number = str(ref.get("citation_number") or "").strip()
        if not number or number in seen_numbers:
            continue
        seen_numbers.add(number)
        source = sources.get(str(ref.get("source_id") or ""))
        paths = reference_path_candidates(repo, source or {}, str(ref.get("cited_text") or "")) if source else []
        if paths:
            rows.append(f"[{number}] {format_reference_paths(paths)}")

    if rows:
        print("\nreferences:")
        for row in rows:
            print(row)


def extract_candidates(text: str, query: str) -> tuple[list[str], list[str]]:
    paths = sorted(set(PATH_RE.findall(text)))
    terms = set()
    for raw in TERM_RE.findall(text + "\n" + query):
        term = raw.strip("`'\"")
        if len(term) < 4 or term.lower() in STOP_TERMS:
            continue
        if "/" in term or "." in term:
            continue
        terms.add(term)
    return paths, sorted(terms)[:24]


def high_signal_terms(terms: list[str]) -> list[str]:
    selected: list[str] = []
    for term in terms:
        lower = term.lower()
        if lower in STOP_TERMS:
            continue
        has_symbol_shape = "_" in term or "-" in term or any(char.isupper() for char in term[1:])
        if has_symbol_shape or len(term) >= 14:
            selected.append(term)
    return selected or [term for term in terms if term.lower() not in STOP_TERMS][:8]


def rg_roots(repo: Path, config: dict[str, Any], candidate_paths: list[str]) -> list[list[str]]:
    candidate_roots = [path for path in candidate_paths if (repo / path).exists()]
    roots = [spec for spec in include_specs(config) if (repo / spec).exists()]
    if not roots:
        roots = ["."]
    groups: list[list[str]] = []
    if candidate_roots:
        groups.append(candidate_roots)
    groups.append(roots)
    return groups


def parse_rg_matches(stdout: str, seen: set[tuple[str, str, str]], remaining: int) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if len(matches) >= remaining:
            break
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        path, line_no, text = parts
        key = (path, line_no, text.strip())
        if key in seen:
            continue
        seen.add(key)
        matches.append({"path": path, "line": int(line_no) if line_no.isdigit() else line_no, "text": text.strip()})
    return matches


def local_rg(repo: Path, config: dict[str, Any], terms: list[str], candidate_paths: list[str] | None = None) -> list[dict[str, Any]]:
    if not terms or shutil.which("rg") is None:
        return []
    signal_terms = high_signal_terms(terms)
    pattern = "|".join(re.escape(term) for term in signal_terms[:16])
    max_matches = int(config.get("retrieval", {}).get("max_local_matches", 80))
    matches: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for roots in rg_roots(repo, config, candidate_paths or []):
        remaining = max_matches - len(matches)
        if remaining <= 0:
            break
        cmd = ["rg", "-n", "-S", "-e", pattern, "--", *roots]
        result = run(cmd, repo, timeout=120)
        if result.returncode not in (0, 1):
            return [{"error": result.stderr.strip()}]
        matches.extend(parse_rg_matches(result.stdout, seen, remaining))
    return matches


def print_result(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, (dict, list)):
                    print(f"{key}: {json.dumps(value, ensure_ascii=False)}")
                else:
                    print(f"{key}: {value}")
        else:
            print(data)


def freshness_warning(freshness: dict[str, Any]) -> str | None:
    status = str(freshness.get("status") or "")
    if status == "stale-throttled":
        changed = freshness.get("relevant_changed_paths") or []
        uploaded_age = freshness.get("uploaded_age_seconds")
        changed_text = ""
        if isinstance(changed, list) and changed:
            preview = ", ".join(str(path) for path in changed[:5])
            suffix = "" if len(changed) <= 5 else f", ...(+{len(changed) - 5})"
            changed_text = f"; changed={preview}{suffix}"
        age_text = f"; uploaded_age_seconds={uploaded_age}" if uploaded_age is not None else ""
        return f"warning: index is stale-throttled{age_text}{changed_text}; provider answer may lag local changes. Use --force-refresh or refresh --force if needed."
    if status == "needs-first-upload-approval":
        return "warning: first broad upload requires approval; rerun with --yes or run refresh explicitly."
    if status == "auto-refresh-disabled":
        return "warning: auto refresh is disabled; provider answer may lag local changes."
    return None


def provider_block_message(freshness: dict[str, Any]) -> str | None:
    status = str(freshness.get("status") or "")
    if status == "not-initialized":
        return "skipped; project is not initialized for project retrieval."
    if status == "needs-first-upload-approval":
        return "skipped; first broad upload requires approval. Rerun ask/locate with --yes or run refresh explicitly."
    return None


def first_upload_next(repo: Path, command: str, query: str) -> dict[str, str]:
    return {
        f"{command}WithFirstUploadApproval": command_line(repo, command, "--yes", query),
        "refresh": command_line(repo, "refresh", "--force"),
    }


def provider_block_payload(freshness: dict[str, Any], *, next_steps: dict[str, str] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": True, "message": provider_block_message(freshness) or "skipped"}
    block_next = freshness.get("next") or next_steps
    if block_next:
        payload["next"] = block_next
    return payload


def print_ask_result(freshness: dict[str, Any], answer: dict[str, Any], args: argparse.Namespace) -> None:
    if args.json:
        print_result({"freshness": freshness, "provider_answer": answer}, True)
        return
    repo = Path(args.repo).resolve()
    warning = freshness_warning(freshness)
    if warning:
        print(warning)
    if args.verbose:
        print(f"freshness: {json.dumps(freshness, ensure_ascii=False)}")
        metadata = {key: answer[key] for key in ("conversation_id", "turn_number", "is_follow_up") if key in answer}
        references = answer.get("references")
        if isinstance(references, list):
            metadata["references_count"] = len(references)
        if metadata:
            print(f"provider: {json.dumps(metadata, ensure_ascii=False)}")
    print(answer_text(answer))
    print_compact_references(repo, answer)


def print_locate_result(result: dict[str, Any], args: argparse.Namespace) -> None:
    if args.json:
        print_result(result, True)
        return
    warning = freshness_warning(result.get("freshness", {}))
    if warning:
        print(warning)
    if args.verbose:
        print(f"freshness: {json.dumps(result.get('freshness', {}), ensure_ascii=False)}")
    visible = {key: value for key, value in result.items() if key != "freshness"}
    print_result(visible, False)


def cmd_init(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    cfg_dir = repo / CONFIG_DIR
    cfg = cfg_dir / CONFIG_JSON
    if cfg.exists() and not args.force:
        die(f"config already exists: {cfg}")
    project_name = args.project_name or repo.name
    title_prefix = args.notebook_title_prefix or DEFAULT_NOTEBOOK_TITLE_PREFIX
    title = args.notebook_title or default_notebook_title(project_name, title_prefix)
    notebook_id_value = args.notebook_id or ""
    resolved_notebook: dict[str, Any] | None = None
    if not notebook_id_value and (args.reuse_existing_notebook or args.create_notebook):
        resolved_notebook = find_notebook_by_title(repo, title)
        if not resolved_notebook and args.create_notebook:
            resolved_notebook = create_notebook(repo, title)
        if not resolved_notebook:
            die(f"no NotebookLM notebook found with title {title!r}; pass --create-notebook or --notebook-id")
        notebook_id_value = str(resolved_notebook.get("id") or "")
    config = default_config(
        repo,
        notebook_id_value,
        project_name=project_name,
        notebook_title_prefix=title_prefix,
        notebook_title=title,
    )
    if args.include:
        config["bundle"]["include"] = [part.strip() for part in args.include.split(",") if part.strip()]
    if args.source_title_prefix:
        config["notebooklm"]["source_title_prefix"] = args.source_title_prefix
    write_json(cfg, config)
    (cfg_dir / ".gitignore").write_text("state.local.json\npending-upload.local.json\ncache/\n*.lock\n")
    print(f"created: {cfg}")
    print(f"created: {cfg_dir / '.gitignore'}")
    print(f"notebook_title: {title}")
    if resolved_notebook:
        print(f"notebook_id: {notebook_id_value}")
    if notebook_id_value:
        print("next:")
        print(f"  {command_line(repo, 'ensure', '--yes')}")
        print(f"  {command_line(repo, 'ask', 'your question')}")
    else:
        print("next:")
        print("  set notebooklm.notebook_id in the config, or rerun init with --create-notebook / --reuse-existing-notebook / --notebook-id")


def cmd_status(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    cfg_candidate = config_path(repo)
    if not cfg_candidate.exists():
        print_result(uninitialized_status(repo, cfg_candidate), args.json)
        return
    config, cfg_path = load_config(repo, command="status")
    state, state_path = load_state(cfg_path)
    fast_hash, changed = fast_fingerprint(repo, config, cfg_path)
    data = {
        "initialized": True,
        "config": str(cfg_path),
        "state": str(state_path),
        "provider": config.get("provider"),
        "projectName": config.get("project", {}).get("name"),
        "notebook_id": config.get("notebooklm", {}).get("notebook_id"),
        "notebookTitle": notebook_title(config),
        "sourceTitlePrefix": config.get("notebooklm", {}).get("source_title_prefix"),
        "lastCheckedAt": state.get("lastCheckedAt"),
        "lastUploadedAt": state.get("lastUploadedAt"),
        "lastBundleSha256": state.get("lastBundleSha256"),
        "fastFingerprint": fast_hash,
        "stateCheckedFastFingerprint": state.get("lastCheckedFastFingerprint"),
        "stateUploadedFastFingerprint": state_uploaded_fingerprint(state),
        "stateFastFingerprint": state.get("lastFastFingerprint"),
        "relevantChangedPaths": changed,
        "sources": state.get("sources", []),
    }
    print_result(data, args.json)


def cmd_pack(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    config, cfg_path = load_config(repo, command="pack")
    state, _ = load_state(cfg_path)
    set_id = args.set_id or now_utc().strftime("%y%m%d%H%M")
    chunks = plan_bundle_chunks(repo, config, set_id=set_id, state=state)
    if args.dry_run:
        print_result(
            {
                "setId": set_id,
                "mode": "chunked",
                "chunkCount": len(chunks),
                "chunks": [
                    {
                        "group": chunk.get("group"),
                        "chunk": chunk.get("chunk"),
                        "title": chunk.get("title"),
                        "estimatedBytes": chunk.get("estimatedBytes"),
                        "fileCount": len(chunk.get("files", [])),
                        **({"files": chunk.get("files", [])} if args.include_files else {}),
                    }
                    for chunk in chunks
                ],
            },
            args.json,
        )
        return
    bundles = build_bundle_set(repo, config, set_id=set_id, state=state)
    print_result(
        {
            "setId": set_id,
            "bundleCount": len(bundles),
            "bundles": [
                {
                    "group": bundle.get("group"),
                    "chunk": bundle.get("chunk"),
                    "title": bundle.get("title"),
                    "path": bundle.get("path"),
                    "fileCount": bundle.get("fileCount"),
                    "bundleSha256": bundle.get("bundleSha256"),
                    "contentSha256": bundle.get("contentSha256"),
                }
                for bundle in bundles
            ],
        },
        args.json,
    )


def cmd_ensure(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    print_result(ensure_index(repo, force=args.force, yes=args.yes, json_output=args.json, command="ensure"), args.json)


def cmd_refresh(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    print_result(ensure_index(repo, force=True, yes=True, json_output=args.json, command="refresh"), args.json)


def cmd_ask(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    freshness = ensure_index(
        repo,
        force=args.force_refresh,
        yes=args.yes,
        json_output=args.json,
        command="ask",
        return_uninitialized=True,
    )
    blocked = provider_block_message(freshness)
    if blocked:
        next_steps = None
        if freshness.get("status") == "needs-first-upload-approval":
            next_steps = first_upload_next(repo, "ask", args.question)
        print_ask_result(freshness, provider_block_payload(freshness, next_steps=next_steps), args)
        return
    answer = ask_provider(repo, args.question)
    print_ask_result(freshness, answer, args)


def cmd_locate(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    freshness = ensure_index(
        repo,
        force=args.force_refresh,
        yes=args.yes,
        json_output=args.json,
        command="locate",
        return_uninitialized=True,
    )
    blocked = provider_block_message(freshness)
    if blocked:
        next_steps = freshness.get("next")
        if not next_steps and freshness.get("status") == "needs-first-upload-approval":
            next_steps = first_upload_next(repo, "locate", args.query)
        result = {
            "freshness": freshness,
            "notebooklm_candidates": {"paths": [], "existing_paths": [], "terms": []},
            "local_line_refs": [],
            "provider_misses_or_stale_paths": [],
            "provider_answer": f"({blocked})",
            "claim_boundary": "Semantic provider was not called because retrieval preflight is blocked.",
        }
        if next_steps:
            result["next"] = next_steps
        print_locate_result(result, args)
        return
    prompt = (
        "Find the code location for this repository question. Return likely repo paths, "
        "function names, test names, command names, and keywords for rg. If exact line "
        f"numbers are unavailable, say so. Question: {args.query}"
    )
    provider = ask_provider(repo, prompt)
    text = answer_text(provider)
    paths, terms = extract_candidates(text, args.query)
    config, _ = load_config(repo, command="locate")
    existing_paths = [path for path in paths if (repo / path).exists()]
    stale_paths = [path for path in paths if not (repo / path).exists()]
    matches = local_rg(repo, config, terms, existing_paths)
    result = {
        "freshness": freshness,
        "notebooklm_candidates": {"paths": paths, "existing_paths": existing_paths, "terms": terms},
        "local_line_refs": matches,
        "provider_misses_or_stale_paths": stale_paths,
        "provider_answer": provider if args.include_provider_answer else "(hidden; pass --include-provider-answer)",
        "claim_boundary": "Line refs come from local rg results, not NotebookLM.",
    }
    print_locate_result(result, args)


def temp_source_sets(state: dict[str, Any]) -> list[dict[str, Any]]:
    sets = state.get("temporarySourceSets")
    if isinstance(sets, list):
        return [item for item in sets if isinstance(item, dict)]
    return []


def temp_source_expires_at(ttl_seconds: int) -> str | None:
    if ttl_seconds <= 0:
        return None
    return iso(now_utc() + dt.timedelta(seconds=ttl_seconds))


def source_is_expired(source_set: dict[str, Any]) -> bool:
    expires_at = source_set.get("expiresAt")
    parsed = parse_iso(str(expires_at)) if expires_at else None
    return bool(parsed and parsed <= now_utc())


def cmd_temp_source_upload(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    config, cfg_path = load_config(repo, command="temp-source upload")
    state, state_path = load_state(cfg_path)
    source_path = Path(args.file).expanduser()
    if not source_path.is_absolute():
        source_path = (repo / source_path).resolve()
    if not source_path.is_file():
        die(f"temp source file not found: {source_path}")
    set_id = now_utc().strftime("%y%m%d%H%M")
    content_sha = sha256_file(source_path)
    title = temp_source_title(config, set_id=set_id, kind=args.kind, title=args.title, content_sha=content_sha)
    staged_path = stage_temp_source_file(repo, title, source_path)
    with repo_lock(repo):
        try:
            state, state_path = load_state(cfg_path)
            source = upload_file_source(repo, config, staged_path, title)
            status = "uploaded"
            if config.get("notebooklm", {}).get("wait_after_upload", True) and source.get("id"):
                status = "ready" if wait_source_ready(repo, notebook_id(config), str(source["id"])) else "error"
                if status != "ready":
                    delete_source_ids_parallel(
                        repo,
                        notebook_id(config),
                        [str(source.get("id") or "")],
                        parallelism=positive_int(config.get("notebooklm", {}).get("delete_parallelism"), 4),
                    )
                    die(f"source processing failed for temp source {title}: {source.get('id')}")
            active = state.get("activeSourceSet") if isinstance(state.get("activeSourceSet"), dict) else {}
            item = {
                "id": source.get("id"),
                "title": source.get("title") or title,
                "contentSha256": content_sha,
                "uploadedAt": iso(),
                "status": status,
                "origin": {
                    "activeSourceSetId": active.get("id"),
                    "chunkKeys": list(args.origin_chunk or []),
                    "filePaths": list(args.origin_file or []),
                },
            }
            source_set = {
                "id": set_id,
                "kind": slugify(args.kind),
                "purpose": args.title,
                "createdAt": iso(),
                "expiresAt": temp_source_expires_at(int(args.ttl_seconds or 0)),
                "sources": [item],
            }
            sets = temp_source_sets(state)
            sets.append(source_set)
            state["temporarySourceSets"] = sets
            write_json(state_path, state)
        finally:
            remove_file_quiet(staged_path)
    print_result({"sourceSet": source_set, "source": item}, args.json)


def cmd_temp_source_list(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    config, cfg_path = load_config(repo, command="temp-source list")
    state, _ = load_state(cfg_path)
    sets = temp_source_sets(state)
    if args.kind:
        wanted = slugify(args.kind)
        sets = [item for item in sets if str(item.get("kind") or "") == wanted]
    prefix = temp_source_prefix(config)
    provider_matches = [src for src in list_sources(repo, notebook_id(config)) if str(src.get("title") or "").startswith(prefix + "--")]
    tracked_ids = {
        str(src.get("id"))
        for source_set in temp_source_sets(state)
        for src in source_set.get("sources", [])
        if isinstance(src, dict) and src.get("id")
    }
    untracked = [src for src in provider_matches if str(src.get("id") or "") not in tracked_ids]
    print_result({"temporarySourceSets": sets, "untrackedPrefixMatches": untracked}, args.json)


def cmd_temp_source_cleanup(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    config, cfg_path = load_config(repo, command="temp-source cleanup")
    with repo_lock(repo):
        state, state_path = load_state(cfg_path)
        sets = temp_source_sets(state)
        wanted_kind = slugify(args.kind) if args.kind else ""
        selected: list[dict[str, Any]] = []
        kept: list[dict[str, Any]] = []
        for source_set in sets:
            matches = True
            if args.set_id and str(source_set.get("id") or "") != str(args.set_id):
                matches = False
            if wanted_kind and str(source_set.get("kind") or "") != wanted_kind:
                matches = False
            if args.expired and not source_is_expired(source_set):
                matches = False
            if matches:
                selected.append(source_set)
            else:
                kept.append(source_set)
        if not args.yes:
            die("cleanup requires --yes")
        source_ids = [
            str(src.get("id"))
            for source_set in selected
            for src in source_set.get("sources", [])
            if isinstance(src, dict) and src.get("id")
        ]
        deleted = delete_source_ids_parallel(
            repo,
            notebook_id(config),
            source_ids,
            parallelism=positive_int(config.get("notebooklm", {}).get("delete_parallelism"), 4),
        )
        deleted_set = set(deleted)
        remaining_selected: list[dict[str, Any]] = []
        for source_set in selected:
            sources = [
                src
                for src in source_set.get("sources", [])
                if isinstance(src, dict) and str(src.get("id") or "") not in deleted_set
            ]
            if sources:
                item = dict(source_set)
                item["sources"] = sources
                remaining_selected.append(item)
        state["temporarySourceSets"] = kept + remaining_selected
        write_json(state_path, state)
        prefix = temp_source_prefix(config)
        provider_matches = [src for src in list_sources(repo, notebook_id(config)) if str(src.get("title") or "").startswith(prefix + "--")]
        tracked_ids = {
            str(src.get("id"))
            for source_set in temp_source_sets(state)
            for src in source_set.get("sources", [])
            if isinstance(src, dict) and src.get("id")
        }
        deleted_set = set(deleted)
        untracked = [
            src
            for src in provider_matches
            if str(src.get("id") or "") not in tracked_ids and str(src.get("id") or "") not in deleted_set
        ]
        if args.include_untracked_prefix:
            extra_ids = [str(src.get("id")) for src in untracked if src.get("id")]
            extra_deleted = delete_source_ids_parallel(
                repo,
                notebook_id(config),
                extra_ids,
                parallelism=positive_int(config.get("notebooklm", {}).get("delete_parallelism"), 4),
            )
            deleted.extend(extra_deleted)
            untracked = [src for src in untracked if str(src.get("id") or "") not in set(extra_deleted)]
    print_result({"deletedSourceIds": deleted, "untrackedPrefixMatches": untracked}, args.json)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memdex",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """
            Agent-facing semantic retrieval for projects and source sets.

            Memdex uses NotebookLM as a semantic locator, then treats local files,
            command output, and project docs as authority for exact evidence.
            Start with init once. For normal agent work, call ask or locate directly;
            they run freshness preflight before querying the provider.
            """
        ).strip(),
        epilog=textwrap.dedent(
            """
            Common agent paths:
              memdex init --repo . --create-notebook
              memdex ask --repo . "Where is retry/backfill documented?"
              memdex locate --repo . "invoice export retry command"
              memdex ask --repo . --yes "question"   # approve first broad upload

            Command routing:
              ask      answer architecture/docs/status questions over the source set
              locate   find likely files or symbols and return local line refs
              init     create .memdex/config.json and bind a NotebookLM notebook
              status   inspect local config, freshness, and recorded source state
              ensure   prewarm or refresh the index when policy allows
              refresh  force a source replacement
              pack     preview deterministic repomix chunks without provider Q&A
            """
        ).strip(),
    )
    sub = parser.add_subparsers(title="commands", dest="command", metavar="<command>", required=True)

    ask = sub.add_parser(
        "ask",
        help="answer semantic project questions with freshness preflight",
        description=textwrap.dedent(
            """
            Ask a question over the configured source set.

            Use this for architecture, docs, behavior, ownership, or status questions.
            Memdex checks freshness first, queries NotebookLM, then prints compact
            provider references. Verify exact claims from local evidence.
            """
        ).strip(),
    )
    ask.add_argument("question", help="natural-language question to ask over the source set")
    ask.add_argument("--repo", default=".", help="project root (default: current directory)")
    ask.add_argument("--yes", action="store_true", help="approve first broad upload if setup is otherwise ready")
    ask.add_argument("--force-refresh", action="store_true", help="refresh managed sources before asking")
    ask.add_argument("--json", action="store_true", help="print machine-readable JSON")
    ask.add_argument("--verbose", action="store_true", help="include freshness and provider metadata")
    ask.set_defaults(func=cmd_ask)

    locate = sub.add_parser(
        "locate",
        help="find likely files or symbols and verify local line refs",
        description=textwrap.dedent(
            """
            Locate implementation, docs, tests, or symbols and verify local line refs.

            Use this when the user asks "where is X?" or needs candidate paths.
            Memdex queries the semantic provider for candidates, then checks local
            files with exact line references when possible.
            """
        ).strip(),
    )
    locate.add_argument("query", help="natural-language thing to find")
    locate.add_argument("--repo", default=".", help="project root (default: current directory)")
    locate.add_argument("--yes", action="store_true", help="approve first broad upload if setup is otherwise ready")
    locate.add_argument("--force-refresh", action="store_true", help="refresh managed sources before locating")
    locate.add_argument("--include-provider-answer", action="store_true", help="include the raw provider answer in output")
    locate.add_argument("--json", action="store_true", help="print machine-readable JSON")
    locate.add_argument("--verbose", action="store_true", help="include freshness metadata")
    locate.set_defaults(func=cmd_locate)

    init = sub.add_parser(
        "init",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="create .memdex/config.json and bind a NotebookLM notebook",
        description="Create project-local Memdex config and bind it to a NotebookLM notebook.",
        epilog=textwrap.dedent(
            """
            Examples:
              memdex init --repo . --create-notebook
              memdex init --repo . --reuse-existing-notebook
              memdex init --repo . --notebook-id <id>
            """
        ).strip(),
    )
    init.add_argument("--repo", default=".", help="project, repo, vault, or source-set root (default: current directory)")
    init.add_argument("--notebook-id", default="", help="bind an existing NotebookLM notebook by ID")
    init.add_argument("--project-name", default="", help="stable project key for notebook and source titles (default: repo basename)")
    init.add_argument("--notebook-title-prefix", default=DEFAULT_NOTEBOOK_TITLE_PREFIX, help="NotebookLM title prefix (default: memdex)")
    init.add_argument("--notebook-title", default="", help="exact NotebookLM title to create or reuse")
    init.add_argument("--reuse-existing-notebook", action="store_true", help="reuse an exact title match; do not create cloud state")
    init.add_argument("--create-notebook", action="store_true", help="create the NotebookLM notebook when no exact title match exists")
    init.add_argument("--source-title-prefix", default="", help="prefix for managed NotebookLM source titles (default: memdex)")
    init.add_argument("--include", default="", help="comma-separated include roots or files for the source set")
    init.add_argument("--force", action="store_true", help="overwrite existing .memdex/config.json")
    init.set_defaults(func=cmd_init)

    status = sub.add_parser(
        "status",
        help="inspect config, freshness, and recorded source state",
        description="Inspect local Memdex config, freshness fingerprints, and NotebookLM source state.",
    )
    status.add_argument("--repo", default=".", help="project root (default: current directory)")
    status.add_argument("--json", action="store_true", help="print machine-readable JSON")
    status.set_defaults(func=cmd_status)

    pack = sub.add_parser(
        "pack",
        help="preview deterministic repomix chunks",
        description="Preview or build deterministic whole-file chunks for the configured source set.",
    )
    pack.add_argument("--repo", default=".", help="project root (default: current directory)")
    pack.add_argument("--set-id", default="", help="stable source-set ID for rendered chunk titles")
    pack.add_argument("--dry-run", action="store_true", help="show planned chunks without running repomix")
    pack.add_argument("--include-files", action="store_true", help="include per-chunk file lists in output")
    pack.add_argument("--json", action="store_true", help="print machine-readable JSON")
    pack.set_defaults(func=cmd_pack)

    ensure = sub.add_parser(
        "ensure",
        help="prewarm or refresh the index when policy allows",
        description="Run freshness preflight and upload/refresh sources only when policy allows.",
    )
    ensure.add_argument("--repo", default=".", help="project root (default: current directory)")
    ensure.add_argument("--force", action="store_true", help="bypass freshness TTL and rebuild source state")
    ensure.add_argument("--yes", action="store_true", help="approve the first broad upload for this run")
    ensure.add_argument("--json", action="store_true", help="print machine-readable JSON")
    ensure.set_defaults(func=cmd_ensure)

    refresh = sub.add_parser(
        "refresh",
        help="force source replacement",
        description="Refresh managed NotebookLM sources, replacing old recorded sources after success.",
    )
    refresh.add_argument("--repo", default=".", help="project root (default: current directory)")
    refresh.add_argument("--force", action="store_true", help="force refresh even when freshness checks would skip it")
    refresh.add_argument("--json", action="store_true", help="print machine-readable JSON")
    refresh.set_defaults(func=cmd_refresh)

    temp = sub.add_parser(
        "temp-source",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="manage temporary derived NotebookLM sources",
        description="Upload, list, or clean temporary derived sources such as notes or study aids.",
    )
    temp_sub = temp.add_subparsers(title="temp-source commands", dest="temp_command", metavar="<command>", required=True)

    temp_upload = temp_sub.add_parser("upload", help="upload a temporary source file")
    temp_upload.add_argument("--repo", default=".", help="project root (default: current directory)")
    temp_upload.add_argument("--kind", required=True, help="temporary source kind, for example notes or flashcard")
    temp_upload.add_argument("--title", required=True, help="human-readable title slug for this temporary source")
    temp_upload.add_argument("--file", required=True, help="local markdown/text file to upload")
    temp_upload.add_argument("--origin-chunk", action="append", default=[], help="origin active chunk key; repeatable")
    temp_upload.add_argument("--origin-file", action="append", default=[], help="origin local file path; repeatable")
    temp_upload.add_argument("--ttl-seconds", type=int, default=0, help="optional expiry TTL in seconds")
    temp_upload.add_argument("--json", action="store_true", help="print machine-readable JSON")
    temp_upload.set_defaults(func=cmd_temp_source_upload)

    temp_list = temp_sub.add_parser("list", help="list recorded temporary sources")
    temp_list.add_argument("--repo", default=".", help="project root (default: current directory)")
    temp_list.add_argument("--kind", default="", help="filter by temporary source kind")
    temp_list.add_argument("--json", action="store_true", help="print machine-readable JSON")
    temp_list.set_defaults(func=cmd_temp_source_list)

    temp_cleanup = temp_sub.add_parser("cleanup", help="delete recorded temporary sources")
    temp_cleanup.add_argument("--repo", default=".", help="project root (default: current directory)")
    temp_cleanup.add_argument("--kind", default="", help="filter by temporary source kind")
    temp_cleanup.add_argument("--set-id", default="", help="filter by temporary source-set ID")
    temp_cleanup.add_argument("--expired", action="store_true", help="clean only expired temporary sources")
    temp_cleanup.add_argument("--include-untracked-prefix", action="store_true", help="also delete untracked prefix matches; requires --yes")
    temp_cleanup.add_argument("--yes", action="store_true", help="confirm deletion")
    temp_cleanup.add_argument("--json", action="store_true", help="print machine-readable JSON")
    temp_cleanup.set_defaults(func=cmd_temp_source_cleanup)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
