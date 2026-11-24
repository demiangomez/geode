# ADR-0004: Patch three gnssmultipath==2.2.0 bugs post-install, tracked in-repo

- **Status:** superseded by [ADR-0005](0005-switch-to-ringo.md)
- **Date:** 2026-11-17
- **Branch (if not master):** —

> **2026-11-17: superseded, same day.** The fourth bug this ADR left deliberately unfixed (see its
> "Known issues / limitations") turned out to be one of several in the same family rather than an
> isolated edge case; Demian decided to replace `gnssmultipath` with RINGO rather than keep
> patching it indefinitely. See [ADR-0005](0005-switch-to-ringo.md). `scripts/patch_gnssmultipath.py`
> has been deleted and the `gnssmultipath` pin removed from `pyproject.toml`/`environment.yml` --
> this ADR's content below is historical only.

## Context

After fixing `pyRinex.py`'s header-sanitization gaps (blank `PRN / # OF OBS` fields, see
`knowledge/checkpoints/2026-11-17-prn-obs-blank-fix.md` and
`2026-11-17-prn-obs-multiline-fix.md`), a `--backfill -force -limit 10` run against the real
`usa.gisa` archive still failed on every file, with two new error signatures:
`IndexError: list index out of range` (3 files) and `KeyError: 2880` (7 files). Both turned out to
be real, pre-existing defects inside `gnssmultipath==2.2.0` itself, triggered by ordinary
real-world RINEX conditions (multi-GNSS files with >9 observation types; receiver gaps) — not
anything wrong with the files or with GeoDE's sanitization. Confirmed by diffing the installed
package against a clean `pip download gnssmultipath==2.2.0 --no-binary :all:` source: the installed
copy was unmodified from PyPI (an early suspicion that it carried an undocumented hand-edit was
wrong — the comment that looked like a prior patch is upstream's own wording).

Three distinct bugs, each root-caused by tracing the real installed code against real `usa.gisa`
files (not guessed):

1. **`readers/readRinexObs.py`, `rinexReadObsFileHeader211`** — on a file declaring >9 observation
   types, `PRN / # OF OBS` wraps onto a blank-PRN continuation line per satellite. The header-scan
   loop does `Sys = line_.pop(0)[0]`; on a continuation line the first token is a digit, not a
   system letter, so `if Sys not in ["G","R","E","C"]: continue` fires. Because `line` had already
   been truncated to `line[0:60]` earlier in the same iteration (dropping the trailing label), that
   `continue` re-tests a `while 'PRN / # OF OBS' in line` condition that's now false — ending the
   whole multi-satellite sweep right there, *without* running the `fid.readline()` at the bottom
   that was supposed to advance to the next line. The outer header loop re-enters this block fresh
   for the next satellite's main line, resetting `system_list = []`, so `GNSSsystems[numGNSSsystems]`
   keeps getting reassigned at key 1 — **silently collapsing `GNSSsystems` down to whichever system
   was encountered last** in the header (verified: `{1:'E',2:'G',3:'R'}` becomes `{1:'R'}`). Later,
   when an epoch's satellite list itself wraps onto a continuation line (>12 satellites visible at
   once — common on a dense multi-constellation day), `rinexReadObsBlock211`'s continuation-line
   satellite recovery filters by this now-wrong single-system list, so `SVlist` ends up shorter than
   `numSV` → `IndexError`. This is also what caused the unrelated-looking `KeyError: 1` crash in
   `GNSS_MultipathAnalysis.py` on other files (see fix 3).
2. **`signalAnalysis.py`, `SignalAnalyzer._build_lli_phase_matrix`** — sizes its output using
   `self.nepochs`, a nominal epoch count derived from the header's `TIME OF FIRST/LAST OBS`, but
   indexes into `gnss_lli`, a dict keyed only by epochs *actually* present. A real receiver gap
   (confirmed in the file: `readRinexObs211` itself logs `WARNING: nepochs calculated in advance
   (2902) != epochs processed (2879)`) leaves `gnss_lli` short of the nominal count, so
   `gnss_lli[ep + 1]` raises `KeyError` once `ep` passes the last real epoch.
3. **`GNSS_MultipathAnalysis.py`, `filter_common_gnss_keys`** — after computing satellite positions
   from a broadcast nav file that doesn't cover every system in the RINEX file (e.g. a GPS-only
   `brdc` file against a GPS+GLONASS+Galileo observation file), this function filters `GNSSsystems`
   down to the systems actually present in both, but keeps each survivor's *original* numeric key
   (`{k: v for k, v in GNSSsystems.items() if v in common}`). Later code assumes the result is
   densely keyed `1..len(GNSSsystems)` (`for sys in range(len(GNSSsystems)): GNSSsystems[sys + 1]`),
   which breaks whenever the surviving system's original key wasn't already 1 — which system that
   is turned out to vary run-to-run for reasons not fully pinned down (dict key assignment order in
   bug 1 before its own fix was not deterministic either), so this bug was intermittent.

Bugs 1 and 2 account for all 10 of the originally-failing files once fixed. Fixing bug 3 was needed
to get a *fully clean* run (not just "no exception") on files where orbit coverage is partial,
since `GNSS_MultipathAnalysis.py` reaches `filter_common_gnss_keys` on that path. A **fourth** bug
(satellites whose system was excluded via `desiredGNSSsystems` are never skipped in the per-epoch
satellite loop at `readRinexObs.py:2422-2433`, both crashing via `sys_to_idx[curr_sys]` and
misaligning `Obs`/`LLI`/`SS` row indices once skipped) was found and root-caused but **deliberately
left unfixed here** — Demian chose to ship these three now rather than keep going; see
`knowledge/known-issues.md`.

## Decision

Patch the installed `gnssmultipath` package in three places via `scripts/patch_gnssmultipath.py`,
run manually after `conda env update --file environment.yml` (documented in `INSTALL.md`), rather
than:
- Waiting on an upstream fix (no visibility into when/whether one lands, and these bugs appear
  untested upstream — they only manifest on multi-GNSS + >9-obs-type + real-world-gap files, a
  combination its own test suite evidently doesn't cover).
- Forking the package on GitHub and pinning to the fork (more overhead, and these are tiny,
  surgical, single-expression changes — a full fork is disproportionate).
- Hand-editing `site-packages` directly and leaving it undocumented (the mistake this ADR is
  itself a reaction to — an earlier, wrongly-suspected "existing hand-edit" turned out to be
  nothing, but the risk it represented — invisible, lost on env rebuild — is real and worth
  designing around).

The patch script does targeted string replacement (not a unified diff applied via `patch -p1`):
no dependency on the `patch` CLI being installed, no line-number drift to worry about, and it
fails loudly (`SKIP`, non-zero exit) if the expected original text isn't found — e.g. if
`gnssmultipath` is ever bumped to a version where these bugs look different or are already fixed.
It's idempotent: each patched file gets a `# GeoDE patch: see knowledge/decisions/0004` marker
comment, checked before re-patching.

## Rationale

- All three bugs are triggered by *valid* RINEX files and *normal* operating conditions (a
  multi-constellation CORS receiver, a nav file that doesn't cover every tracked system, a receiver
  gap) — none of them are something `pyRinex.py`'s sanitization pass could or should work around,
  unlike the earlier `PRN / # OF OBS` blank-field fixes which were genuinely about tolerating a
  legal-but-unusual RINEX 2 encoding.
- Verified each fix against the real failing files (`usa.gisa`, DOY 290/292/296 of 2026) before
  writing it into the patch script, not just against synthetic fixtures — rebuilding the exact
  crash from the real file first, then confirming the fix clears it, same discipline as the
  `pyRinex.py` fixes.
- Confirmed via a real `--backfill -force -limit 10` re-run: `ok=0 failed=10` before this patch,
  `ok=6 partial=3 failed=1` after. The 3 "partial" results are a pre-existing, correct GeoDE
  behavior (orbit coverage genuinely missing for some systems on those days — `rinex_qc_run.
  systems_without_orbits` reflects it) and not something this ADR touches. The 1 remaining failure
  is the fourth bug noted above.

## Consequences

- `gnssmultipath` stays pinned at exactly `2.2.0` (per ADR-0003) with this patch layered on top;
  bumping the pin requires re-verifying all three string replacements still apply (the script's
  `SKIP` output is the signal that a version bump needs attention) and re-checking whether the
  bugs still exist upstream before assuming the patch still applies as-is.
- `scripts/patch_gnssmultipath.py` must be re-run after any environment rebuild (`conda env update
  --file environment.yml --name geode` reinstalling the pip section, a fresh `conda create`, a CI
  runner image rebuild, etc.) — there is currently no automatic hook for this; `INSTALL.md` is the
  only place it's documented. If it's missed, the user gets exactly the two original crash
  signatures back, and this ADR + the patch script are what to reach for.
- The fourth bug (per-epoch satellite loop doesn't skip satellites whose system was excluded by
  `desiredGNSSsystems`) is now a known, scoped, narrower follow-up — see `knowledge/known-issues.md`
  — rather than something hidden inside this session's "fixed everything" story.

## Alternatives considered

- Fork `gnssmultipath` on GitHub, patch it there, pin to the fork — rejected as disproportionate for
  three single-expression fixes; revisit if the patch count grows enough that tracking diffs against
  upstream by hand becomes its own maintenance burden.
- File upstream issues/PRs and wait — not rejected outright (worth doing anyway, see
  `knowledge/known-issues.md`), but not a substitute for unblocking Demian's own backfill now.
- Work around bugs 1-3 in GeoDE's own code (e.g. by restricting `runner.py` to only ever call
  gnssmultipath with a single system at a time) — rejected: bug 1 is a pure header-parsing defect
  unrelated to which systems GeoDE asks for, and artificially serializing per-system calls would
  multiply gnssmultipath's already-nontrivial per-file runtime for no benefit.
