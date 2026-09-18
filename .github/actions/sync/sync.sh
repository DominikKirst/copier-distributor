#!/usr/bin/env bash
set -euo pipefail

dest="${REPO##*/}"
pretend=""
if [[ "${DRY_RUN}" == "true" ]]; then
  pretend=" --pretend"
fi

echo "execute_gh=${EXECUTE_GH}"
echo "use_pr_integration=${USE_PR_INTEGRATION}"
echo "dry_run=${DRY_RUN}"
echo "slack is demo only — never sent"
echo

common=$(cat <<EOF
git clone --branch ${BRANCH} ${URL} ${dest}
cd ${dest}
git checkout -B sync/template
copier update --trust --defaults --skip-answered --vcs-ref ${VCS_REF} -d type=${TYPE}${pretend}
git add -A
git commit -m 'chore: template sync' || true
EOF
)

pr_upsert=$(cat <<EOF
title="chore: template sync \$(date -u +%Y-%m-%d)"
commits=\$(git log origin/${BRANCH}..sync/template --format='%h %s')
pr=\$(gh pr list --head sync/template --base ${BRANCH} --state open --json number --jq '.[0].number // empty')
if [ -n "\$pr" ]; then
  body=\$(gh pr view "\$pr" --json body --jq .body)
  gh pr edit "\$pr" --title "\$title" --body "\$body

\$commits"
else
  gh pr create --base ${BRANCH} --head sync/template --title "\$title" --body "\$commits"
fi
EOF
)

if [[ "${USE_PR_INTEGRATION}" == "true" ]]; then
  plan=$(cat <<EOF
${common}
git push -u origin sync/template
${pr_upsert}

# [slack demo — never sent]
# channel: ${SLACK}
# message: Template sync PR opened/updated for ${REPO}: sync/template → ${BRANCH}.

git checkout ${BRANCH}
if ! git merge --no-edit sync/template; then
  git merge --abort

  # [slack demo — never sent]
  # channel: ${SLACK}
  # message: Template sync for ${REPO} could not be merged into ${BRANCH}.
fi
EOF
)
else
  plan=$(cat <<EOF
${common}
git checkout ${BRANCH}
if git merge --no-edit sync/template; then
  git push origin ${BRANCH}
else
  git merge --abort
  git checkout sync/template
  git push -u origin sync/template
${pr_upsert}

  # [slack demo — never sent]
  # channel: ${SLACK}
  # message: Template sync PR opened/updated for ${REPO}: sync/template → ${BRANCH}.
fi
EOF
)
fi

echo "$plan"
echo

if [[ "${EXECUTE_GH}" != "true" ]]; then
  echo "execute_gh=false → not running"
  exit 0
fi

echo "execute_gh=true → running"
python3 -m pip install --user --quiet 'copier>=9'
export PATH="${HOME}/.local/bin:${PATH}"
git config --global user.name "copier-distributor"
git config --global user.email "41898282+github-actions[bot]@users.noreply.github.com"

if [[ -z "${GH_TOKEN:-}" ]]; then
  echo "GH_TOKEN is required to clone/push client repos" >&2
  exit 1
fi

# Client answers use git@github.com; the runner has no SSH key.
git config --global url."https://github.com/".insteadOf "git@github.com:"
git config --global --add url."https://github.com/".insteadOf "ssh://git@github.com/"
basic="$(printf 'x-access-token:%s' "${GH_TOKEN}" | openssl base64 -A)"
git config --global http.https://github.com/.extraheader "AUTHORIZATION: basic ${basic}"

clone_url="https://x-access-token:${GH_TOKEN}@github.com/${REPO}.git"
workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT
git clone --branch "${BRANCH}" "${clone_url}" "${workdir}/${dest}"
cd "${workdir}/${dest}"
git checkout -B sync/template
copier update --trust --defaults --skip-answered --vcs-ref "${VCS_REF}" -d "type=${TYPE}"
git add -A
echo "=== git status ==="
git status --short
echo "=== git diff ==="
git diff --cached
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "dry_run: not committing or pushing"
  exit 0
fi
git commit -m 'chore: template sync' || true

upsert_pr() {
  local title commits pr body
  title="chore: template sync $(date -u +%Y-%m-%d)"
  commits="$(git log "origin/${BRANCH}..sync/template" --format='%h %s' || true)"
  pr="$(gh pr list --head sync/template --base "${BRANCH}" --state open --json number --jq '.[0].number // empty')"
  if [[ -n "${pr}" ]]; then
    body="$(gh pr view "${pr}" --json body --jq .body)"
    gh pr edit "${pr}" --title "${title}" --body "${body}"$'\n\n'"${commits}"
  else
    gh pr create --base "${BRANCH}" --head sync/template --title "${title}" --body "${commits}"
  fi
  echo "[slack demo — never sent] channel=${SLACK} message=Template sync PR opened/updated for ${REPO}: sync/template → ${BRANCH}."
}

if [[ "${USE_PR_INTEGRATION}" == "true" ]]; then
  git push -u origin sync/template
  upsert_pr
  git checkout "${BRANCH}"
  if ! git merge --no-edit sync/template; then
    git merge --abort
    echo "[slack demo — never sent] channel=${SLACK} message=Template sync for ${REPO} could not be merged into ${BRANCH}."
  fi
else
  git checkout "${BRANCH}"
  if git merge --no-edit sync/template; then
    git push origin "${BRANCH}"
  else
    git merge --abort
    git checkout sync/template
    git push -u origin sync/template
    upsert_pr
  fi
fi
