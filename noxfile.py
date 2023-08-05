import argparse
from typing import cast

import nox
from nox.command import CommandFailed


@nox.session
def release(session: nox.Session) -> None:
    """
    Kicks off an automated release process by creating and pushing a new tag.

    Invokes bump2version with the posarg setting the version.

    Usage:
    $ nox -s release -- [major|minor|patch]
    """
    parser = argparse.ArgumentParser(description="Release a semver version.")
    parser.add_argument(
        "version",
        type=str,
        nargs=1,
    )
    args: argparse.Namespace = parser.parse_args(args=session.posargs)
    version: str = args.version.pop()

    # If we get here, we should be good to go
    # Let's do a final check for safety
    release_confirm = (
        input(f"You are about to release {version!r} version. Are you sure? [y/n]: ")
        .casefold()
        .strip()
    )

    # Abort on anything other than 'y'
    if release_confirm != "y":
        session.error(f"You said no when prompted to bump the {version!r} version.")

    session.run("poetry", "self", "add", "poetry-bumpversion", external=True)

    session.log(f"Bumping the {version!r} version")
    session.run("poetry", "version", version, external=True)
    new_version = (
        "v"
        + cast(
            str, session.run("poetry", "version", "--short", silent=True, external=True)
        ).strip()
    )
    session.log(f"Creating {new_version} tag...")
    try:
        session.run(
            "git",
            "tag",
            "-a",
            new_version,
            "-m",
            f"Release {new_version}",
            external=True,
        )
    except CommandFailed:
        session.log(f"Failed to create {new_version} tag, probably already exists.")
    session.log("Pushing local tags...")
    session.run("git", "push", "--tags", external=True)

    session.run("git", "diff", external=True)
    commit_confirm = (
        input(
            "You are about to commit auto-changed files due to version upgrade, "
            "see the diff view above. Are you sure? [y/n]: "
        )
        .casefold()
        .strip()
    )

    if commit_confirm == "y":
        session.run(
            "git", "commit", "-a", "-m", f"Release {new_version}", external=True
        )
        session.run("git", "push", external=True)
    else:
        session.log("Ok.")
