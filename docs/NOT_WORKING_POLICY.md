# Kodi-Wulf preservation and **Not Working** policy

Last reviewed: 2026-10-27. Applies to Kodi-Wulf's existing package inventory.

## Review states

- **unverified** (default): archive is preserved without a claim that installation or functionality was tested.
- **working**: a specific ZIP was successfully installed and its advertised functionality exercised on a documented Kodi version/platform.
- **not working**: a specific ZIP failed installation or an actual advertised function reproducibly failed.

An upstream repository being archived, old or offline **alone is not a proof that a packaged add-on is broken**. Likewise, a structurally valid ZIP is not proof of working runtime behavior.

Record *reviewed* packages in `_data/addon_status.json`. For each manually reviewed add-on version, store `addon_id`, `version`, exact original/published ZIP `sha256`, `status`, `reason`, `evidence` (issue, test log or maintained report location), and ISO `checked_at` date. Never put unreviewed or fabricated results in the file. Hash mismatches must stop classification pending re-review.

## User interfaces

- Website and Kodi's **Install from ZIP** source share folder navigation. The `not-working/` root is a separate ZIP-browser category.
- Kodi's **Install from repository** screen does not support arbitrary custom categories: it groups add-ons by built-in types. To expose the archived list there, Kodi-Wulf can publish an **opt-in secondary repository add-on** called `Kodi-Wulf – Not Working (Archive)`. It is an archive of known-broken content, **not a claim that those ZIPs will install**. The original Kodi-Wulf repository should list only non-quarantined packages.
- Existing Kodi-Wulf installations must keep their repository identity and known working entries. A secondary repository is discoverable through Kodi-Wulf's **Add-on repositories** category, not silently installed into an existing user's profile.

## Intake

Existing `AGENTS.md` and `UPDATE_PROCESS.md` remain authoritative for researched **third-party repository installer** sources: only upstream-published ZIPs into temporary `zips/`, never fabricated foreign repository installers. Direct add-on preservation requires legitimate upstream ZIP distribution and a documented source/permission review; no unsupported or infringing content is hosted merely because a link exists.

## Verification before publication

Regenerate with the existing `tools/build.py` workflow and run `tools/validate_repo.py`, the status unit tests and a Kodi installation/runtime smoke check. Do not merge a branch based only on GitHub metadata if full package reconciliation and Kodi UI behavior were not tested.
