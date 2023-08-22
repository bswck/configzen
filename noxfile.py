import argparse
from typing import cast

import nox
from nox.command import CommandFailed


@nox.session
def release(session: nox.Session) -> None:
    """
    Kicks off an automated release process by updating local files,
    creating and pushing a new tag.

    Usage:
    $ nox -s release -- [major|minor|patch|<major>.<minor>.<patch>]
    """
    parser = argparse.ArgumentParser(description="Release a semver version.")
    parser.add_argument(
        "version",
        type=str,
        nargs=1,
    )
    args: argparse.Namespace = parser.parse_args(args=session.posargs)
    version: str = args.version.pop()

    files_changed = session.run(
        *"git diff --name-only HEAD".split(),
        silent=True,
        external=True,
    )
    if files_changed:
        continue_confirm = (
            input(
                "There are uncommitted changes in the working tree in these files:\n"
                f"{files_changed}\n"
                "Continue? They will be included in the release commit. (y/n) [n]: ",
            )
            .casefold()
            .strip()
            or "n"
        )[0]
        if continue_confirm != "y":
            session.error("Uncommitted changes in the working tree")

    # If we get here, we should be good to go
    # Let's do a final check for safety
    release_confirm = (
        (
            input(
                f"You are about to release {version!r} version. "
                "Are you sure? (y/n) [y]: ",
            )
            .casefold()
            .strip()
        )
        or "y"
    )[0]

    if release_confirm != "y":
        session.error(f"You said no when prompted to bump the {version!r} version.")

    session.run("poetry", "self", "add", "poetry-bumpversion", external=True)

    session.log(f"Bumping the {version!r} version")
    session.run("poetry", "version", version, external=True)
    new_version = (
        "v"
        + cast(
            str,
            session.run("poetry", "version", "--short", silent=True, external=True),
        ).strip()
    )

    session.run("git", "diff", external=True)
    commit_confirm = (
        (
            input(
                "You are about to commit auto-changed files due to version upgrade, "
                "see the diff view above. Are you sure? (y/n) [y]: ",
            )
            .casefold()
            .strip()
        )
        or "y"
    )[0]

    if commit_confirm == "y":
        session.run(
            "git",
            "commit",
            "-am",
            f"Release `{new_version}`",
            external=True,
        )
        session.run("git", "push", external=True)
    else:
        session.error(
            "Changes made uncommitted. Commit your unrelated changes and try again.",
        )

    session.log(f"Creating {new_version} tag...")
    try:
        session.run(
            "git",
            "tag",
            "-a",
            new_version,
            "-m",
            f"Release `{new_version}`",
            external=True,
        )
    except CommandFailed:
        session.log(f"Failed to create {new_version} tag, probably already exists.")
    else:
        session.log("Pushing local tags...")
        session.run("git", "push", "--tags", external=True)
