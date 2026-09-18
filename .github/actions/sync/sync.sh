#!/usr/bin/env bash
set -euo pipefail

dest="${REPO##*/}"
label="${LABEL:-${dest}}"
pretend=""
if [[ "${DRY_RUN}" == "true" ]]; then
  pretend=" --pretend"
fi
sync_type="${SYNC_TYPE:-push}"
automerge="${AUTOMERGE:-false}"

write_result() {
  local conflicts="$1"
  local path="${RESULT_FILE:-${GITHUB_WORKSPACE:-.}/sync-result.json}"
  python3 -c '
import json, os, sys
path = sys.argv[2]
data = {
    "type": os.environ["TYPE"],
    "sync_type": os.environ.get("SYNC_TYPE", "push"),
    "repo": os.environ["REPO"],
    "label": os.environ.get("LABEL") or os.environ["REPO"].rsplit("/", 1)[-1],
    "has_conflicts": sys.argv[1] == "true",
    "html_url": "https://github.com/" + os.environ["REPO"],
    "slack": os.environ.get("SLACK") or "",
}
parent = os.path.dirname(path)
if parent:
    os.makedirs(parent, exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f)
    f.write("\n")
' "${conflicts}" "${path}"
}

echo "sync_type=${sync_type}"
echo "automerge=${automerge}"
echo "dry_run=${DRY_RUN}"
echo "label=${label}"
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

case "${sync_type}" in
  event)
    plan=$(cat <<EOF
gh workflow run template-sync.yml --repo ${REPO} --ref ${BRANCH} -f vcs_ref=${VCS_REF} -f dry_run=false
EOF
)
    if [[ "${DRY_RUN}" == "true" ]]; then
      plan="# dry_run: not dispatched
${plan}"
    fi
    ;;
  pr)
    plan=$(cat <<EOF
${common}
git push --force-with-lease -u origin sync/template
${pr_upsert}

# [slack demo — never sent]
# channel: ${SLACK}
# message: Template sync PR opened/updated for ${label}: sync/template → ${BRANCH}.

git checkout ${BRANCH}
if git merge --no-edit sync/template; then
  if [ "${automerge}" = "true" ]; then
    gh pr merge sync/template --merge --auto
  fi
else
  git merge --abort

  # [slack demo — never sent]
  # channel: ${SLACK}
  # message: Template sync for ${label} could not be merged into ${BRANCH}.
fi
EOF
)
    ;;
  *)
    plan=$(cat <<EOF
${common}
git checkout ${BRANCH}
if git merge --no-edit sync/template; then
  git push origin ${BRANCH}
else
  git merge --abort
  git checkout sync/template
  git push --force-with-lease -u origin sync/template
${pr_upsert}

  # [slack demo — never sent]
  # channel: ${SLACK}
  # message: Template sync PR opened/updated for ${label}: sync/template → ${BRANCH}.
fi
EOF
)
    ;;
esac

echo "$plan"
echo

if [[ "${sync_type}" == "event" ]]; then
  echo "would run: gh workflow run template-sync.yml --repo ${REPO} --ref ${BRANCH} -f vcs_ref=${VCS_REF} -f dry_run=false"
  write_result false
  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "dry_run: not dispatching"
    exit 0
  fi
  if [[ -z "${GH_TOKEN:-}" ]]; then
    echo "GH_TOKEN is required to dispatch client workflows" >&2
    exit 1
  fi
  gh workflow run template-sync.yml --repo "${REPO}" --ref "${BRANCH}" \
    -f "vcs_ref=${VCS_REF}" -f "dry_run=false"
  echo "[slack demo — never sent] channel=${SLACK} message=Dispatched template-sync on ${label}@${BRANCH}."
  exit 0
fi

python3 -m pip install --user --quiet 'copier>=9'
export PATH="${HOME}/.local/bin:${PATH}"
git config --global user.name "copier-distributor"
git config --global user.email "41898282+github-actions[bot]@users.noreply.github.com"

if [[ -z "${GH_TOKEN:-}" ]]; then
  echo "GH_TOKEN is required to clone/push client repos" >&2
  exit 1
fi

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
git commit -m 'chore: template sync' || true

git checkout "${BRANCH}"
has_conflicts=false
if ! git merge --no-edit sync/template; then
  has_conflicts=true
  git merge --abort || true
fi
write_result "${has_conflicts}"
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "dry_run: not pushing (has_conflicts=${has_conflicts})"
  exit 0
fi

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
  echo "[slack demo — never sent] channel=${SLACK} message=Template sync PR opened/updated for ${label}: sync/template → ${BRANCH}."
}

if [[ "${sync_type}" == "pr" ]]; then
  git checkout sync/template
  git push --force-with-lease -u origin sync/template
  upsert_pr
  if [[ "${has_conflicts}" == "true" ]]; then
    echo "[slack demo — never sent] channel=${SLACK} message=Template sync for ${label} could not be merged into ${BRANCH}."
  elif [[ "${automerge}" == "true" ]]; then
    gh pr merge sync/template --merge --auto || gh pr merge sync/template --merge
  fi
  exit 0
fi

if [[ "${has_conflicts}" != "true" ]]; then
  git checkout "${BRANCH}"
  git merge --no-edit sync/template || true
  git push origin "${BRANCH}"
else
  git checkout sync/template
  git push --force-with-lease -u origin sync/template
  upsert_pr
fi
