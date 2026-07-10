#!/usr/bin/env bash
set -euo pipefail

TARGET_BRANCH="${CI_TARGET_BRANCH:-master}"
SOURCE_BRANCH="${CI_SOURCE_BRANCH:-HEAD}"
SEVERITY="${SKILL_SCANNER_SEVERITY:-critical}"

cleanup() {
    git reset --hard HEAD 2>/dev/null || true
    git checkout - 2>/dev/null || true
}
trap cleanup EXIT

echo "=== CI Skill Scanner (incremental via merge --squash) ==="
echo "Target: ${TARGET_BRANCH}"
echo "Source: ${SOURCE_BRANCH}"
echo ""

echo "[1/4] Fetching branches..."
git fetch origin "${TARGET_BRANCH}" 2>/dev/null || true
git fetch origin "${SOURCE_BRANCH}" 2>/dev/null || true

echo "[2/4] Checking out target branch: ${TARGET_BRANCH}"
if git show-ref --verify --quiet "refs/remotes/origin/${TARGET_BRANCH}"; then
    git checkout "origin/${TARGET_BRANCH}" -b "${TARGET_BRANCH}" 2>/dev/null || git checkout "${TARGET_BRANCH}" 2>&1
else
    git checkout "${TARGET_BRANCH}" 2>&1
fi

echo "[3/4] Merging source branch (squash, no commit): ${SOURCE_BRANCH}"
MERGE_REF="${SOURCE_BRANCH}"
if [ "${SOURCE_BRANCH}" != "HEAD" ] && git show-ref --verify --quiet "refs/remotes/origin/${SOURCE_BRANCH}"; then
    MERGE_REF="origin/${SOURCE_BRANCH}"
fi
if ! git merge --squash "${MERGE_REF}" 2>&1; then
    echo "❌ Merge failed (possible conflicts). Run full scan instead."
    exit 1
fi

STAGED_COUNT=$(git diff --cached --name-only --diff-filter=ACMR | wc -l)
echo "   Staged files: ${STAGED_COUNT}"

if [ "${STAGED_COUNT}" -eq 0 ]; then
    echo "✅ No skill-related changes detected."
    exit 0
fi

echo ""
echo "[4/4] Running pre-commit (incremental)..."
pre-commit run --hook-stage pre-commit 2>&1
EXIT_CODE=$?

echo ""
if [ ${EXIT_CODE} -eq 0 ]; then
    echo "✅ Skill scan passed."
else
    echo "❌ Skill scan failed."
fi

exit ${EXIT_CODE}
