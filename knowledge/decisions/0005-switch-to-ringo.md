# ADR-0005: Replace gnssmultipath with RINGO as the RINEX QC engine

- **Status:** accepted
- **Date:** 2026-11-17
- **Branch (if not master):** —

## Context

RinexQC's original engine, `gnssmultipath==2.2.0` (ADR-0003), turned up three confirmed,
root-caused bugs on ordinary real multi-GNSS CORS files during the first `--backfill` runs against
`usa.gisa` — a header-parsing bug that silently collapsed its tracked GNSS systems down to one, a
receiver-gap epoch-count mismatch, and a key-renumbering bug after orbit-coverage filtering (all
patched, ADR-0004). Patching those got the real backfill from `ok=0 failed=10` to
`ok=6 partial=3 failed=1` — real progress, but the one remaining failure immediately traced into a
**fourth** bug in the same family (a per-epoch satellite-skipping bug that would also misalign
array rows if naively patched), which itself exposed a **fifth**, unexplained `ValueError` once the
fourth was worked around. Given GeoDE needs to run this over roughly 22 million archive files,
Demian's assessment was that the bug count wasn't converging fast enough to trust at that scale:
*"this code has proven to be very unreliable... these crashes do not seem encouraging."* He asked
for an evaluation of switching to RINGO (GSI, Japan) instead, including a robustness check against
real archive files and a plan for installing it.

## Decision

Replace `gnssmultipath` with **RINGO** (`https://terras.gsi.go.jp/software/ringo/`) as RinexQC's
computation engine. Remove `gnssmultipath` entirely: no pip pin (`pyproject.toml`/`environment.yml`),
no patch script (`scripts/patch_gnssmultipath.py` deleted), no import anywhere in
`geode/rinex_qc/`. ADR-0003/0004 are marked superseded, not deleted (historical record of why those
bugs existed and how they were found).

RINGO is installed by `scripts/install_ringo.sh` (new) — a plain download-and-unpack of a
platform-specific static binary from GSI's own site, run once after any environment (re)build, not
a pip/conda dependency. No vendoring into the repo: GSI publishes no explicit software license for
RINGO (only a general site Terms of Use that doesn't address redistribution), so each install pulls
fresh from GSI under GSI's own terms rather than GeoDE redistributing the binary itself.

## Rationale

**Robustness, tested against real files, not just claims.** Downloaded RINGO v0.9.6 and ran it
against:
- All 10 of the real `usa.gisa` files that crashed gnssmultipath (the original `IndexError`/
  `KeyError` signatures, pre-ADR-0004) — **10/10 clean**, no pre-processing needed.
- A genuinely random sample of 25 files across 17 different networks (`usa`, `bos`, `ant`, `ecu`,
  `chs`, `bra`, `bol`, `sag`, `arg`, ...), spanning 1995-2026, daily and hourly sessions, old and
  new receivers, picked via `ORDER BY random()` per randomly-sampled station — **25/25 clean**.
- Garbage/empty/missing files — doesn't crash; exits 0 with empty stdout and a
  `[info] no obs file found` log line (handled in `runner._run_rnxcsv`: empty stdout, not exit
  code, is the failure signal).

35/35 real files, zero crashes, including every file that broke gnssmultipath.

**Simpler I/O than gnssmultipath ever had.** RINGO reads `.Z`-compressed, Hatanaka-compressed, and
plain `.gz` archive files **directly** — confirmed on real `.d.Z` and `.crx.gz` files, both RINEX 2
and RINEX 3. No `crx2rnx`, no `gfzrnx_lx` shell-out, no temp-file decompression needed for the tool
itself (`pyRinex.ReadRinex` still runs, but only for provenance metadata now — see
`knowledge/architecture/rinex-qc.md`). It also recovered gracefully from things that used to need
sanitization (missing approx position, auto-computed; mid-file interval changes, just logged).

**Installation is strictly easier than every other external dependency GeoDE has** (GAMIT/GLOBK,
GFZRNX, PPP engines) — a single static binary per platform, versioned tarballs at a predictable
URL, no build step, no other runtime dependency. `scripts/install_ringo.sh` automates the whole
thing; `gfzrnx_lx` by comparison is still a fully manual download.

**Performance is comparable or better.** ~2s/file on a dense 84-satellite, 2879-epoch day (RINGO
internally multi-threads — user time exceeds wall time), done via subprocess rather than an
in-process Python call, and with no Python-interpreter overhead for the actual computation.

## Consequences

**A new piece of real science GeoDE now owns: per-arc degree-5 polynomial detrending of multipath
before it's usable (see `knowledge/architecture/rinex-qc.md`'s "Non-obvious decisions" for the
full story).** gnssmultipath handed back an already-detrended per-epoch multipath array; RINGO's
raw `MP1`/`MP2`/`MP5` carry a residual ionospheric term that inflates a naive RMS by 5-20x (verified:
2.5m raw vs RINGO's own 0.55m text-summary value for the same satellite), and RINGO never exposes a
detrended value per epoch anywhere readable. `runner._detrend_by_arc()` fits and removes a
degree-5 polynomial per slip-bounded arc (Demian's explicit call, standard practice for separating
the ionospheric term from genuine multipath) — this landed within ~1-10% of RINGO's own
per-satellite numbers on every satellite checked, vs 15-100%+ off for simpler alternatives (flat
per-arc mean, linear per-arc detrend) that were tried and rejected first. This is now GeoDE's own
algorithm to maintain, not a black box inside a library call.

**Owning that algorithm meant owning its bugs too, and a real one reached production the same
day.** A live `--backfill` run hit `psycopg2.errors.NumericValueOutOfRange: smallint out of range`
writing `rinex_qc_signal_bins.mp_rms_mm`. Root cause: `_detrend_by_arc()` only split arcs on an
explicit slip flag, but "no slip flagged" doesn't mean "continuously observed" — it can also mean
there was nothing to compare against across a long gap (the satellite fully set and rose again
hours later, or just dropped out briefly). One arc silently spanned a 2643-epoch (~22 hour) gap
between two unrelated passes; a single fit (global or windowed) bridging it produced ±30-40m
residuals right at the boundary. Fixed two ways at once: (1) arcs are now further split on *any*
index discontinuity, not just slip flags, before fitting; (2) the fit itself moved from one
global polynomial per arc to `scipy.signal.savgol_filter` — a rolling-window polynomial fit,
chosen because a single high-degree fit over a long-but-genuinely-continuous span is *also*
prone to Runge's-phenomenon-style edge instability on its own, independent of the gap bug. Also
added a defense-in-depth guard in `writer._quantize_bins()`: a value that would still overflow
smallint is now stored as `NULL` for that one cell rather than crashing the whole file's write —
the gap bug is fixed, but a single bad bin should never again be able to take an entire otherwise-
good file down with it. Side benefit, not just a crash fix: several satellites' whole-signal RMS
dropped to match the rest of the file much more closely once the hidden gap-inflation was removed
(e.g. one file's GLONASS P2 RMS went from an earlier-reported 1.58m to 0.50m) — the bug was
inflating results across many files to a lesser degree, not just the ones extreme enough to crash.

**Multipath identity is band-level, not exact-code-level, for every signal going forward.** RINGO
reports MP/GF/MW/slips per band (1/2/5), not per exact RINEX observation code — it never says which
range+phase attribute it combined for a given band. Every RINGO-sourced `rinex_qc_signal` row has
`band_attr` like `'1?'` (not `'1C'`), `obs_code_orig` like `'MP1'`, `mp_partner` always `NULL`, and
`snr_band_only` always `True`. `signals.py`'s old RINEX2→RINEX3 `(gnss, obs_code) → band_attr`
mapping table (`normalize()`, `RINEX2_RANGE_MAP`, `SignalMapping`) is gone — nothing maps to an
exact attribute any more, so there was nothing left for it to do; only `find_snr_code()` (now
taking a bare band digit, not a band_attr) survives, simplified to match.

**`n_clock_jumps` is always `NULL` now.** gnssmultipath reported it as a QC statistic; RINGO treats
clock jumps as something to *fix* (a separate `clkcorr` command), not count. Dropped deliberately
(Demian's call) rather than attempting to derive an equivalent ourselves — revisit if it turns out
to matter.

**`rinex_qc_run.tool`/`tool_version` are now `'ringo'`/`'0.9.6'`.** Existing `gnssmultipath`-tagged
rows in a real database are untouched (different `tool` value, so they coexist rather than
collide) — a `--backfill --force` only replaces rows matching the *current* tool/version/config_hash,
so old gnssmultipath runs simply become stale, superseded history, not something this migration
needs to clean up itself.

**Verified end-to-end before calling this done**, not just unit-tested: real `pipeline.process_one_file()`
run against a real file produced sane output; a full `writer.write_db()` round-trip against a
disposable, from-scratch Postgres 16 instance (base schema dump + migrations + a real `rinex` row)
stored the expected `rinex_qc_run`/`rinex_qc_signal`/`rinex_qc_signal_bins` rows correctly, including
the new `NULL` `n_clock_jumps`/`mp_partner` and `'<band>?'` `band_attr` values.

## Alternatives considered

- **Keep gnssmultipath as a second, parallel source of information** — explicitly considered and
  rejected (Demian's call): maintaining two QC engines means two sets of patches, two sets of
  config knobs, and double the test surface for a tool already shown to be unreliable at the scale
  GeoDE needs. The schema's `tool`/`tool_version` columns already support this architecturally if
  it's ever wanted again (e.g. for cross-validation), and ADR-0003/0004 fully document how
  gnssmultipath was wired in, so nothing is lost by removing it now.
- **Keep patching gnssmultipath** — rejected per the Context section: the bug count wasn't
  converging, each fix exposed another bug in the same family, and this was before even reaching
  gnssmultipath's data-correctness risks (vs. RINGO's) at full archive scale.
- **Anubis (G-Nut/Anubis)** — surfaced during research (RINGO's own `qc`/`qclog` output format
  resembles tools in this space) but **not evaluated** in this session; no robustness testing, no
  installation assessment. Worth a look if RINGO itself turns up problems at scale later.
- **Vendor the RINGO binary into the repo** instead of downloading fresh per install — rejected:
  GSI publishes no explicit redistribution license for RINGO, only a general site Terms of Use;
  downloading directly from GSI per install sidesteps that question entirely and matches how every
  other external GeoDE dependency (GAMIT/GLOBK, GFZRNX) is already handled.
