"""
Adaptive per-station weighting for a full station's own mutual-constraint ties.

A full station's own velocity (and, for one with active geophysical jumps, each
postseismic relaxation amplitude) gets a single mutual-constraint equation tying it to
the spatially-interpolated field from its neighbors, weighted by the constraint type's
configured h_sigma/v_sigma -- see constraints/base.py's collect_constraints. That weight
is the same fixed value for every station of that type, regardless of how reliable that
particular station's own data actually is.

For most full stations this is fine: their own normal-equation diagonal for velocity is
large because their own data genuinely determines it well. But a station with a jump
whose relaxation has mostly saturated by the time most of its data arrives (so the decay
curve looks almost like a straight line over the observed span) can have velocity and
that decay term correlated upwards of 0.85-0.95 in its own normal equations -- the
diagonal is still large, but it's inflated by that internal collinearity rather than by
genuinely independent information, and the fixed mutual-constraint weight (calibrated for
a well-conditioned station) ends up far too weak to pull such a station back toward a sane
value. The result can be large, unstable swings in that station's own velocity and decay
estimates that don't show up in any other diagnostic until you compare its own
normal-equation diagonal against the weight its mutual constraint actually has.

A single global, looser h_sigma/v_sigma (as a blunt alternative to this module) was tried
empirically and rejected: loosening postseismic sigma network-wide helped the severely
entangled stations but visibly hurt previously well-behaved ones (WRMS increments that
used to be ~1-2 jumped to 10-35 for stations with no entanglement problem at all), because
a single global number can't be tight for a well-conditioned station and loose for an
entangled one at the same time. This module exists because the right amount of trust is
inherently per-station, not a constant.

This module tightens that constraint weight on a per-station basis: for each flagged
station's own velocity and postseismic-decay ties, it caps the ratio between that
station's own *effective* weight and the tie's weight at a configured target
(EtmStackerConfig.adaptive_constraint_target_ratio), tightening -- never loosening --
whichever ties would otherwise leave that ratio too large.

"Effective weight" here is NOT the raw normal-equation diagonal -- using the raw diagonal
was tried first and is exactly the inflated-by-collinearity quantity the module docstring
above describes as the problem, so using it as the yardstick for how much to trust a
station undermines the whole point. Instead this computes each parameter's marginal
precision via the Schur complement of the *entangled block* (velocity plus every active
event's decay column at that station, coseismic excluded as always): invert that small
sub-block of the station's own normal equations and read off 1/diag(inverse) for the
target parameter. This is exact linear algebra, not a heuristic -- it's the same math
behind variance inflation factors in regression diagnostics, generalized from a pairwise
correlation to however many correlated parameters a station actually has (one relaxation
term correlates with velocity; two relaxation terms on the same event correlate with each
other even more than either does with velocity, ~0.99 in the case that motivated this). A
well-conditioned station's effective weight is close to its raw diagonal (little
correlation, little discount); a severely entangled one is discounted by roughly the
inverse of its own collinearity, continuously rather than as a binary flag/no-flag
decision.

Coseismic ties are deliberately left alone: coseismic_h_sigma/v_sigma are already
effectively unconstrained (on the order of a meter) by design, and weren't implicated in
the entanglement this module addresses.

The flagged set is every full station carrying at least one active geophysical jump --
not just ones that are also a to_constrain member for interseismic. A station can have
clean pre-event baseline data (making it is_interseismic, and so a constraining rather
than to_constrain member for the interseismic field -- see
_tighten_station_equations' own, separate is_interseismic check before touching that
particular tie) and still see severe velocity/decay correlation in its own fit, driven
entirely by the post-event portion of its data: a relaxation that's mostly saturated by
the time most of a station's data arrives doesn't care whether that station also has
years of pre-event data sitting before it. is_interseismic governs eligibility for the
interseismic field alone; PostseismicConstraint.select_stations has its own, separate
eligibility criteria for postseismic ties, and a station can be a to_constrain member
there regardless. Confirmed empirically on ecu.coec: is_interseismic (first obs 2012,
first jump 2016), correlation 0.88-0.94.

This module is also what replaces a former hardcoded eligibility gate (removed from
constraints/postseismic.py and constraints/fault_geometry.py, 2026-11): that gate
zero-tied an entire postseismic event (constraint_sigma = [1e-6]*3, unreachable by any of
h_sigma/v_sigma/update_constraint/sparse settings) whenever no station's coseismic-offset
sigma cleared a hardcoded 1mm bar -- which, under gls_for_all_stations, is essentially
every station for essentially every event, since GLS honestly inflates parameter sigmas.
Because this module's "only tighten, never loosen" rule can never push a tie looser than
the configured h_sigma/v_sigma default, removing that gate and relying on this module
instead means the worst case for an event nobody can determine well is now "stays at the
ordinary default," not "hard-zeroed via a value nothing can reach."
"""

from typing import List

import numpy as np

from ..data_classes import Station


def _entangled_columns(stacker, stn: Station) -> List[int]:
    """Local (station-scoped) columns of every parameter in this station's own
    "entangled block": velocity plus one decay column per active geophysical jump, for
    every configured relaxation. Coseismic offsets are excluded -- see module docstring.
    Column 0 of the returned list is always velocity; used as a fixed anchor so callers
    can find it regardless of how many decay columns follow."""
    neq = stn.normal_equations
    cols = [stn.get_velocity_column() - neq.parameter_start_idx]
    for jump in stn.etm.jump_manager.jumps:
        if not jump.is_geophysical() or not jump.fit:
            continue
        for relax in stacker.config.relaxation:
            col = stn.get_postseismic_column(jump.earthquake.id, relax)
            if col is None:
                continue
            cols.append(int(np.atleast_1d(col)[0]) - neq.parameter_start_idx)
    return cols


def _effective_diag(neq_comp: np.ndarray, cols: List[int], target_col: int) -> float:
    """Marginal (entanglement-corrected) precision of the parameter at target_col,
    accounting for its correlation with every other parameter in cols at once -- see
    module docstring for the Schur-complement reasoning. Falls back to the raw diagonal
    if the sub-block can't be inverted (e.g. perfectly collinear columns) or target_col
    isn't in cols."""
    raw = neq_comp[target_col, target_col]
    if target_col not in cols or len(cols) == 1:
        return raw
    idx = cols.index(target_col)
    sub = neq_comp[np.ix_(cols, cols)]
    try:
        cov_sub = np.linalg.inv(sub)
    except np.linalg.LinAlgError:
        return raw
    diag_cov = cov_sub[idx, idx]
    if not (diag_cov > 0) or not np.isfinite(diag_cov):
        return raw
    return 1.0 / diag_cov


def _tighten_station_equations(stacker, stn: Station, target_ratio: float) -> None:
    """Tighten (only ever tighten) this station's own velocity and postseismic-decay
    mutual-constraint equations so each one's own/external effective-weight ratio
    doesn't exceed target_ratio. Mutates the already-collected ConstraintEquation
    objects in place; must run after collect_all_constraints (so the equations exist)
    and before add_all_constraints (so the tightened sigma is what actually gets
    applied)."""
    neq = stn.normal_equations
    cols = _entangled_columns(stacker, stn)
    vel_col = cols[0]

    def tighten(constraint, base_col: int) -> None:
        for eq in constraint.equations:
            if eq.station is not stn:
                continue
            for comp in range(3):
                own = _effective_diag(neq.neq[comp], cols, base_col)
                if own <= 0:
                    continue
                new_sigma = np.sqrt(target_ratio / own)
                old_sigma = eq.constraint_sigma[comp]
                if np.isfinite(new_sigma) and 0 < new_sigma < old_sigma:
                    eq.constraint_sigma[comp] = new_sigma

    if not stn.is_interseismic:
        interseismic = stacker.constraint_registry.constraints['interseismic'][0]
        tighten(interseismic, vel_col)

    for jump in stn.etm.jump_manager.jumps:
        if not jump.is_geophysical() or not jump.fit:
            continue
        for relax in stacker.config.relaxation:
            col = stn.get_postseismic_column(jump.earthquake.id, relax)
            if col is None:
                continue
            col = int(np.atleast_1d(col)[0]) - neq.parameter_start_idx
            postseismic = [c for c in stacker.constraint_registry.constraints['postseismic']
                           if c.event.id == jump.earthquake.id and c.relaxation == relax]
            if postseismic:
                tighten(postseismic[0], col)


def flagged_stations(stacker) -> List[Station]:
    """Full stations this adaptive weighting applies to: carrying at least one active
    geophysical jump, i.e. having at least one postseismic tie to potentially tighten.
    Deliberately NOT gated on is_interseismic -- see the module docstring for why that
    would wrongly exclude a station like ecu.coec, which has clean pre-event data but is
    still severely entangled from its post-event data alone. The "only tighten, never
    loosen" rule in _tighten_station_equations is still what actually decides whether
    any given tie changes, so including every station with a jump here is safe: a
    well-conditioned one simply won't trigger a change."""
    return [stn for stn in stacker.stations
           if any(j.is_geophysical() and j.fit for j in stn.etm.jump_manager.jumps)]


def apply_adaptive_weighting(stacker) -> None:
    """Tighten every flagged station's own velocity/postseismic-decay mutual-constraint
    ties in place. No-op when EtmStackerConfig.adaptive_constraint_weighting_enabled is
    False. Must run after constraint_registry.collect_all_constraints and before
    constraint_registry.add_all_constraints."""
    if not stacker.config.adaptive_constraint_weighting_enabled:
        return

    target_ratio = stacker.config.adaptive_constraint_target_ratio
    for stn in flagged_stations(stacker):
        _tighten_station_equations(stacker, stn, target_ratio)
