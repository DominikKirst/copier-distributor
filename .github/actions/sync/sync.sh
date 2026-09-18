#!/usr/bin/env bash
set -euo pipefail

dest="${REPO##*/}"
pretend=""
if [[ "${DRY_RUN}" == "true" ]]; then
  pretend=" --pretend"
fi

echo "execute_gh=${EXECUTE_GH} (false → print git/gh; true → run git/gh later)"
echo "use_pr_integration=${USE_PR_INTEGRATION}"
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
  cat <<EOF
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
else
  cat <<EOF
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
fi
