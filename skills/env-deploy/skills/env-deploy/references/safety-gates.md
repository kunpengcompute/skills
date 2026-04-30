# Safety Gates

Pause and ask for explicit confirmation before:

- Installing or upgrading system packages.
- Replacing drivers, CUDA, cuDNN, Java, Go, Python, Docker, or other host-level runtimes.
- Running destructive package-manager cleanup.
- Entering credentials, tokens, private registry settings, or SSH keys.
- Choosing between ambiguous project types or Java build tools.
- Continuing after tests fail for reasons that do not look environment-only.

Prefer:

- Dry-run plans before execution.
- Project-local isolation over host-wide changes.
- Append-only notes and logs over hidden state.
- Redacted output whenever credentials may appear.
