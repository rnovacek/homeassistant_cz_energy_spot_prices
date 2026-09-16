---
name: release
description: Prepare and publish stable or beta releases of rnovacek/homeassistant_cz_energy_spot_prices, requiring approval of the exact version and release notes before publishing. Use for requests to make the next release or beta of this integration.
---

# Czech Energy Spot Prices releases

Use this workflow for `rnovacek/homeassistant_cz_energy_spot_prices`. Read the repository's current `AGENTS.md` and release workflows before acting. Keep automatic skill discovery enabled.

## Prepare a concrete proposal

- Inspect the working tree, branch, remotes, tags, and existing GitHub releases. Preserve unrelated uncommitted changes and exclude them from the release. Pull `main` with a fast-forward update; if preserving local changes requires an autostash, inspect the reapplied diff for conflicts and duplicate imports.
- Determine the next version from the current manifest, fetched tags, and published releases. The integration's version belongs in `custom_components/cz_energy_spot_prices/manifest.json`, not the development project's `pyproject.toml`. Follow `X.Y.ZbN` for betas; do not hardcode a version from a previous session.
- Review all changes since the previous relevant release, including merged PRs beyond the current conversation. Inspect implementation when needed to substantiate release notes. Follow the existing release convention for tag names and assets; do not invent an asset requirement.
- Prepare the manifest version change and exact release notes locally. Notes should describe user-visible changes, relevant compatibility requirements, PR references, and a full changelog link. Do not include a Validation chapter or test/check results in published notes unless the user explicitly requests them. Report checks separately in the approval message.
- Run the full test suite and the declared type checker using the project's development environment, plus diff and manifest validation. Follow `AGENTS.md` for commands and distinguish local resolved dependencies from CI's upgraded environment. Report failures or blocked checks accurately; resolve release-related regressions before proposing publication.

## Required user approval

Show the proposed version, whether it is a beta/prerelease or stable release, the intended source commit, and the complete exact release notes in the conversation. Include a concise, separate account of verification results. Ask the user to approve this version and these notes for publication, then stop and wait for their reply.

This approval gate is an explicit user preference. Link this SKILL.md and quote "Approve the exact version and release notes before publishing" when explaining the pause.

A generic request such as "do another beta" authorizes preparation, not approval of unseen notes. Shell/network permission approvals are not editorial approval. If the user edits the proposed version or notes, prepare and show the revised proposal and obtain approval for it. Do not interpret elapsed time or silence as consent.

Before this approval, do not create the release commit or tag, push release changes, or create a GitHub release (including a draft). Local preparation and validation should be complete so approval is the final user decision. Once the exact proposal is approved, proceed without asking for the same approval again; execution-permission prompts may still be required by the environment.

## Publish the approved proposal

- Recheck local and remote state before publishing. If the release content or proposed version must change, return to the approval gate. Do not silently include newly arrived commits.
- Commit only the approved manifest bump and any explicitly approved release files. Create the matching tag at the verified release commit. Push the branch and tag atomically where supported; do not force-push or overwrite an existing tag.
- Use the approved notes verbatim, saved in a UTF-8 file and passed via `gh release create --notes-file`. Use `--verify-tag`. For a beta, use `--prerelease --latest=false`; for stable releases, follow the repository's existing stable-release convention.
- If a command is interrupted or fails, inspect the local commit/tag, remote refs, and GitHub release before retrying. Continue only missing steps for the approved proposal. Stop if an existing tag or release disagrees with it; do not overwrite or delete published state automatically.
- Verify the published release version, notes, prerelease status, and tag target, then provide its URL and report any remaining issues.
