import argparse
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "codebase-retrieve.py"
SPEC = importlib.util.spec_from_file_location("codebase_retrieve_cli", SCRIPT_PATH)
assert SPEC is not None
codebase_retrieve = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(codebase_retrieve)


def json_loads(value: str):
    return json.loads(value)


class CodebaseRetrieveCliTest(unittest.TestCase):
    def test_plan_chunked_bundles_keeps_whole_files_under_named_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "docs").mkdir()
            (repo / "packages/billing/src").mkdir(parents=True)
            (repo / "docs/a.md").write_text("a" * 300)
            (repo / "docs/b.md").write_text("b" * 300)
            (repo / "docs/secret.env").write_text("SECRET=1")
            (repo / "packages/billing/src/retry.ts").write_text("export const x = 1\n")

            config = codebase_retrieve.default_config(repo)
            config["notebooklm"]["source_title_prefix"] = "cbr"
            config["bundle"].update(
                {
                    "mode": "chunked",
                    "target_chunk_bytes": 420,
                    "max_chunk_bytes": 520,
                    "groups": [
                        {"id": "docs", "include": ["docs/**"]},
                        {"id": "billing", "include": ["packages/billing/**"]},
                    ],
                }
            )
            config["safety"]["never_upload"].append("**/*.env")

            chunks = codebase_retrieve.plan_bundle_chunks(repo, config, set_id="2605200912")

        chunk_files = [path for chunk in chunks for path in chunk["files"]]
        self.assertEqual(
            sorted(chunk_files),
            ["docs/a.md", "docs/b.md", "packages/billing/src/retry.ts"],
        )
        self.assertEqual(len(chunk_files), len(set(chunk_files)))
        self.assertNotIn("docs/secret.env", chunk_files)
        for chunk in chunks:
            self.assertLessEqual(chunk["estimatedBytes"], 520)
            self.assertRegex(
                chunk["title"],
                r"^cbr--2605200912--(docs|billing)--\d{3}--[0-9a-f]{8}\.md$",
            )

    def test_build_chunked_bundle_set_renders_each_chunk_with_repomix_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "docs").mkdir()
            (repo / "docs/a.md").write_text("a" * 200)
            (repo / "docs/b.md").write_text("b" * 200)
            config = codebase_retrieve.default_config(repo)
            config["notebooklm"]["source_title_prefix"] = "cbr"
            config["bundle"].update(
                {
                    "mode": "chunked",
                    "target_chunk_bytes": 260,
                    "max_chunk_bytes": 420,
                    "groups": [{"id": "docs", "include": ["docs/**"]}],
                }
            )
            calls: list[tuple[list[str], str]] = []

            def fake_run(argv, cwd, *, input_text=None, timeout=None):
                if argv[:2] == ["git", "ls-files"]:
                    return subprocess.CompletedProcess(argv, 1, "", "")
                calls.append((argv, input_text or ""))
                output = Path(argv[argv.index("--output") + 1])
                output.write_text("rendered\n")
                return subprocess.CompletedProcess(argv, 0, "", "")

            with patch.object(codebase_retrieve, "run", side_effect=fake_run):
                bundles = codebase_retrieve.build_bundle_set(repo, config, set_id="2605200912")

        self.assertEqual(len(bundles), 2)
        self.assertEqual(len(calls), 2)
        for argv, input_text in calls:
            self.assertIn("--stdin", argv)
            self.assertIn("--output", argv)
            self.assertEqual(len([line for line in input_text.splitlines() if line]), 1)

    def test_build_chunked_bundle_set_rejects_rendered_chunk_over_max_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "docs").mkdir()
            (repo / "docs/a.md").write_text("a")
            config = codebase_retrieve.default_config(repo)
            config["bundle"].update(
                {
                    "mode": "chunked",
                    "target_chunk_bytes": 120,
                    "max_chunk_bytes": 200,
                    "groups": [{"id": "docs", "include": ["docs/**"]}],
                }
            )

            def fake_run(argv, cwd, *, input_text=None, timeout=None):
                if argv[:2] == ["git", "ls-files"]:
                    return subprocess.CompletedProcess(argv, 1, "", "")
                output = Path(argv[argv.index("--output") + 1])
                output.write_text("x" * 300)
                return subprocess.CompletedProcess(argv, 0, "", "")

            with patch.object(codebase_retrieve, "run", side_effect=fake_run), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    codebase_retrieve.build_bundle_set(repo, config, set_id="2605200912")

    def test_repomix_base_argv_prefers_installed_repomix_over_npx(self) -> None:
        config = codebase_retrieve.default_config(Path("/tmp/repo"))

        def fake_which(name: str) -> str | None:
            if name == "repomix":
                return "/bin/repomix"
            if name == "npx":
                return "/bin/npx"
            return None

        with patch.object(codebase_retrieve.shutil, "which", side_effect=fake_which):
            argv = codebase_retrieve.repomix_base_argv(config)

        self.assertEqual(argv[0], "/bin/repomix")

    def test_sticky_chunk_plan_reuses_previous_file_members_when_new_file_sorts_between_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "docs").mkdir()
            (repo / "docs/a.md").write_text("a" * 100)
            (repo / "docs/b.md").write_text("b" * 100)
            (repo / "docs/c.md").write_text("c" * 100)
            config = codebase_retrieve.default_config(repo)
            config["bundle"].update(
                {
                    "target_chunk_bytes": 390,
                    "max_chunk_bytes": 500,
                    "groups": [{"id": "docs", "include": ["docs/**"]}],
                }
            )
            first = codebase_retrieve.plan_bundle_chunks(repo, config, set_id="2605200912")
            state = {"activeSourceSet": {"sources": [{"group": c["group"], "chunk": c["chunk"], "files": c["files"]} for c in first]}}
            (repo / "docs/aa.md").write_text("aa" * 30)

            second = codebase_retrieve.plan_bundle_chunks(repo, config, set_id="2605200913", state=state)

        self.assertEqual(second[0]["files"], ["docs/a.md", "docs/b.md"])
        self.assertIn("docs/aa.md", [path for chunk in second[1:] for path in chunk["files"]])

    def test_upload_bundle_set_reuses_unchanged_chunk_and_uploads_only_changed_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            cfg_dir = repo / ".codebase-retrieve"
            cfg_dir.mkdir()
            config = codebase_retrieve.default_config(repo, notebook_id="nb-1")
            config["notebooklm"]["wait_after_upload"] = True
            state = {
                "activeSourceSet": {
                    "id": "old",
                    "sources": [
                        {
                            "id": "old-1",
                            "title": "old-1.md",
                            "group": "docs",
                            "chunk": "001",
                            "contentSha256": "same",
                            "status": "ready",
                        },
                        {
                            "id": "old-2",
                            "title": "old-2.md",
                            "group": "docs",
                            "chunk": "002",
                            "contentSha256": "retired",
                            "status": "ready",
                        },
                    ],
                }
            }
            bundles = [
                {
                    "group": "docs",
                    "chunk": "001",
                    "title": "new-1.md",
                    "contentSha256": "same",
                    "bundleSha256": "same-bundle",
                    "sha256": "same-files",
                    "fileCount": 1,
                    "files": ["docs/a.md"],
                    "path": str(repo / "new-1.md"),
                },
                {
                    "group": "docs",
                    "chunk": "002",
                    "title": "new-2.md",
                    "contentSha256": "changed",
                    "bundleSha256": "changed-bundle",
                    "sha256": "changed-files",
                    "fileCount": 1,
                    "files": ["docs/b.md"],
                    "path": str(repo / "new-2.md"),
                },
            ]
            (repo / "new-1.md").write_text("old")
            (repo / "new-2.md").write_text("new")
            uploaded: list[str] = []
            waited: list[str] = []
            deleted: list[str] = []

            def fake_run(argv, cwd, *, input_text=None, timeout=None):
                if argv[:3] == ["/bin/notebooklm", "source", "add"]:
                    title = argv[argv.index("--title") + 1]
                    uploaded.append(title)
                    return subprocess.CompletedProcess(argv, 0, '{"id":"new-2-id","title":"new-2.md"}', "")
                if argv[:3] == ["/bin/notebooklm", "source", "wait"]:
                    waited.append(argv[3])
                    return subprocess.CompletedProcess(argv, 0, "", "")
                if argv[:3] == ["/bin/notebooklm", "source", "delete"]:
                    deleted.append(argv[3])
                    return subprocess.CompletedProcess(argv, 0, "", "")
                return subprocess.CompletedProcess(argv, 0, '{"sources":[]}', "")

            with (
                patch.object(codebase_retrieve, "notebooklm_cmd", return_value=["/bin/notebooklm"]),
                patch.object(codebase_retrieve, "run", side_effect=fake_run),
            ):
                source_set = codebase_retrieve.upload_bundle_set(repo, config, state, bundles, set_id="new")

        self.assertEqual(uploaded, ["new-2.md"])
        self.assertEqual(waited, ["new-2-id"])
        self.assertEqual(deleted, [])
        self.assertEqual(source_set["_retiredSourceIds"], ["old-2"])
        self.assertEqual([src["id"] for src in source_set["sources"]], ["old-1", "new-2-id"])
        self.assertTrue(source_set["sources"][0]["reused"])

    def test_upload_bundle_set_runs_adds_in_parallel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            config = codebase_retrieve.default_config(repo, notebook_id="nb-1")
            config["notebooklm"].update({"wait_after_upload": False, "upload_parallelism": 3})
            state: dict[str, object] = {}
            bundles = []
            for index in range(3):
                path = repo / f"chunk-{index}.md"
                path.write_text(str(index))
                bundles.append(
                    {
                        "group": "docs",
                        "chunk": f"{index + 1:03d}",
                        "title": f"chunk-{index}.md",
                        "contentSha256": f"hash-{index}",
                        "bundleSha256": f"bundle-{index}",
                        "sha256": f"files-{index}",
                        "fileCount": 1,
                        "files": [f"docs/{index}.md"],
                        "path": str(path),
                    }
                )
            active = 0
            max_active = 0
            lock = threading.Lock()

            def fake_run(argv, cwd, *, input_text=None, timeout=None):
                nonlocal active, max_active
                if argv[:3] == ["/bin/notebooklm", "source", "add"]:
                    with lock:
                        active += 1
                        max_active = max(max_active, active)
                    time.sleep(0.05)
                    with lock:
                        active -= 1
                    title = argv[argv.index("--title") + 1]
                    return subprocess.CompletedProcess(argv, 0, f'{{"id":"{title}-id","title":"{title}"}}', "")
                return subprocess.CompletedProcess(argv, 0, '{"sources":[]}', "")

            with (
                patch.object(codebase_retrieve, "notebooklm_cmd", return_value=["/bin/notebooklm"]),
                patch.object(codebase_retrieve, "run", side_effect=fake_run),
            ):
                codebase_retrieve.upload_bundle_set(repo, config, state, bundles, set_id="new")

        self.assertGreaterEqual(max_active, 2)

    def test_pending_upload_journal_is_cleaned_before_new_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            cfg_dir = repo / ".codebase-retrieve"
            cfg_dir.mkdir()
            config = codebase_retrieve.default_config(repo, notebook_id="nb-1")
            codebase_retrieve.write_json(
                cfg_dir / "pending-upload.local.json",
                {
                    "notebookId": "nb-1",
                    "sources": [
                        {"id": "partial-1", "title": "cbr--old--docs--001.md"},
                        {"id": "partial-2", "title": "cbr--old--docs--002.md"},
                    ],
                },
            )
            deleted: list[str] = []

            def fake_run(argv, cwd, *, input_text=None, timeout=None):
                if argv[:3] == ["/bin/notebooklm", "source", "delete"]:
                    deleted.append(argv[3])
                return subprocess.CompletedProcess(argv, 0, "", "")

            with (
                patch.object(codebase_retrieve, "notebooklm_cmd", return_value=["/bin/notebooklm"]),
                patch.object(codebase_retrieve, "run", side_effect=fake_run),
            ):
                codebase_retrieve.recover_pending_upload(repo, config)

        self.assertEqual(sorted(deleted), ["partial-1", "partial-2"])
        self.assertFalse((Path(tmp) / ".codebase-retrieve" / "pending-upload.local.json").exists())

    def test_chunked_upload_records_retired_sources_for_deferred_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            cfg_dir = repo / ".codebase-retrieve"
            cfg_dir.mkdir()
            config = codebase_retrieve.default_config(repo, notebook_id="nb-1")
            codebase_retrieve.write_json(cfg_dir / "config.json", config)
            codebase_retrieve.write_json(
                cfg_dir / "state.local.json",
                {
                    "lastUploadedAt": "2026-01-01T00:00:00Z",
                    "lastUploadedFastFingerprint": "old",
                    "sources": [{"id": "old-1", "status": "ready"}],
                },
            )
            bundle_path = repo / "new.md"
            bundle_path.write_text("new")
            deleted: list[str] = []

            def fake_upload_bundle_set(repo_arg, config_arg, state_arg, bundles_arg, *, set_id):
                return {
                    "id": "new-set",
                    "sources": [{"id": "new-1", "status": "ready"}],
                    "_retiredSourceIds": ["old-1"],
                }

            def fake_delete(repo_arg, nbid, source_ids, *, parallelism):
                deleted.extend(source_ids)
                return list(source_ids)

            with (
                patch.object(codebase_retrieve, "fast_fingerprint", return_value=("new", [])),
                patch.object(
                    codebase_retrieve,
                    "build_bundle_set",
                    return_value=[{"path": str(bundle_path), "contentSha256": "new"}],
                ),
                patch.object(codebase_retrieve, "source_set_hash", return_value="set-sha"),
                patch.object(codebase_retrieve, "upload_bundle_set", side_effect=fake_upload_bundle_set),
                patch.object(codebase_retrieve, "delete_source_ids_parallel", side_effect=fake_delete),
            ):
                result = codebase_retrieve.ensure_index_locked(repo, yes=True, command="ask")

            state = json.loads((cfg_dir / "state.local.json").read_text())

        self.assertEqual(result["status"], "uploaded")
        self.assertEqual(deleted, [])
        self.assertEqual(state["sources"], [{"id": "new-1", "status": "ready"}])
        self.assertEqual(state["cleanupPendingSourceIds"], ["old-1"])
        self.assertEqual(result["cleanupPendingSourceIds"], ["old-1"])

    def test_pending_cleanup_retries_from_state_and_keeps_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            cfg_dir = repo / ".codebase-retrieve"
            cfg_dir.mkdir()
            config = codebase_retrieve.default_config(repo, notebook_id="nb-1")
            codebase_retrieve.write_json(cfg_dir / "config.json", config)
            state_path = cfg_dir / "state.local.json"
            codebase_retrieve.write_json(
                state_path,
                {
                    "activeSourceSet": {"sources": [{"id": "new-1"}]},
                    "cleanupPendingSourceIds": ["old-1", "old-2"],
                },
            )
            attempts: list[str] = []

            def fake_run(argv, cwd, *, input_text=None, timeout=None):
                if argv[:3] == ["/bin/notebooklm", "source", "delete"]:
                    attempts.append(argv[3])
                    return subprocess.CompletedProcess(argv, 1 if argv[3] == "old-2" else 0, "", "")
                return subprocess.CompletedProcess(argv, 0, "", "")

            with (
                patch.object(codebase_retrieve, "notebooklm_cmd", return_value=["/bin/notebooklm"]),
                patch.object(codebase_retrieve, "run", side_effect=fake_run),
                redirect_stderr(io.StringIO()),
            ):
                deleted = codebase_retrieve.recover_pending_cleanup(repo, config, json.loads(state_path.read_text()), state_path)

            state_after_first = json.loads(state_path.read_text())

            def fake_run_success(argv, cwd, *, input_text=None, timeout=None):
                if argv[:3] == ["/bin/notebooklm", "source", "delete"]:
                    attempts.append(argv[3])
                return subprocess.CompletedProcess(argv, 0, "", "")

            with (
                patch.object(codebase_retrieve, "notebooklm_cmd", return_value=["/bin/notebooklm"]),
                patch.object(codebase_retrieve, "run", side_effect=fake_run_success),
            ):
                deleted_retry = codebase_retrieve.recover_pending_cleanup(repo, config, dict(state_after_first), state_path)

            final_state = json.loads(state_path.read_text())

        self.assertEqual(deleted, ["old-1"])
        self.assertEqual(state_after_first["cleanupPendingSourceIds"], ["old-2"])
        self.assertEqual(deleted_retry, ["old-2"])
        self.assertNotIn("cleanupPendingSourceIds", final_state)
        self.assertEqual(attempts, ["old-1", "old-2", "old-2"])

    def test_temp_source_upload_records_owned_source_with_prefix_and_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            cfg_dir = repo / ".codebase-retrieve"
            cfg_dir.mkdir()
            source_file = repo / "flashcard.md"
            source_file.write_text("# Flashcard seed\n")
            config = codebase_retrieve.default_config(repo, notebook_id="nb-1")
            codebase_retrieve.write_json(cfg_dir / "config.json", config)
            codebase_retrieve.write_json(
                cfg_dir / "state.local.json",
                {
                    "activeSourceSet": {
                        "id": "active-1",
                        "sources": [{"id": "chunk-1", "chunkKey": "docs/001", "files": ["docs/a.md"]}],
                    }
                },
            )
            calls: list[list[str]] = []
            args = argparse.Namespace(
                repo=str(repo),
                kind="flashcard",
                title="Retry Design",
                file=str(source_file),
                origin_chunk=["docs/001"],
                origin_file=["docs/a.md"],
                ttl_seconds=3600,
                json=True,
            )

            def fake_run(argv, cwd, *, input_text=None, timeout=None):
                calls.append(argv)
                if argv[:3] == ["/bin/notebooklm", "source", "add"]:
                    self.assertIn(".codebase-retrieve/cache/cbrtmp--", argv[3])
                    title = argv[argv.index("--title") + 1]
                    return subprocess.CompletedProcess(argv, 0, f'{{"id":"tmp-1","title":"{title}"}}', "")
                if argv[:3] == ["/bin/notebooklm", "source", "wait"]:
                    return subprocess.CompletedProcess(argv, 0, "", "")
                return subprocess.CompletedProcess(argv, 0, '{"sources":[]}', "")

            with (
                patch.object(codebase_retrieve, "notebooklm_cmd", return_value=["/bin/notebooklm"]),
                patch.object(codebase_retrieve, "run", side_effect=fake_run),
                redirect_stdout(io.StringIO()) as stdout,
            ):
                codebase_retrieve.cmd_temp_source_upload(args)

            state = codebase_retrieve.load_state(cfg_dir / "config.json")[0]

        payload = json_loads(stdout.getvalue())
        temp_sets = state["temporarySourceSets"]
        self.assertEqual(payload["source"]["id"], "tmp-1")
        self.assertEqual(len(temp_sets), 1)
        source = temp_sets[0]["sources"][0]
        self.assertEqual(source["id"], "tmp-1")
        self.assertTrue(source["title"].startswith("cbrtmp--"))
        self.assertIn("--flashcard--retry-design--", source["title"])
        self.assertEqual(source["origin"]["activeSourceSetId"], "active-1")
        self.assertEqual(source["origin"]["chunkKeys"], ["docs/001"])
        self.assertEqual(source["origin"]["filePaths"], ["docs/a.md"])
        self.assertTrue(any(call[:3] == ["/bin/notebooklm", "source", "wait"] for call in calls))

    def test_temp_source_cleanup_deletes_only_state_recorded_sources_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            cfg_dir = repo / ".codebase-retrieve"
            cfg_dir.mkdir()
            config = codebase_retrieve.default_config(repo, notebook_id="nb-1")
            codebase_retrieve.write_json(cfg_dir / "config.json", config)
            codebase_retrieve.write_json(
                cfg_dir / "state.local.json",
                {
                    "temporarySourceSets": [
                        {
                            "id": "set-1",
                            "kind": "flashcard",
                            "sources": [{"id": "owned-1", "title": "cbrtmp--old--flashcard--owned--11111111.md"}],
                        }
                    ]
                },
            )
            deleted: list[str] = []
            args = argparse.Namespace(
                repo=str(repo),
                kind="",
                set_id="",
                expired=False,
                include_untracked_prefix=False,
                yes=True,
                json=True,
            )

            def fake_run(argv, cwd, *, input_text=None, timeout=None):
                if argv[:3] == ["/bin/notebooklm", "source", "list"]:
                    return subprocess.CompletedProcess(
                        argv,
                        0,
                        '{"sources":[{"id":"owned-1","title":"cbrtmp--old--flashcard--owned--11111111.md"},{"id":"manual-1","title":"cbrtmp--manual--flashcard--manual--22222222.md"}]}',
                        "",
                    )
                if argv[:3] == ["/bin/notebooklm", "source", "delete"]:
                    deleted.append(argv[3])
                    return subprocess.CompletedProcess(argv, 0, "", "")
                return subprocess.CompletedProcess(argv, 0, "", "")

            with (
                patch.object(codebase_retrieve, "notebooklm_cmd", return_value=["/bin/notebooklm"]),
                patch.object(codebase_retrieve, "run", side_effect=fake_run),
                redirect_stdout(io.StringIO()) as stdout,
            ):
                codebase_retrieve.cmd_temp_source_cleanup(args)
            state = codebase_retrieve.load_state(cfg_dir / "config.json")[0]

        payload = json_loads(stdout.getvalue())
        self.assertEqual(deleted, ["owned-1"])
        self.assertEqual(payload["deletedSourceIds"], ["owned-1"])
        self.assertEqual(payload["untrackedPrefixMatches"][0]["id"], "manual-1")
        self.assertEqual(state.get("temporarySourceSets"), [])

    def test_ask_provider_limits_query_to_active_ready_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            cfg_dir = repo / ".codebase-retrieve"
            cfg_dir.mkdir()
            config = codebase_retrieve.default_config(repo, notebook_id="nb-1")
            codebase_retrieve.write_json(cfg_dir / "config.json", config)
            codebase_retrieve.write_json(
                cfg_dir / "state.local.json",
                {
                    "activeSourceSet": {
                        "id": "2605200912",
                        "sources": [
                            {"id": "src-ready-1", "status": "ready"},
                            {"id": "src-error", "status": "error"},
                            {"id": "src-ready-2", "status": "ready"},
                        ],
                    }
                },
            )
            calls: list[list[str]] = []

            def fake_run(argv, cwd, *, input_text=None, timeout=None):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, '{"answer":"ok"}', "")

            with patch.object(codebase_retrieve, "run", side_effect=fake_run):
                answer = codebase_retrieve.ask_provider(repo, "question")

        self.assertEqual(answer, {"answer": "ok"})
        argv = calls[0]
        self.assertIn("-s", argv)
        self.assertIn("src-ready-1", argv)
        self.assertIn("src-ready-2", argv)
        self.assertNotIn("src-error", argv)

    def test_pack_dry_run_prints_chunk_plan_without_building_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            cfg_dir = repo / ".codebase-retrieve"
            cfg_dir.mkdir()
            (repo / "docs").mkdir()
            (repo / "docs/a.md").write_text("a")
            config = codebase_retrieve.default_config(repo, notebook_id="nb-1")
            config["bundle"]["groups"] = [{"id": "docs", "include": ["docs/**"]}]
            codebase_retrieve.write_json(cfg_dir / "config.json", config)
            args = argparse.Namespace(repo=str(repo), set_id="2605200912", dry_run=True, include_files=False, json=True)

            with (
                patch.object(codebase_retrieve, "build_bundle_set") as build_bundle_set,
                redirect_stdout(io.StringIO()) as stdout,
            ):
                codebase_retrieve.cmd_pack(args)

        build_bundle_set.assert_not_called()
        output = stdout.getvalue()
        self.assertIn('"chunkCount": 1', output)
        self.assertIn("cbr--2605200912--docs--001", output)

    def test_ask_returns_init_guidance_without_provider_when_uninitialized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                repo=tmp,
                question="Where is reconnect implemented?",
                force_refresh=False,
                yes=False,
                json=False,
                verbose=False,
            )

            with (
                patch.object(codebase_retrieve, "ask_provider") as ask_provider,
                redirect_stdout(io.StringIO()) as stdout,
            ):
                codebase_retrieve.cmd_ask(args)

        ask_provider.assert_not_called()
        output = stdout.getvalue()
        self.assertIn("project is not initialized", output)
        self.assertIn("createNotebook", output)
        self.assertIn("ask --repo", output)

    def test_locate_returns_init_guidance_without_provider_when_uninitialized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                repo=tmp,
                query="where is reconnect implemented?",
                force_refresh=False,
                yes=False,
                include_provider_answer=False,
                json=False,
                verbose=False,
            )

            with (
                patch.object(codebase_retrieve, "ask_provider") as ask_provider,
                redirect_stdout(io.StringIO()) as stdout,
            ):
                codebase_retrieve.cmd_locate(args)

        ask_provider.assert_not_called()
        output = stdout.getvalue()
        self.assertIn("project is not initialized", output)
        self.assertIn("createNotebook", output)
        self.assertIn("local_line_refs: []", output)

    def test_ask_stops_before_provider_when_first_upload_needs_approval(self) -> None:
        args = argparse.Namespace(
            repo=".",
            question="Where is reconnect implemented?",
            force_refresh=False,
            yes=False,
            json=False,
            verbose=False,
        )

        with (
            patch.object(codebase_retrieve, "ensure_index", return_value={"status": "needs-first-upload-approval"}),
            patch.object(codebase_retrieve, "ask_provider") as ask_provider,
            redirect_stdout(io.StringIO()) as stdout,
        ):
            codebase_retrieve.cmd_ask(args)

        ask_provider.assert_not_called()
        output = stdout.getvalue()
        self.assertIn("first broad upload requires approval", output)
        self.assertIn("--yes", output)
        self.assertIn("askWithFirstUploadApproval", output)
        self.assertIn("skipped; first broad upload requires approval", output)

    def test_locate_stops_before_provider_when_first_upload_needs_approval(self) -> None:
        args = argparse.Namespace(
            repo=".",
            query="where is reconnect implemented?",
            force_refresh=False,
            yes=False,
            include_provider_answer=False,
            json=False,
            verbose=False,
        )

        with (
            patch.object(codebase_retrieve, "ensure_index", return_value={"status": "needs-first-upload-approval"}),
            patch.object(codebase_retrieve, "ask_provider") as ask_provider,
            redirect_stdout(io.StringIO()) as stdout,
        ):
            codebase_retrieve.cmd_locate(args)

        ask_provider.assert_not_called()
        output = stdout.getvalue()
        self.assertIn("first broad upload requires approval", output)
        self.assertIn("local_line_refs: []", output)
        self.assertIn("locateWithFirstUploadApproval", output)
        self.assertNotIn("next: {}", output)
        self.assertIn("skipped; first broad upload requires approval", output)


if __name__ == "__main__":
    unittest.main()
