"""Data-version control port and a deterministic stdlib fake."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DataVersionPort(Protocol):
    """Version dataset history without exposing a specific data-versioning client."""

    def create_branch(self, branch: str, from_ref: str) -> str:
        """Create a branch at a resolved reference and return its name."""

    def commit(self, branch: str, message: str) -> str:
        """Commit on a branch and return the new commit identifier."""

    def merge(self, source_branch: str, target_branch: str) -> str:
        """Merge source into target and return the resulting target commit."""

    def resolve_commit(self, ref: str) -> str:
        """Resolve a branch or commit reference to a commit ID."""

    def protect_branch(self, branch: str) -> str:
        """Protect a branch and return its name."""


class InMemoryDataVersion:
    """A branch-and-commit fake that supplies deterministic IDs in CPU-only tests."""

    def __init__(self) -> None:
        self._branches: dict[str, str] = {"main": "commit-0000"}
        self._protected: set[str] = set()
        self._next_commit = 1

    def create_branch(self, branch: str, from_ref: str) -> str:
        """Create a branch at an existing resolved reference."""
        if branch in self._branches:
            raise ValueError(f"branch already exists: {branch}")
        self._branches[branch] = self.resolve_commit(from_ref)
        return branch

    def commit(self, branch: str, message: str) -> str:
        """Advance an unprotected branch to a deterministic new commit."""
        if not message:
            raise ValueError("commit message must be non-empty")
        if branch in self._protected:
            raise PermissionError(f"branch is protected: {branch}")
        if branch not in self._branches:
            raise KeyError(f"branch not found: {branch}")
        commit_id = f"commit-{self._next_commit:04d}"
        self._next_commit += 1
        self._branches[branch] = commit_id
        return commit_id

    def merge(self, source_branch: str, target_branch: str) -> str:
        """Set an unprotected target branch to the source branch's current commit."""
        if target_branch not in self._branches:
            raise KeyError(f"branch not found: {target_branch}")
        if target_branch in self._protected:
            raise PermissionError(f"branch is protected: {target_branch}")
        self._branches[target_branch] = self.resolve_commit(source_branch)
        return self._branches[target_branch]

    def resolve_commit(self, ref: str) -> str:
        """Resolve known branches or commit IDs to their commit ID."""
        if ref in self._branches:
            return self._branches[ref]
        if ref in self._branches.values():
            return ref
        raise KeyError(f"reference not found: {ref}")

    def protect_branch(self, branch: str) -> str:
        """Mark an existing branch protected and return its name."""
        if branch not in self._branches:
            raise KeyError(f"branch not found: {branch}")
        self._protected.add(branch)
        return branch
