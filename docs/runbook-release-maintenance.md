# Runbook: hotfix an older release line

**When.** A deployed `vX.Y.Z` needs a fix while `main` has already moved past it (new minor or major not ready to ship). Normally there is one deployment and it runs the latest release, so prefer releasing from `main`; use this only when that is not an option.

The generic procedure is the [release-please maintenance line](https://github.com/giocaizzi/harness/blob/main/plugins/dev-tools/skills/release-please/references/release-flow.md#maintenance-line). What is specific to this repo:

1. Cut the branch from the tag: `git branch release/X.Y.x vX.Y.0 && git push origin release/X.Y.x`.
2. On that branch only, let `.github/workflows/release-please.yml` run on `push: branches: [main, "release/**"]` and pass `target-branch: ${{ github.ref_name }}` to the action. `main`'s workflow stays `main`-only.
3. CI already covers it: `ci.yml` on `main` runs on PRs into `release/**`.
4. Land the fix as a `fix:` PR into `release/X.Y.x`, then merge the `chore(release): X.Y.Z+1` PR release-please opens there. The bare `vX.Y.Z+1` tag fires `cd.yml` as usual.
5. Port the same fix to `main` in its own PR, or the next release regresses it.
6. **`latest` moves backwards.** `cd.yml` pushes `ghcr.io/giocaizzi/greenhouse:latest` for every `v*` tag, and the Pi stack deploys `:latest`. After the hotfix image is built, re-run CD for `main`'s newest tag (`gh workflow run cd.yml --ref vA.B.C`) before the next deploy, unless the hotfix line is the one meant to run. On GitHub Releases, restore the newest one with `gh release edit vA.B.C --latest`.
7. Delete the branch once the line is retired; the tags keep its commits.

Done once: v3.1.1 (June 2026, `release/3.1.x`, PRs #75 and #77).
