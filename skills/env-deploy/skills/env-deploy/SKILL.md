---
name: env-deploy
description: Minimal framework-neutral guidance for deploying a local or cloned Linux code project's runtime environment. Use when an agent needs lightweight project detection, dependency planning, safe dry-run-first setup guidance, explicit confirmation gates, and reproducible deployment notes without platform-specific tooling.
---

# env-deploy

Deploy a Linux code project's runtime environment with minimal host impact and reproducible notes. Use this generic version when no platform-specific Skill is available or when the user wants a lightweight, instruction-only workflow.

## Workflow

1. **Confirm the project source**
   - Use the provided local path or cloned repository.
   - If the target is missing or inaccessible, ask for the path or repository URL before planning commands.

2. **Inspect before changing anything**
   - Identify project markers such as `pyproject.toml`, `requirements.txt`, `go.mod`, `pom.xml`, `build.gradle`, `CMakeLists.txt`, `Makefile`, `Dockerfile`, and `docker-compose.yml`.
   - Read `README.md`, `INSTALL.md`, and dependency manifests before proposing install commands.
   - Use `references/project-markers.md` when you need a compact marker checklist.
   - If multiple project types or Java build tools are plausible, ask the user to choose.

3. **Plan in dry-run style first**
   - Present dependency, build, runtime, and test commands before executing them.
   - Prefer project-local isolation: Python virtual environments, language-version managers, local build directories, and non-destructive package installs.
   - Keep system package installation and driver/runtime replacement opt-in.

4. **Apply only after confirmation**
   - Execute commands only when the user asks to apply the plan.
   - Stop and ask before driver changes, destructive package-manager cleanup, credential entry, private package setup, or unresolved test failures.
   - Use `references/safety-gates.md` before any risky or ambiguous change.
   - Do not fix application code unless the user explicitly expands the scope beyond environment deployment.

5. **Leave reproducible output**
   - Record effective commands, skipped steps, failures, warnings, and user decisions.
   - Produce enough notes for another agent or developer to repeat the setup.
   - Use `templates/deploy-notes.md` when the user wants a reusable deployment summary.
   - If the user needs bundled automation scripts, use the platform-specific variants: `env-deploy-for-codex`, `env-deploy-for-cc`, or `env-deploy-for-trae`.

## Safety Rules

- Default to inspection and planning over execution.
- Never hide failed commands or partial setup state.
- Redact credentials and tokens from logs, notes, and final summaries.
- Preserve existing host runtimes and drivers unless the user explicitly approves a risky change.

## Bundled Resources

- `references/project-markers.md`: quick language and tool marker checklist.
- `references/safety-gates.md`: confirmation gates and non-destructive deployment rules.
- `templates/deploy-notes.md`: simple structure for reproducible setup notes.
