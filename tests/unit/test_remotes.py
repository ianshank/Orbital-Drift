"""Unit tests for the shared remote-URL normalizer (charter C-5).

The guard and pre-push tests exercise remotes.py through bash subprocesses;
these pin its Python behavior directly — one normalizer, one truth.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orbital_drift import remotes

CANONICAL = "github.com/ianshank/orbital-drift"


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/ianshank/Orbital-Drift.git",
        "https://github.com/ianshank/Orbital-Drift",
        "git@github.com:ianshank/Orbital-Drift.git",
        "ssh://git@github.com/ianshank/Orbital-Drift.git",
        "HTTPS://GITHUB.COM/ianshank/Orbital-Drift.git/",
        "https://github.com/ianshank/Orbital-Drift.git\r",
    ],
)
def test_equivalent_spellings_normalize_identically(url: str) -> None:
    assert remotes.normalize(url) == CANONICAL


def test_different_repos_stay_different() -> None:
    assert remotes.normalize("https://github.com/evil/Orbital-Drift.git") != CANONICAL
    assert remotes.normalize("https://gitlab.com/ianshank/Orbital-Drift.git") != CANONICAL


def _allowlist(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "allowed-remotes.txt"
    path.write_text(content, encoding="utf-8")
    return path


def test_allowlisted_url_passes(tmp_path: Path) -> None:
    allowlist = _allowlist(tmp_path, "# comment\nhttps://github.com/ianshank/Orbital-Drift.git\n")
    assert remotes.is_allowlisted("git@github.com:ianshank/Orbital-Drift.git", allowlist)


def test_unlisted_url_fails(tmp_path: Path) -> None:
    allowlist = _allowlist(tmp_path, "https://github.com/ianshank/Orbital-Drift.git\n")
    assert not remotes.is_allowlisted("https://github.com/evil/repo.git", allowlist)


def test_main_exit_codes(tmp_path: Path) -> None:
    allowlist = _allowlist(tmp_path, "https://github.com/ianshank/Orbital-Drift.git\n")
    ok = remotes.main(
        [
            "--check-url",
            "https://github.com/ianshank/Orbital-Drift.git",
            "--allowlist",
            str(allowlist),
        ]
    )
    assert ok == 0
    denied = remotes.main(
        ["--check-url", "https://github.com/evil/repo.git", "--allowlist", str(allowlist)]
    )
    assert denied == 1


def test_missing_allowlist_returns_the_error_code(tmp_path: Path) -> None:
    """Exit 2 (error), distinct from exit 1 (not allowlisted): callers render
    the two differently and a conflation blamed the allowlist for a broken
    venv.

    This absorbed a byte-for-byte twin (``test_main_fails_closed_on_missing_
    allowlist``) that differed only in the name of the file it did not create —
    same argv shape, same assertion, same line of ``remotes.py``. Two names for
    one case is not defence in depth: it inflates the count while adding no
    input class, and the next reader has to diff them to find that out. The
    genuinely different input classes are kept and are immediately below:
    a path that is a DIRECTORY (the ``is_file()`` guard) and a read that raises
    ``OSError`` (the ``except`` branch).
    """
    assert (
        remotes.main(["--check-url", "https://x/y.git", "--allowlist", str(tmp_path / "no.txt")])
        == 2
    )


def test_unreadable_allowlist_fails_closed(tmp_path: Path) -> None:
    """A directory where a file is expected fails closed too.

    This is the ``is_file()`` guard (main()'s FIRST check), not the ``except
    OSError`` branch below it: ``Path.is_file()`` returns ``False`` for a
    directory, so this hits the same "allowlist is missing" message a
    nonexistent path would — see
    ``test_main_fails_closed_when_the_allowlist_read_raises_oserror`` for the
    actually-distinct ``except OSError`` path this docstring used to claim
    (wrongly) that this test covered.
    """
    directory = tmp_path / "as-a-directory"
    directory.mkdir()
    assert remotes.main(["--check-url", "https://x/y.git", "--allowlist", str(directory)]) == 2


def test_main_fails_closed_when_the_allowlist_read_raises_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real ``except OSError`` branch in ``main()`` — distinct from a
    missing/non-file path (``is_file()`` returning ``False``, covered above).

    Simulated via monkeypatch rather than a permission-denied fixture: file
    permissions are not portable across this repo's authoring platform
    (Windows) and CI (Linux), and a directory hits the OTHER branch (``is_file()``
    is ``False`` before ``is_allowlisted`` is ever called), not this one. Before
    this test, lines 78-80 of remotes.py — the only place that branch's own
    "failing closed" message is produced — were never executed by any test.
    """
    allowlist = _allowlist(tmp_path, "https://github.com/ianshank/Orbital-Drift.git\n")

    def _boom(_url: str, _path: Path) -> bool:
        raise OSError("simulated read failure")

    monkeypatch.setattr(remotes, "is_allowlisted", _boom)
    result = remotes.main(
        [
            "--check-url",
            "https://github.com/ianshank/Orbital-Drift.git",
            "--allowlist",
            str(allowlist),
        ]
    )
    assert result == 2


@pytest.mark.parametrize(
    "content",
    ["", "# only a comment\n", "\n\n   \n"],
)
def test_empty_or_comment_only_allowlist_permits_nothing(tmp_path: Path, content: str) -> None:
    allowlist = _allowlist(tmp_path, content)
    assert not remotes.is_allowlisted("https://github.com/ianshank/Orbital-Drift.git", allowlist)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://github.com:443/ianshank/Orbital-Drift.git",
            "github.com:443/ianshank/orbital-drift",
        ),
        ("ssh://git@[::1]:22/x/y.git", "[::1]:22/x/y"),
        # A file:// URL normalizes to a bare path — noted here as a pinned
        # SHAPE, not as a filesystem location this test ever touches.
        ("file:///tmp/evil.git", "/tmp/evil"),  # noqa: S108
        ("../evil-repo", "../evil-repo"),
        ("", ""),
    ],
)
def test_normalize_pins_the_odd_shapes(url: str, expected: str) -> None:
    """These all normalize to something that is NOT the allow-listed value, so
    they fail closed. Pinned so a normalizer change cannot quietly make one of
    them collide with a permitted entry."""
    assert remotes.normalize(url) == expected


def test_case_folding_applies_to_the_whole_reference() -> None:
    """Documented behaviour (see normalize's docstring): conservative on
    case-sensitive forges — it can only ever match a spelling already listed."""
    assert remotes.normalize("https://github.com/IANSHANK/ORBITAL-DRIFT") == (
        "github.com/ianshank/orbital-drift"
    )
