This fixture intentionally documents the two path-resolution domains used by AutoDev reusable workflows.

- `$/.github/actions/version-policy` resolves from the repository and exact commit containing the running reusable workflow.
- `./.github/actions/version-policy` resolves from the checked-out workspace and must not be used for AutoDev's internal action after the caller repository is checked out.

The executable regression assertion lives in `tests/test_git_flow_promotion_semver_wiring.py`.
