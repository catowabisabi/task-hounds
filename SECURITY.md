# Security Policy

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability or leaked
credential. Use GitHub's private vulnerability reporting feature for this
repository and include:

- the affected version or commit;
- reproduction steps;
- expected and observed behavior;
- the potential impact;
- any suggested mitigation.

Do not include real API keys, access tokens, private project contents, or user
databases in reports.

## Local data

Task Hounds stores credentials, runtime logs, databases, and OpenCode state
locally. These files are excluded from Git by default. Before publishing a fork
or diagnostic archive, verify that it does not contain:

- `.env` or provider credentials;
- `*.db`, `*.db-wal`, or `*.db-shm`;
- `core/runtime/` contents other than tracked configuration templates;
- generated logs, screenshots, traces, or test workspaces.
