# Security Policy

## Reporting

Please report security issues privately by opening a GitHub security advisory
for this repository, or by contacting the repository owner through GitHub.

Do not open public issues that include credentials, auth cookies, private
NotebookLM source IDs, private repo contents, or production logs.

## Scope

Security-sensitive areas include:

- source selection and `safety.never_upload` behavior;
- first broad-upload approval;
- NotebookLM source deletion and cleanup ownership;
- local state files under `.codebase-retrieve/`;
- command construction for subprocess calls.

## Data Handling

This project does not require credentials in repo files. Auth for NotebookLM is
handled by the external `notebooklm-py` CLI. Users are responsible for deciding
which repository content may be uploaded to NotebookLM.
