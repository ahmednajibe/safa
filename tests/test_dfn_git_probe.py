"""deepfilternet's init_df -> init_logger calls get_commit_hash(), which
spawns `git`. Upstream only catches CalledProcessError — on a machine with
no git binary (a friend's portable install) it dies with FileNotFoundError
(WinError 2) and the denoise stage fails. Our wrapper must make the probe
return None the same way it does when git exists but we're not in a repo.
"""
from __future__ import annotations

import subprocess


def test_df_git_probes_survive_missing_git(monkeypatch):
    import df.utils

    from backend.media import dfn

    dfn.patch_df_portability()

    def no_git(*a, **k):
        raise FileNotFoundError(2, "The system cannot find the file specified")

    monkeypatch.setattr(subprocess, "check_output", no_git)

    assert df.utils.get_git_root() is None
    assert df.utils.get_commit_hash() is None

    import df.logger

    assert df.logger.get_commit_hash() is None
    # get_branch_name must not explode either if reached
    assert df.logger.get_branch_name() is None


def test_df_git_probe_patch_is_idempotent():
    import df.utils

    from backend.media import dfn

    dfn.patch_df_portability()
    fn1 = df.utils.get_git_root
    dfn.patch_df_portability()
    assert df.utils.get_git_root is fn1
