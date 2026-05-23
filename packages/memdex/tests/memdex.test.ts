import { afterEach, describe, expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { buildProgram } from "../src/cli";
import { buildBundleSet, planBundleChunks, repomixBaseArgv } from "../src/chunking";
import { cmdAsk } from "../src/commands";
import { defaultConfig, MemdexError, resetTestHooks, setTestHooks, writeJson, type RunResult } from "../src/common";
import { askProvider } from "../src/retrieval";
import { uploadBundleSet } from "../src/notebooklm";

function tempRepo(): string {
  return mkdtempSync(join(tmpdir(), "memdex-test-"));
}

function ok(argv: string[], stdout = "", stderr = ""): RunResult {
  return { argv, returncode: 0, stdout, stderr };
}

afterEach(() => {
  resetTestHooks();
});

describe("memdex ts cli", () => {
  test("commander help routes agents to primary commands", () => {
    const help = buildProgram().helpInformation();

    expect(help).toContain("Agent-facing semantic retrieval");
    expect(help).toContain("NotebookLM as a semantic locator");
    expect(help).toContain("ask");
    expect(help).toContain("locate");
    expect(help).toContain("init");
    const program = buildProgram();
    expect(program.commands.find((command) => command.name() === "ask")?.helpInformation()).toContain("--repo-worktree <branch>");
    expect(program.commands.find((command) => command.name() === "locate")?.helpInformation()).toContain("--repo-worktree <branch>");
  });

  test("ask can resolve a sibling main worktree for retrieval", async () => {
    const root = tempRepo();
    const mainRepo = join(root, "main");
    const featureRepo = join(root, "feature");
    const oldCwd = process.cwd();
    try {
      mkdirSync(join(mainRepo, ".memdex"), { recursive: true });
      mkdirSync(featureRepo, { recursive: true });
      const config = defaultConfig(mainRepo, "nb-1");
      writeJson(join(mainRepo, ".memdex/config.json"), config);
      writeJson(join(mainRepo, ".memdex/state.local.json"), {
        lastUploadedAt: "2099-01-01T00:00:00Z",
        activeSourceSet: { sources: [{ id: "src-ready", status: "ready" }] },
      });
      process.chdir(featureRepo);
      let notebooklmCwd = "";
      setTestHooks({
        notebooklmCmd: () => ["/bin/notebooklm"],
        run: async (argv, cwd) => {
          if (argv.join(" ") === "git rev-parse --show-toplevel") return ok(argv, featureRepo);
          if (argv.join(" ") === "git worktree list --porcelain") {
            return ok(argv, `worktree ${mainRepo}\nHEAD 1111111\nbranch refs/heads/main\n\nworktree ${featureRepo}\nHEAD 2222222\nbranch refs/heads/feature\n`);
          }
          if (argv.join(" ") === "git rev-parse HEAD") return ok(argv, "1111111");
          if (argv.join(" ") === "git status --porcelain=v1 -z --untracked-files=all") return ok(argv, "");
          if (argv.join(" ") === "git ls-files -co --exclude-standard") return ok(argv, "README.md\n");
          if (argv.slice(0, 3).join(" ") === "/bin/notebooklm ask question") {
            notebooklmCwd = cwd;
            return ok(argv, '{"answer":"ok"}');
          }
          return ok(argv, "");
        },
      });

      await cmdAsk("question", { repoWorktree: "main" });

      expect(notebooklmCwd).toBe(mainRepo);
    } finally {
      process.chdir(oldCwd);
      rmSync(root, { recursive: true, force: true });
    }
  });

  test("repo-worktree reuses recorded sources without refreshing by default", async () => {
    const root = tempRepo();
    const mainRepo = join(root, "main");
    const featureRepo = join(root, "feature");
    const oldCwd = process.cwd();
    try {
      mkdirSync(join(mainRepo, ".memdex"), { recursive: true });
      mkdirSync(featureRepo, { recursive: true });
      writeFileSync(join(mainRepo, "README.md"), "fermi\n");
      const config = defaultConfig(mainRepo, "nb-1");
      writeJson(join(mainRepo, ".memdex/config.json"), config);
      writeJson(join(mainRepo, ".memdex/state.local.json"), {
        lastUploadedAt: "2000-01-01T00:00:00Z",
        activeSourceSet: { sources: [{ id: "src-ready", status: "ready" }] },
      });
      process.chdir(featureRepo);
      let providerCalled = false;
      setTestHooks({
        notebooklmCmd: () => ["/bin/notebooklm"],
        repomixCmd: () => ["/bin/repomix"],
        run: async (argv) => {
          if (argv.join(" ") === "git rev-parse --show-toplevel") return ok(argv, featureRepo);
          if (argv.join(" ") === "git worktree list --porcelain") {
            return ok(argv, `worktree ${mainRepo}\nHEAD 1111111\nbranch refs/heads/main\n\nworktree ${featureRepo}\nHEAD 2222222\nbranch refs/heads/feature\n`);
          }
          if (argv.join(" ") === "git rev-parse HEAD") return ok(argv, "1111111");
          if (argv.join(" ") === "git status --porcelain=v1 -z --untracked-files=all") return ok(argv, "");
          if (argv.join(" ") === "git ls-files -co --exclude-standard") return ok(argv, "README.md\n");
          if (argv.slice(0, 3).join(" ") === "/bin/notebooklm ask question") {
            providerCalled = true;
            return ok(argv, '{"answer":"ok"}');
          }
          if (argv[0] === "/bin/repomix" || argv.slice(0, 4).join(" ") === "/bin/notebooklm source add -") {
            throw new Error(`unexpected refresh for repo-worktree: ${argv.join(" ")}`);
          }
          return ok(argv, "");
        },
      });

      await cmdAsk("question", { repoWorktree: "main" });

      expect(providerCalled).toBe(true);
    } finally {
      process.chdir(oldCwd);
      rmSync(root, { recursive: true, force: true });
    }
  });

  test("repo-worktree yes can approve first upload for the indexed worktree", async () => {
    const root = tempRepo();
    const mainRepo = join(root, "main");
    const featureRepo = join(root, "feature");
    const oldCwd = process.cwd();
    try {
      mkdirSync(join(mainRepo, ".memdex"), { recursive: true });
      mkdirSync(featureRepo, { recursive: true });
      writeFileSync(join(mainRepo, "README.md"), "fermi\n");
      const config = defaultConfig(mainRepo, "nb-1");
      writeJson(join(mainRepo, ".memdex/config.json"), config);
      writeJson(join(mainRepo, ".memdex/state.local.json"), { sources: [] });
      process.chdir(featureRepo);
      let uploaded = false;
      let providerCall: string[] = [];
      setTestHooks({
        notebooklmCmd: () => ["/bin/notebooklm"],
        repomixCmd: () => ["/bin/repomix"],
        run: async (argv) => {
          if (argv.join(" ") === "git rev-parse --show-toplevel") return ok(argv, featureRepo);
          if (argv.join(" ") === "git worktree list --porcelain") {
            return ok(argv, `worktree ${mainRepo}\nHEAD 1111111\nbranch refs/heads/main\n\nworktree ${featureRepo}\nHEAD 2222222\nbranch refs/heads/feature\n`);
          }
          if (argv.join(" ") === "git rev-parse HEAD") return ok(argv, "1111111");
          if (argv.join(" ") === "git status --porcelain=v1 -z --untracked-files=all") return ok(argv, "");
          if (argv.join(" ") === "git ls-files -co --exclude-standard") return ok(argv, "README.md\n");
          if (argv[0] === "/bin/repomix") {
            writeFileSync(argv[argv.indexOf("--output") + 1], "rendered\n");
            return ok(argv);
          }
          if (argv.slice(0, 4).join(" ") === "/bin/notebooklm source add -") {
            uploaded = true;
            return ok(argv, '{"id":"new-source","title":"new.md"}');
          }
          if (argv.slice(0, 3).join(" ") === "/bin/notebooklm source wait") return ok(argv);
          if (argv.slice(0, 3).join(" ") === "/bin/notebooklm ask question") {
            providerCall = argv;
            return ok(argv, '{"answer":"ok"}');
          }
          return ok(argv, "");
        },
      });

      await cmdAsk("question", { repoWorktree: "main", yes: true });

      expect(uploaded).toBe(true);
      expect(providerCall).toContain("new-source");
    } finally {
      process.chdir(oldCwd);
      rmSync(root, { recursive: true, force: true });
    }
  });

  test("repo-worktree outside git reports an agent-actionable next step", async () => {
    const repo = tempRepo();
    const oldCwd = process.cwd();
    try {
      process.chdir(repo);
      setTestHooks({
        run: async (argv) => {
          if (argv.join(" ") === "git rev-parse --show-toplevel") return { argv, returncode: 128, stdout: "", stderr: "fatal: not a git repository" };
          return ok(argv, "");
        },
      });

      const error = await cmdAsk("question", { repoWorktree: "main" }).catch((caught) => caught);

      expect(error).toBeInstanceOf(MemdexError);
      expect(error.message).toContain("--repo-worktree requires cwd inside a Git worktree");
      expect(error.message).toContain("memdex ask --repo /path/to/main");
    } finally {
      process.chdir(oldCwd);
      rmSync(repo, { recursive: true, force: true });
    }
  });

  test("repo-worktree cannot be combined with repo", async () => {
    const error = await cmdAsk("question", { repo: "/tmp/main", repoWorktree: "main" }).catch((caught) => caught);

    expect(error).toBeInstanceOf(MemdexError);
    expect(error.message).toContain("choose either --repo or --repo-worktree");
  });

  test("default chunk target is 512 KiB and missing target falls back to it", async () => {
    const repo = tempRepo();
    try {
      mkdirSync(join(repo, "docs"));
      writeFileSync(join(repo, "docs/a.md"), "a".repeat(300_000));
      writeFileSync(join(repo, "docs/b.md"), "b".repeat(300_000));
      const config = defaultConfig(repo);
      delete config.bundle.target_chunk_bytes;
      setTestHooks({ run: async () => ({ argv: [], returncode: 1, stdout: "", stderr: "" }) });

      const chunks = await planBundleChunks(repo, config, { setId: "2605200912" });

      expect(defaultConfig(repo).bundle.target_chunk_bytes).toBe(524288);
      expect(chunks.map((chunk) => chunk.files)).toEqual([["docs/a.md"], ["docs/b.md"]]);
    } finally {
      rmSync(repo, { recursive: true, force: true });
    }
  });

  test("chunk planner keeps whole files under named groups and ignores denied files", async () => {
    const repo = tempRepo();
    try {
      mkdirSync(join(repo, "docs"));
      mkdirSync(join(repo, "packages/billing/src"), { recursive: true });
      writeFileSync(join(repo, "docs/a.md"), "a".repeat(300));
      writeFileSync(join(repo, "docs/b.md"), "b".repeat(300));
      writeFileSync(join(repo, "docs/secret.env"), "SECRET=1");
      writeFileSync(join(repo, "packages/billing/src/retry.ts"), "export const x = 1\n");
      const config = defaultConfig(repo);
      config.bundle.target_chunk_bytes = 420;
      config.bundle.max_chunk_bytes = 520;
      config.bundle.groups = [
        { id: "docs", include: ["docs/**"] },
        { id: "billing", include: ["packages/billing/**"] },
      ];
      config.safety.never_upload.push("**/*.env");
      setTestHooks({ run: async () => ({ argv: [], returncode: 1, stdout: "", stderr: "" }) });

      const chunks = await planBundleChunks(repo, config, { setId: "2605200912" });

      const files = chunks.flatMap((chunk) => chunk.files).sort();
      expect(files).toEqual(["docs/a.md", "docs/b.md", "packages/billing/src/retry.ts"]);
      expect(files).not.toContain("docs/secret.env");
      expect(chunks.every((chunk) => chunk.estimatedBytes <= 520)).toBe(true);
      expect(chunks.map((chunk) => chunk.title).join("\n")).toMatch(/^memdex--2605200912--/m);
    } finally {
      rmSync(repo, { recursive: true, force: true });
    }
  });

  test("buildBundleSet renders each chunk with repomix stdin", async () => {
    const repo = tempRepo();
    try {
      mkdirSync(join(repo, "docs"));
      writeFileSync(join(repo, "docs/a.md"), "a".repeat(200));
      writeFileSync(join(repo, "docs/b.md"), "b".repeat(200));
      const config = defaultConfig(repo);
      config.bundle.target_chunk_bytes = 260;
      config.bundle.max_chunk_bytes = 420;
      config.bundle.groups = [{ id: "docs", include: ["docs/**"] }];
      const calls: Array<{ argv: string[]; input?: string }> = [];
      setTestHooks({
        run: async (argv, _cwd, opts) => {
          if (argv[0] === "git") return { argv, returncode: 1, stdout: "", stderr: "" };
          calls.push({ argv, input: opts?.inputText });
          writeFileSync(argv[argv.indexOf("--output") + 1], "rendered\n");
          return ok(argv);
        },
        repomixCmd: () => ["repomix"],
      });

      const bundles = await buildBundleSet(repo, config, { setId: "2605200912" });

      expect(bundles).toHaveLength(2);
      expect(calls).toHaveLength(2);
      for (const call of calls) {
        expect(call.argv).toContain("--stdin");
        expect(call.argv).toContain("--output");
        expect(call.input?.split("\n").filter(Boolean)).toHaveLength(1);
      }
    } finally {
      rmSync(repo, { recursive: true, force: true });
    }
  });

  test("uploadBundleSet uploads changed chunks as stdin text and reuses unchanged chunks", async () => {
    const repo = tempRepo();
    try {
      const config = defaultConfig(repo, "nb-1");
      config.notebooklm.wait_after_upload = true;
      const changedPath = join(repo, "new-2.md");
      writeFileSync(join(repo, "new-1.md"), "old");
      writeFileSync(changedPath, "new");
      const state = {
        activeSourceSet: {
          sources: [
            { id: "old-1", title: "old-1.md", group: "docs", chunk: "001", contentSha256: "same", status: "ready" },
            { id: "old-2", title: "old-2.md", group: "docs", chunk: "002", contentSha256: "retired", status: "ready" },
          ],
        },
      };
      const bundles = [
        { group: "docs", chunk: "001", title: "new-1.md", contentSha256: "same", bundleSha256: "same-bundle", sha256: "same-files", fileCount: 1, files: ["docs/a.md"], path: join(repo, "new-1.md") },
        { group: "docs", chunk: "002", title: "new-2.md", contentSha256: "changed", bundleSha256: "changed-bundle", sha256: "changed-files", fileCount: 1, files: ["docs/b.md"], path: changedPath },
      ];
      const uploaded: string[] = [];
      const waited: string[] = [];
      setTestHooks({
        notebooklmCmd: () => ["/bin/notebooklm"],
        run: async (argv, _cwd, opts) => {
          if (argv.slice(0, 4).join(" ") === "/bin/notebooklm source add -") {
            expect(argv).toContain("--type");
            expect(argv[argv.indexOf("--type") + 1]).toBe("text");
            expect(opts?.inputText).toBe("new");
            uploaded.push(argv[argv.indexOf("--title") + 1]);
            return ok(argv, '{"id":"new-2-id","title":"new-2.md"}');
          }
          if (argv.slice(0, 3).join(" ") === "/bin/notebooklm source wait") {
            waited.push(argv[3]);
            return ok(argv);
          }
          return ok(argv, '{"sources":[]}');
        },
      });

      const sourceSet = await uploadBundleSet(repo, config, state, bundles, { setId: "new" });

      expect(uploaded).toEqual(["new-2.md"]);
      expect(waited).toEqual(["new-2-id"]);
      expect(sourceSet._retiredSourceIds).toEqual(["old-2"]);
      expect(sourceSet.sources.map((src: any) => src.id)).toEqual(["old-1", "new-2-id"]);
      expect(sourceSet.sources[0].reused).toBe(true);
    } finally {
      rmSync(repo, { recursive: true, force: true });
    }
  });

  test("askProvider limits NotebookLM query to active ready sources", async () => {
    const repo = tempRepo();
    try {
      mkdirSync(join(repo, ".memdex"));
      const config = defaultConfig(repo, "nb-1");
      writeJson(join(repo, ".memdex/config.json"), config);
      writeJson(join(repo, ".memdex/state.local.json"), {
        activeSourceSet: {
          sources: [
            { id: "src-ready-1", status: "ready" },
            { id: "src-error", status: "error" },
            { id: "src-ready-2", status: "ready" },
          ],
        },
      });
      let call: string[] = [];
      setTestHooks({
        notebooklmCmd: () => ["/bin/notebooklm"],
        run: async (argv) => {
          call = argv;
          return ok(argv, '{"answer":"ok"}');
        },
      });

      const answer = await askProvider(repo, "question");

      expect(answer).toEqual({ answer: "ok" });
      expect(call).toContain("-s");
      expect(call).toContain("src-ready-1");
      expect(call).toContain("src-ready-2");
      expect(call).not.toContain("src-error");
    } finally {
      rmSync(repo, { recursive: true, force: true });
    }
  });

  test("repomix argv prefers installed repomix hook over npx", () => {
    setTestHooks({ repomixCmd: () => ["/bin/repomix"] });
    expect(repomixBaseArgv(defaultConfig("/tmp/repo"))[0]).toBe("/bin/repomix");
  });
});
