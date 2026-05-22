#!/usr/bin/env node
import { Command } from "commander";
import { MemdexError } from "./common";
import {
  cmdAsk,
  cmdEnsure,
  cmdInit,
  cmdLocate,
  cmdPack,
  cmdRefresh,
  cmdStatus,
  cmdTempSourceCleanup,
  cmdTempSourceList,
  cmdTempSourceUpload,
} from "./commands";

export function buildProgram(): Command {
  const program = new Command();
  program
    .name("memdex")
    .description(
      [
        "Agent-facing semantic retrieval for projects and source sets.",
        "",
        "Memdex uses NotebookLM as a semantic locator, then treats local files,",
        "command output, and project docs as authority for exact evidence.",
      ].join("\n"),
    )
    .showHelpAfterError()
    .addHelpText(
      "after",
      `

Common agent paths:
  memdex init --repo . --create-notebook
  memdex ask --repo . "Where is retry/backfill documented?"
  memdex locate --repo . "invoice export retry command"
  memdex ask --repo . --yes "question"

Command routing:
  ask      answer architecture/docs/status questions over the source set
  locate   find likely files or symbols and return local line refs
  init     create .memdex/config.json and bind a NotebookLM notebook
  status   inspect local config, freshness, and recorded source state
  ensure   prewarm or refresh the index when policy allows
  refresh  force a source replacement
  pack     preview deterministic repomix chunks without provider Q&A
`,
    );

  program
    .command("ask")
    .description("answer semantic project questions with freshness preflight")
    .argument("<question>", "natural-language question to ask over the source set")
    .option("--repo <repo>", "project root", ".")
    .option("--yes", "approve first broad upload if setup is otherwise ready")
    .option("--force-refresh", "refresh managed sources before asking")
    .option("--json", "print machine-readable JSON")
    .option("--verbose", "include freshness and provider metadata")
    .action((question, opts) => cmdAsk(question, opts));

  program
    .command("locate")
    .description("find likely files or symbols and verify local line refs")
    .argument("<query>", "natural-language thing to find")
    .option("--repo <repo>", "project root", ".")
    .option("--yes", "approve first broad upload if setup is otherwise ready")
    .option("--force-refresh", "refresh managed sources before locating")
    .option("--include-provider-answer", "include the raw provider answer in output")
    .option("--json", "print machine-readable JSON")
    .option("--verbose", "include freshness metadata")
    .action((query, opts) => cmdLocate(query, opts));

  program
    .command("init")
    .description("create .memdex/config.json and bind a NotebookLM notebook")
    .option("--repo <repo>", "project, repo, vault, or source-set root", ".")
    .option("--notebook-id <id>", "bind an existing NotebookLM notebook by ID", "")
    .option("--project-name <name>", "stable project key for notebook and source titles", "")
    .option("--notebook-title-prefix <prefix>", "NotebookLM title prefix", "memdex")
    .option("--notebook-title <title>", "exact NotebookLM title to create or reuse", "")
    .option("--reuse-existing-notebook", "reuse an exact title match; do not create cloud state")
    .option("--create-notebook", "create the NotebookLM notebook when no exact title match exists")
    .option("--source-title-prefix <prefix>", "prefix for managed NotebookLM source titles", "")
    .option("--include <specs>", "comma-separated include roots or files for the source set", "")
    .option("--force", "overwrite existing .memdex/config.json")
    .action((opts) => cmdInit(opts));

  program
    .command("status")
    .description("inspect config, freshness, and recorded source state")
    .option("--repo <repo>", "project root", ".")
    .option("--json", "print machine-readable JSON")
    .action((opts) => cmdStatus(opts));

  program
    .command("pack")
    .description("preview deterministic repomix chunks")
    .option("--repo <repo>", "project root", ".")
    .option("--set-id <id>", "stable source-set ID for rendered chunk titles", "")
    .option("--dry-run", "show planned chunks without running repomix")
    .option("--include-files", "include per-chunk file lists in output")
    .option("--json", "print machine-readable JSON")
    .action((opts) => cmdPack(opts));

  program
    .command("ensure")
    .description("prewarm or refresh the index when policy allows")
    .option("--repo <repo>", "project root", ".")
    .option("--force", "bypass freshness TTL and rebuild source state")
    .option("--yes", "approve the first broad upload for this run")
    .option("--json", "print machine-readable JSON")
    .action((opts) => cmdEnsure(opts));

  program
    .command("refresh")
    .description("force source replacement")
    .option("--repo <repo>", "project root", ".")
    .option("--force", "force refresh even when freshness checks would skip it")
    .option("--json", "print machine-readable JSON")
    .action((opts) => cmdRefresh(opts));

  const temp = program.command("temp-source").description("manage temporary derived NotebookLM sources");
  temp
    .command("upload")
    .description("upload a temporary source file")
    .option("--repo <repo>", "project root", ".")
    .requiredOption("--kind <kind>", "temporary source kind")
    .requiredOption("--title <title>", "human-readable title slug")
    .requiredOption("--file <file>", "local markdown/text file to upload")
    .option("--origin-chunk <chunk>", "origin active chunk key; repeatable", collect, [])
    .option("--origin-file <file>", "origin local file path; repeatable", collect, [])
    .option("--ttl-seconds <seconds>", "optional expiry TTL in seconds", "0")
    .option("--json", "print machine-readable JSON")
    .action((opts) => cmdTempSourceUpload({ ...opts, ttlSeconds: Number(opts.ttlSeconds || 0) }));

  temp
    .command("list")
    .description("list recorded temporary sources")
    .option("--repo <repo>", "project root", ".")
    .option("--kind <kind>", "filter by temporary source kind", "")
    .option("--json", "print machine-readable JSON")
    .action((opts) => cmdTempSourceList(opts));

  temp
    .command("cleanup")
    .description("delete recorded temporary sources")
    .option("--repo <repo>", "project root", ".")
    .option("--kind <kind>", "filter by temporary source kind", "")
    .option("--set-id <id>", "filter by temporary source-set ID", "")
    .option("--expired", "clean only expired temporary sources")
    .option("--include-untracked-prefix", "also delete untracked prefix matches; requires --yes")
    .option("--yes", "confirm deletion")
    .option("--json", "print machine-readable JSON")
    .action((opts) => cmdTempSourceCleanup(opts));

  return program;
}

function collect(value: string, previous: string[]): string[] {
  previous.push(value);
  return previous;
}

export async function main(argv = process.argv): Promise<void> {
  try {
    await buildProgram().parseAsync(argv);
  } catch (error) {
    if (error instanceof MemdexError) {
      console.error(`error: ${error.message}`);
      process.exitCode = error.code;
      return;
    }
    throw error;
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  await main();
}
