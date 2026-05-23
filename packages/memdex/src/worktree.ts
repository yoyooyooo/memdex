import { resolve } from "node:path";
import { JsonObject, commandLine, die, runCommand } from "./common";

export type WorktreeEntry = {
  path: string;
  branch?: string;
};

export function parseGitWorktreePorcelain(text: string): WorktreeEntry[] {
  const entries: WorktreeEntry[] = [];
  let current: WorktreeEntry | null = null;
  for (const line of text.split("\n")) {
    if (!line.trim()) {
      current = null;
      continue;
    }
    if (line.startsWith("worktree ")) {
      current = { path: line.slice("worktree ".length) };
      entries.push(current);
      continue;
    }
    if (current && line.startsWith("branch ")) current.branch = line.slice("branch ".length);
  }
  return entries;
}

function branchName(value: string): string {
  return value.trim().replace(/^refs\/heads\//, "");
}

function commandWithQuery(repo: string, command: string, query?: string): string {
  return query ? commandLine(repo, command, query) : commandLine(repo, command);
}

function explicitRepoGuidance(command: string, query?: string): string {
  return commandWithQuery("/path/to/main", command, query);
}

export async function resolveRepoOption(opts: JsonObject, command: string, query?: string): Promise<string> {
  const requestedWorktree = String(opts.repoWorktree || "").trim();
  const explicitRepo = opts.repo === undefined ? "" : String(opts.repo || "").trim();
  if (!requestedWorktree) return resolve(explicitRepo || ".");

  if (explicitRepo) {
    die(
      [
        "choose either --repo or --repo-worktree; they both select the repository root.",
        "",
        "Agent next step:",
        `  Use explicit path: ${commandWithQuery(explicitRepo, command, query)}`,
        `  Or resolve a checked-out branch from cwd: memdex ${command} --repo-worktree ${branchName(requestedWorktree)}${query ? ` ${JSON.stringify(query)}` : ""}`,
      ].join("\n"),
    );
  }

  const anchor = process.cwd();
  const root = await runCommand(["git", "rev-parse", "--show-toplevel"], anchor, { timeout: 30 });
  if (root.returncode !== 0 || !root.stdout.trim()) {
    die(
      [
        "--repo-worktree requires cwd inside a Git worktree.",
        `Current cwd: ${anchor}`,
        "",
        "Agent next step:",
        `  Pass the indexed main worktree explicitly: ${explicitRepoGuidance(command, query)}`,
        `  Or cd into any worktree of this Git repository, then rerun: memdex ${command} --repo-worktree ${branchName(requestedWorktree)}${query ? ` ${JSON.stringify(query)}` : ""}`,
      ].join("\n"),
    );
  }

  const list = await runCommand(["git", "worktree", "list", "--porcelain"], root.stdout.trim(), { timeout: 30 });
  if (list.returncode !== 0) die(`git worktree list failed:\n${list.stdout}\n${list.stderr}`);

  const wanted = branchName(requestedWorktree);
  const entry = parseGitWorktreePorcelain(list.stdout).find((item) => branchName(item.branch || "") === wanted);
  if (!entry) {
    die(
      [
        `no Git worktree found for branch ${JSON.stringify(wanted)}.`,
        `Repository root: ${root.stdout.trim()}`,
        "",
        "Agent next step:",
        `  If main is checked out elsewhere, pass it explicitly: ${explicitRepoGuidance(command, query)}`,
        "  Otherwise create or checkout that worktree first, then rerun this command.",
      ].join("\n"),
    );
  }
  return resolve(entry.path);
}
