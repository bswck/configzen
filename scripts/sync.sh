#!/usr/bin/env bash
# (C) 2023–present Bartosz Sławecki (bswck)
#
# Sync with bswck/skeleton.
# This script was adopted from https://github.com/bswck/skeleton/tree/338b77f/project/scripts/sync.sh.jinja
#
# Usage:
# $ poe sync


# Automatically copied from https://github.com/bswck/skeleton/tree/338b77f/handle-task-event.sh

toggle_workflows() {
    # Toggle workflows depending on the project's settings
    echo "Toggling workflows..."
    supply_smokeshow_key
    gh workflow enable smokeshow.yml
    gh workflow enable release.yml
}

determine_project_path() {
    # Determine the project path set by the preceding copier task process
    export PROJECT_PATH
    PROJECT_PATH=$(redis-cli get "$PROJECT_PATH_KEY")
}

ensure_github_environment() {
    # Ensure that the GitHub environment exists
    jq -n '{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}'|gh api -H "Accept: application/vnd.github+json" -X PUT "/repos/bswck/configzen/environments/$1" --input - | grep ''
}

supply_smokeshow_key() {
    # Supply smokeshow key to the repository
    # This is not sufficient and will become a GitHub action:
    # https://github.com/bswck/supply-smokeshow-key
    echo "Checking if smokeshow secret needs to be created..."
    ensure_github_environment "Smokeshow"
    if test "$(gh secret list -e Smokeshow | grep -o SMOKESHOW_AUTH_KEY)"
    then
        echo "Smokeshow secret already exists, aborting." && return 0
    fi
    echo "Smokeshow secret does not exist, creating..."
    SMOKESHOW_AUTH_KEY=$(smokeshow generate-key | grep SMOKESHOW_AUTH_KEY | grep -oP "='\K[^']+")
    gh secret set SMOKESHOW_AUTH_KEY --env Smokeshow --body "$SMOKESHOW_AUTH_KEY" 2> /dev/null
    if test $? = 0
    then
        echo "Smokeshow secret created."
    else
        echo "Failed to create smokeshow secret." 1>&2
    fi
}

# End of copied code

determine_new_ref() {
    # Determine the new skeleton revision set by the child process
    export NEW_REF
    NEW_REF=$(redis-cli get "$NEW_REF_KEY")
}

before_update_algorithm() {
    # Stash changes if any
    if test "$(git diff --name-only | grep "")"
    then
        echo "There are uncommitted changes in the project."
        git stash push --message "Stash before syncing with gh:bswck/skeleton"
        DID_STASH=1
    else
        echo "Working tree clean, no need to stash."
    fi
}

run_update_algorithm() {
    # Run the underlying update algorithm
    copier update --trust --vcs-ref "${1:-"HEAD"}"
    determine_new_ref
    determine_project_path
}

after_update_algorithm() {
    # Run post-update hooks, auto-commit and push changes
    cd "$PROJECT_PATH" || exit 1

    echo "Previous skeleton revision: $LAST_REF"
    echo "Current skeleton revision: ${NEW_REF:-"N/A"}"

    local REVISION_PARAGRAPH="Skeleton revision: https://github.com/bswck/skeleton/tree/${NEW_REF:-"HEAD"}"

    git add .
    if test "$LAST_REF" = "$NEW_REF"
    then
        echo "The version of the skeleton has not changed."
        local COMMIT_MSG="Patch .copier-answers.yml at bswck/skeleton@$NEW_REF"
    else
        if test "$NEW_REF"
        then
            local COMMIT_MSG="Upgrade to bswck/skeleton@$NEW_REF"
        else
            local COMMIT_MSG="Upgrade to bswck/skeleton of unknown revision"
        fi
    fi
    git commit --no-verify -m "$COMMIT_MSG" -m "$REVISION_PARAGRAPH"
    git push --no-verify
    toggle_workflows
    if test "$DID_STASH"
    then
        echo "Unstashing changes..."
        git stash pop && echo "Done!"
    fi
}

main() {
    export LAST_REF="338b77f"
    export PROJECT_PATH_KEY="$$_skeleton_project_path"
    export NEW_REF_KEY="$$_skeleton_new_ref"
    export LAST_LICENSE_NAME="MIT"

    cd "${PROJECT_PATH:=$(git rev-parse --show-toplevel)}" || exit 1
    echo
    echo "--- Last skeleton revision: $LAST_REF"
    echo
    echo "UPDATE ROUTINE [1/3]: Running pre-update hooks."
    echo "[---------------------------------------------]"
    before_update_algorithm
    echo "[---------------------------------------------]"
    echo "UPDATE ROUTINE [1/3] COMPLETE. ✅"
    echo
    echo "UPDATE ROUTINE [2/3]: Running the underlying update algorithm."
    echo "[------------------------------------------------------------]"
    run_update_algorithm "$@"
    echo "[------------------------------------------------------------]"
    echo "UPDATE ROUTINE [2/3] COMPLETE. ✅"
    echo
    echo "--- Project path: $PROJECT_PATH"
    echo
    echo "UPDATE ROUTINE [3/3]: Running post-update hooks."
    echo "[----------------------------------------------]"
    after_update_algorithm
    echo "[----------------------------------------------]"
    echo "UPDATE ROUTINE [3/3] COMPLETE. ✅"
    echo
    echo "Done! 🎉"
    echo
    echo "Your repository is now up to date with this bswck/skeleton revision:"
    echo "https://github.com/bswck/skeleton/tree/${NEW_REF:-"HEAD"}"

    redis-cli del "$PROJECT_PATH_KEY" > /dev/null 2>&1
    redis-cli del "$NEW_REF_KEY" > /dev/null 2>&1
}

main "$@"