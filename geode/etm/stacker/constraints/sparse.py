"""
Sparse-station contribution to the stacker's normal-equation system.

A sparse station owns no parameters, so it can't be constrained the way
InterseismicConstraint/CoseismicConstraint/PostseismicConstraint constrain a full
station's own (already estimated) parameter against the interpolated field -- there is
no "own parameter" on the other side of that comparison. Instead, this builds a direct
observation-equation contribution: a per-epoch design matrix mapping the global
parameters (through the same spatial interpolation coefficients the mutual full-station
constraints use) to the station's own cleaned data, weighted uniformly per epoch and
folded straight into the base system -- the same role a full station's own normal
equations play, not a small h_sigma-weighted smoothing constraint row.

Algebraically this is a local *generalized* least squares over the sparse station's own
temporal basis (velocity / jump / decay columns, taken directly from its constrained
ETM's design matrix), "spread" into the global parameter space through the spatial
interpolation coefficients (BaseConstraint._build_k_matrix, reused here for a target
with no owned parameter -- see the small additions in interseismic.py/sw_okada.py).

The per-epoch weighting is NOT a uniform scalar: thousands of daily GPS epochs are not
independent observations (flicker/random-walk-type temporal correlation), so treating
them as such massively overstates a sparse station's effective information content --
large enough, empirically, to distort full stations far from it. Instead the station's
own NoiseCovarianceGLS-fitted colored-noise covariance (a lag-indexed function, not a
materialized matrix -- see AdjustmentResults.noise_covariance_lag) is rebuilt as a
Toeplitz matrix over this station's own epochs and applied via a Cholesky whitening
transform, the same technique noise_covariance.gls_fit uses for a station's own GLS fit.
There is no uniform-weight fallback for a component where that noise model isn't
available: a uniform per-epoch weight is exactly the thing this module exists to avoid
(see above), so a component lacking it is dropped from the sparse contribution rather
than given a fabricated weight.

No h_sigma/v_sigma-style smoothing tolerance is applied here: that role exists for full
stations because they have an already-estimated own parameter to smooth against a
tolerance; a sparse station has nothing to play that role, and there's no principled way
to reuse h_sigma at the per-epoch level without it collapsing into noise (tried and
abandoned during development of this).
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
from tqdm import tqdm

from .. import etm_builder
from ..data_classes import Station
from ...core.type_declarations import JumpType
from ...least_squares.noise_covariance import build_toeplitz_covariance, cholesky_whiten_design
from ....dbConnection import Cnn
from ....elasticity.elastic_interpolation import get_radius
from ....Utils import stationID, print_yellow

# Co-located stations (e.g. two antennas on the same monument) make the spatial
# interpolation system singular or severely ill-conditioned for a brand new evaluation
# point. Full-station mutual constraints are shielded from this by
# BaseConstraint.constrain_nearby_stations; a sparse station being evaluated here isn't,
# so duplicates are dropped before computing interpolation coefficients.
COLOCATION_THRESHOLD_KM = 0.01


def dedup_colocated_stations(stations: List[Station],
                             threshold_km: float = COLOCATION_THRESHOLD_KM) -> List[Station]:
    """Drop near-duplicate-location stations, keeping one representative per cluster."""
    if len(stations) < 2:
        return list(stations)

    xy = np.array([stn.projected_coords for stn in stations])
    r, _, _ = get_radius(xy, xy)
    np.fill_diagonal(r, np.inf)

    keep: List[Station] = []
    dropped = set()
    for i, stn in enumerate(stations):
        if i in dropped:
            continue
        keep.append(stn)
        for j in np.where(r[i] < threshold_km)[0]:
            if j > i:
                dropped.add(int(j))

    if len(keep) < len(stations):
        tqdm.write(f' -- Dropped {len(stations) - len(keep)} co-located station(s) from '
                   f'interpolation sources (< {threshold_km * 1000:.0f} m apart)')

    return keep


@dataclass
class SparseField:
    """One spatially-interpolated global field this sparse station's data can see: the
    interseismic velocity field, or a coseismic/postseismic field for one earthquake.

    `basis` is the station's own temporal design-matrix column for this field (velocity
    Δt, coseismic step, or postseismic decay), sliced directly from its constrained ETM's
    design matrix -- not recomputed -- so it's full length (one value per epoch of that
    ETM's own time vector, before inlier filtering). ke/kn/ku are the (already
    globally-placed) spatial interpolation coefficient rows.
    """
    label: str
    basis: np.ndarray
    ke: np.ndarray
    kn: np.ndarray
    ku: np.ndarray

    def row(self, component: int) -> np.ndarray:
        return (self.ke, self.kn, self.ku)[component].flatten()


def build_fields(stacker, stn: Station) -> List[SparseField]:
    """Find every spatially-interpolated field (interseismic + per-earthquake
    coseismic/postseismic) this sparse station can be predicted from, i.e. every field
    with a registered constraint and more than one (deduplicated) constraining station.

    Jump identity/timing/relaxation and the temporal design columns all come from the
    station's own constrained ETM (stn.etm): it already carries every geophysical jump
    the stacker models for this station, correctly typed and validated (coseismic vs.
    postseismic-only, condition-number fallbacks, etc.) -- the free fit used elsewhere
    for nuisance removal has none of that (it was deliberately built with no
    cherry-picked earthquakes) and would be missing columns for some of them.
    """
    tp = stacker.total_parameters
    fields: List[SparseField] = []
    etm = stn.etm
    poly = etm.design_matrix.get_polynomial()

    if stacker.constraint_registry.constraints['interseismic']:
        inter = stacker.constraint_registry.constraints['interseismic'][0]
        constraining = dedup_colocated_stations([s for s in stacker.stations if s.is_interseismic])
        if len(constraining) > 1:
            # compute_constraint_coefficients reads target_station.vertical_response;
            # full stations get this once at grid-build time, this is the sparse-station
            # equivalent for a point that was never part of that build.
            stn.vertical_response = stacker.grids.compute_vertical_response_at_point(
                stn.lon, stn.lat, stacker.stations, stacker.config.vertical_method, stacker.config.tension)
            ke, kn, ku = inter._build_k_matrix(stn, constraining, stacker.grids, tp)
            fields.append(SparseField('interseismic', poly.design[:, 1], ke, kn, ku))

    for event in stacker.earthquakes:
        jump = etm.jump_manager.get_geophysical_jump(event.id)
        if not (jump and jump.fit):
            continue

        if jump.p.jump_type < JumpType.POSTSEISMIC_ONLY:
            coseis = next((c for c in stacker.constraint_registry.constraints['coseismic']
                          if c.event.id == event.id), None)
            if coseis is not None and not coseis.is_collision:
                constraining = dedup_colocated_stations(coseis.station_list)
                if len(constraining) > 1:
                    ke, kn, ku = coseis._build_k_matrix(stn, constraining, stacker.grids, tp)
                    step_col = jump.get_jump_col(return_col_of_design_matrix=False)
                    fields.append(SparseField(f'coseismic:{event.id}', jump.design[:, step_col], ke, kn, ku))

        for relax in stacker.config.relaxation:
            relax_cols = jump.get_relaxation_cols(relax, return_col_of_design_matrix=False)
            if not relax_cols:
                continue
            post = next((c for c in stacker.constraint_registry.constraints['postseismic']
                        if c.event.id == event.id and c.relaxation == relax), None)
            if post is not None and not post.is_collision:
                constraining = dedup_colocated_stations(post.station_list)
                if len(constraining) > 1:
                    ke, kn, ku = post._build_k_matrix(stn, constraining, stacker.grids, tp)
                    fields.append(SparseField(f'postseismic:{event.id}:{relax:.3f}',
                                              jump.design[:, relax_cols[0]], ke, kn, ku))

    return fields


def overall_validity_date(stacker) -> Optional[float]:
    """
    The fyear epoch from which a sparse station's contribution should actually be used,
    or stacker.config.sparse_validity_override when set, which always wins.

    This is a property of the full-station network's own data, not of any particular
    field/temporal-basis function: the Nth-earliest first_obs among ALL full stations
    (stacker.config.sparse_validity_min_stations, 2 by default) -- the date by which at
    least that many full stations actually have data, full stop. Earlier designs here
    tried to make this per-field (interseismic vs. each coseismic/postseismic field) and
    took the latest of those; that was wrong -- it let one field's own constraining
    subset (which can be a small, recent set of stations for a specific event) drag the
    whole station's validity date arbitrarily late, when what actually determines
    whether riop's own data means anything is simply how much of the network was there
    to begin with, at all.
    """
    if stacker.config.sparse_validity_override is not None:
        return stacker.config.sparse_validity_override

    n = stacker.config.sparse_validity_min_stations
    if len(stacker.stations) < n:
        return None
    first_obs = sorted(stn.first_obs.fyear for stn in stacker.stations)
    return first_obs[n - 1]


def clean_station_data(stn: Station) -> Tuple[np.ndarray, np.ndarray, List[np.ndarray], List[np.ndarray], List[np.ndarray]]:
    """
    Return (time_vector_fyear, time_vector_mjd, cleaned[3], inlier_mask[3], noise_lag[3])
    -- E, N, U order, matching the rest of the system.

    cleaned is the constrained ETM's own data (stn.etm) with constant + periodic +
    mechanical-jump terms removed; velocity and any geophysical jump/decay signal are
    left in, since those are exactly what the spatially-interpolated field is meant to
    explain. The nuisance VALUES being removed come from stn.free_fit -- an independent
    fit with no cherry-picked earthquakes -- not from stn.etm's own fit: stn.etm jointly
    estimates those nuisance terms alongside the geophysical jumps it fits, so using its
    own nuisance parameters to clean its own data would reintroduce exactly the bias this
    is meant to avoid. The free fit's parameters are evaluated at stn.etm's own epochs
    (override_time_vector) so the whole computation shares one time base/outlier mask.

    noise_lag[c] is stn.free_fit's own fitted colored-noise covariance function
    (AdjustmentResults.noise_covariance_lag, in meters^2, indexed by integer day lag).
    It describes the station's measurement noise, which doesn't depend on which jump
    list was used to fit it, so it's safe to reuse against stn.etm's own epochs even
    though it was estimated from stn.free_fit's (very likely near-identical) ones.
    Empty when stn.free_fit's adjustment model wasn't NoiseCovarianceGLS or it fell back
    to the plain robust fit -- the caller treats that as "no noise model available".
    """
    etm = stn.etm
    free = stn.free_fit

    design_matrix = etm.design_matrix
    time_vector = design_matrix.time_vector
    mask = etm.config.modeling.get_observation_mask(etm.solution_data.time_vector)
    time_vector_mjd = etm.solution_data.time_vector_mjd[mask]
    l = etm.solution_data.transform_to_local()  # NEU, aligned to time_vector

    free_poly = free.design_matrix.get_polynomial()
    free_periodic = free.design_matrix.get_periodic()

    # NEU (fit/solution_data component order) -> ENU (the rest of the system's order),
    # matching the convention _create_normal_equations already uses.
    neu_of = [1, 0, 2]

    cleaned: List[np.ndarray] = []
    inlier_masks: List[np.ndarray] = []
    noise_lags: List[np.ndarray] = []

    for c in range(3):
        i = neu_of[c]

        nuisance = np.full(time_vector.shape, free_poly.p.params[i][0])  # constant term only
        if free_periodic is not None and free_periodic.p.params[i].size > 0:
            # the size check matters, not just a style choice: get_design_ts re-analyzes
            # data gaps against whatever time vector it's given when its own param_count
            # is 0, which can *find* fittable frequencies against stn.etm's (longer) span
            # even though nothing was actually fit here -- producing a design with more
            # columns than free_periodic.p.params has values for. Skipping the call
            # entirely when there's nothing fitted is also just correct: no periodic
            # signal was estimated, so there's nothing to subtract.
            nuisance = nuisance + free_periodic.eval(i, override_time_vector=time_vector)
        for f in free.design_matrix.functions:
            if f.p.object == 'jump' and f.fit and not f.is_geophysical():
                nuisance = nuisance + f.eval(i, override_time_vector=time_vector)

        cleaned.append(l[i] - nuisance)
        inlier_masks.append(etm.fit.results[i].outlier_flags.astype(bool))
        noise_lags.append(free.fit.results[i].noise_covariance_lag)

    return time_vector, time_vector_mjd, cleaned, inlier_masks, noise_lags


def compute_sparse_system_contribution(stacker, stn: Station
                                       ) -> Optional[Tuple[np.ndarray, np.ndarray, float, int]]:
    """Build this sparse station's (3*total_parameters, 3*total_parameters) /
    (3*total_parameters,) contribution to the base system, or None if it has nothing
    to contribute (no free fit, or no interpolated field reaches it).

    Also returns (lpl, n_equations): the weighted sum of squared cleaned observations
    (Σ w·obs²) and the inlier-epoch count summed over E/N/U. These are exactly the
    "y^T P y" and equation-count terms _create_normal_equations implicitly contributes
    for a full station -- the caller must fold them into solve()'s own lpl/dof alongside
    neq_contrib/ceq_contrib, or the variance-of-unit-weight bookkeeping goes wrong.
    """
    if stn.free_fit is None:
        tqdm.write(f' -- Sparse station {stationID(stn)} has no free fit; skipping sparse contribution')
        return None

    fields = build_fields(stacker, stn)
    if not fields:
        tqdm.write(f' -- Sparse station {stationID(stn)}: no interpolated field reaches it; skipping')
        return None

    validity = overall_validity_date(stacker)
    time_vector, time_vector_mjd, cleaned, inlier_masks, noise_lags = clean_station_data(stn)
    if validity is not None:
        valid_epoch = time_vector >= validity
        n_dropped = int(np.sum(inlier_masks[0] & ~valid_epoch))
        if n_dropped:
            tqdm.write(f' -- Sparse station {stationID(stn)}: dropping {n_dropped} epoch(s) before '
                       f'{validity:.3f} (model validity: fewer than '
                       f'{stacker.config.sparse_validity_min_stations} full stations with data before then)')
        inlier_masks = [m & valid_epoch for m in inlier_masks]

    tp = stacker.total_parameters
    neq_contrib = np.zeros((tp * 3, tp * 3))
    ceq_contrib = np.zeros((tp * 3,))
    lpl = 0.0
    n_equations = 0

    for c in range(3):
        mask = inlier_masks[c]
        if not np.any(mask):
            continue

        phi = np.column_stack([f.basis[mask] for f in fields])
        obs = cleaned[c][mask]

        n_local, c_local, obs_contrib = _whiten_and_weight(
            stn, 'ENU'[c], phi, obs, time_vector_mjd[mask], noise_lags[c],
            common_mode_sigma=stacker.config.sparse_common_mode_sigma)
        if n_local is None:
            continue

        if stacker.config.sparse_constraint_mode:
            n_c, c_c, lpl_c, neq_c = _collapse_to_tempered_constraint(stacker, fields, n_local, c_local, c)
            neq_contrib += n_c
            ceq_contrib += c_c
            lpl += lpl_c
            n_equations += neq_c
        else:
            spatial = np.vstack([f.row(c) for f in fields])
            neq_contrib += spatial.T @ n_local @ spatial
            ceq_contrib += spatial.T @ c_local
            lpl += obs_contrib
            n_equations += int(np.sum(mask))

    tqdm.write(f' -- Sparse station {stationID(stn)}: {len(fields)} field(s) '
               f'({", ".join(f.label for f in fields)}), '
               f'{int(np.sum(inlier_masks[0]))}/{int(np.sum(inlier_masks[1]))}/'
               f'{int(np.sum(inlier_masks[2]))} inlier epochs (E/N/U)')

    return neq_contrib, ceq_contrib, lpl, n_equations


# Singular-value threshold (relative to the largest singular value) for the pseudo-inverse
# in _collapse_to_tempered_constraint: directions with a singular value below this fraction
# are treated as unconstrained by the data and zeroed, rather than inverted -- see that
# function's docstring. 1e-8 is deliberately loose (numpy's own historical lstsq default is
# close to machine precision): we're not just guarding against exact linear dependence, we
# want near-collinear columns (e.g. a coseismic step indistinguishable from a fast-decaying
# postseismic term over a short post-event window) caught too, before they can turn into a
# large, noise-driven coefficient.
_SPARSE_CONSTRAINT_RCOND = 1e-8


def _field_h_v_sigma(label: str, config) -> Tuple[float, float]:
    """(h_sigma, v_sigma) already configured for this field's own mutual constraint type --
    reused here so a sparse station's constraint-style injection is capped at exactly the
    same trust ceiling every other station-to-station spatial coupling of that type already
    respects (see BaseConstraint.collect_constraints)."""
    if label == 'interseismic':
        return config.interseismic_h_sigma, config.interseismic_v_sigma
    if label.startswith('coseismic:'):
        return config.coseismic_h_sigma, config.coseismic_v_sigma
    return config.postseismic_h_sigma, config.postseismic_v_sigma


def _collapse_to_tempered_constraint(stacker, fields: List[SparseField], n_local: np.ndarray,
                                     c_local: np.ndarray, component: int
                                     ) -> Tuple[np.ndarray, np.ndarray, float, int]:
    """
    Alternative to direct data-strength injection (compute_sparse_system_contribution's
    default `spatial.T @ n_local @ spatial`): collapse this component's joint local system
    -- still built over ALL of this station's active fields together, so a pair of
    near-collinear columns (e.g. coseismic step vs. postseismic decay over a short window)
    is never forced apart -- down to one point estimate per field via the Moore-Penrose
    pseudo-inverse, the same technique SWOkada already uses for its own ill-conditioned
    leave-one-out systems (see sw_okada.py's _compute_interpolation_coefficients). Along any
    direction the data doesn't actually constrain, the pseudo-inverse returns exactly zero
    instead of dividing by a near-zero singular value -- which is what keeps a rank-deficient
    n_local from turning into a wild, noise-driven estimate once "inverted".

    Each field's resulting point estimate is then injected as a single constraint row,
    weighted by that field's OWN existing h_sigma/v_sigma -- the same cap every other
    station-to-station spatial coupling in this system already lives under (base.py's
    collect_constraints), rather than by this station's own (data-strength) GLS precision.
    That's the actual change from the default path: not the degeneracy handling above (the
    default path never needed to invert n_local at all, since it folds n_local directly into
    the shared system without first solving for a point estimate), but how much influence the
    result is allowed to have afterward.
    """
    theta_hat, _, _, _ = np.linalg.lstsq(n_local, c_local, rcond=_SPARSE_CONSTRAINT_RCOND)

    tp = stacker.total_parameters
    neq_contrib = np.zeros((tp * 3, tp * 3))
    ceq_contrib = np.zeros((tp * 3,))
    lpl = 0.0
    n_equations = 0

    for k, f in enumerate(fields):
        h_sigma, v_sigma = _field_h_v_sigma(f.label, stacker.config)
        sigma = h_sigma if component < 2 else v_sigma
        if sigma <= 0:
            continue

        row = f.row(component)
        w = 1.0 / sigma ** 2
        neq_contrib += w * np.outer(row, row)
        ceq_contrib += w * row * theta_hat[k]
        lpl += w * theta_hat[k] ** 2
        n_equations += 1

    return neq_contrib, ceq_contrib, lpl, n_equations


def _whiten_and_weight(stn: Station, label: str, phi: np.ndarray, obs: np.ndarray,
                       t_mjd: np.ndarray, noise_lag: np.ndarray,
                       common_mode_sigma: float = 0.0
                       ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], float]:
    """
    Reduce one component's (M-epoch phi, obs) to its (n_fields, n_fields) weighted normal
    matrix / (n_fields,) weighted right-hand side, properly accounting for the station's
    own colored-noise covariance when available.

    common_mode_sigma (meters), when > 0, adds a single scalar (common_mode_sigma**2) to
    every entry of the covariance before whitening -- equivalent to adding a rank-1,
    perfectly-correlated-across-every-epoch term, i.e. "this station's own realization of
    the field may carry an unknown constant offset of up to common_mode_sigma, no matter
    how much data it has." This is the same geophysical trust ceiling role h_sigma/v_sigma
    play for a full station's own estimated parameter, just expressed in observation space
    instead of parameter space: a sparse station's gaps mean velocity/coseismic/
    postseismic can't be reliably separated into per-field parameters on their own (that
    separation is supposed to come from the rest of the network once this is folded into
    the shared system), so there's no per-field parameter to cap directly. Being rank-1
    rather than added to the diagonal, it does not average away as epochs accumulate --
    it bounds how precisely an offset-like direction can be pinned down by this station
    alone, independent of its own formal noise model.

    Returns (n_local, c_local, obs_contrib) where obs_contrib is this component's
    contribution to lpl (the weighted sum of squared observations); (None, None, 0.) if
    this component has no usable weight at all.
    """
    if noise_lag.size:
        # beyond the fitted lag range (shouldn't normally happen: stn.etm's epochs come
        # from the same underlying data as stn.free_fit's), build_toeplitz_covariance
        # plateaus at the largest fitted lag rather than indexing out of bounds.
        c_mat = build_toeplitz_covariance(t_mjd, noise_lag)
        if common_mode_sigma:
            c_mat = c_mat + common_mode_sigma ** 2

        try:
            return cholesky_whiten_design(
                phi, obs, c_mat, f'{stationID(stn)} {label} sparse-station noise covariance')
        except np.linalg.LinAlgError as e:
            tqdm.write(print_yellow(f' -- Sparse station {stationID(stn)} {label}: {e}; skipping component'))
            return None, None, 0.

    # No GLS noise model for this component (not enough inliers, non-unique days, or the
    # noise model estimation failed and NoiseCovarianceGLS fell back to the plain robust
    # fit): there's no principled per-epoch weight to fall back to here (a uniform one is
    # exactly the failure mode this module was built to avoid -- see module docstring),
    # so this component is dropped from the sparse contribution rather than guessed at.
    tqdm.write(print_yellow(
        f' -- Sparse station {stationID(stn)} {label}: no GLS noise covariance available; '
        f'excluding this component from the sparse contribution'))
    return None, None, 0.


def build_sparse_free_fits(stacker, cnn: Cnn) -> None:
    """
    Build (or rebuild) station.free_fit for every sparse station, windowed to the
    network's own model-validity date (overall_validity_date) when one can be
    determined. Needs every full station already added (self.stations complete) --
    doesn't otherwise depend on constraint registration, so this can run any time after
    the add_station loop, not just from build_system().

    The windowing matters beyond "don't use data from before the field could predict
    anything": the free fit's own constant/periodic/mechanical terms are jointly
    estimated over whatever span it's built from, and by construction of that fit its
    own residual only has zero mean/trend averaged over that WHOLE span. Fitting it over
    the station's full history and then only using a later subset in
    compute_sparse_system_contribution would leave the free fit's residual free to carry
    a nonzero mean/trend within that subset -- which, since the sparse system has no
    intercept column of its own, gets misattributed directly into the estimated global
    velocity/jump parameters. Building the free fit on the validity-windowed span to
    begin with keeps its own zero-mean/zero-trend guarantee aligned with the span
    actually used.
    """
    if not stacker.config.sparse_constraints_enabled:
        return

    validity = overall_validity_date(stacker)
    window = [[validity, 9999.]] if validity is not None else None
    if window is not None:
        tqdm.write(f' -- Sparse stations: free fits windowed to {validity:.3f} onward '
                   f'(model validity: fewer than {stacker.config.sparse_validity_min_stations} '
                   f'full stations with data before then)')

    if stacker.config.parallel_workers <= 1 or len(stacker.sparse_stations) <= 1:
        for stn in stacker.sparse_stations:
            stn.free_fit = etm_builder.build_free_fit(cnn, stn.network_code, stn.station_code,
                                                      data_model_window=window)
        return

    # Same local-cores-only multiprocessing as EtmStacker.add_stations_parallel, and for
    # the same reason: each sparse station's free fit is an independent, GLS-fit (always
    # slow) unit of work. window is identical for every job here (computed once above),
    # unlike add_stations_parallel's per-station config.
    import multiprocessing as mp

    jobs = [(stn.network_code, stn.station_code, window) for stn in stacker.sparse_stations]

    tqdm.write(f' -- Building {len(jobs)} sparse-station free fit(s) across '
              f'{stacker.config.parallel_workers} worker processes')

    results = {}
    ctx = mp.get_context('fork')
    with ctx.Pool(processes=stacker.config.parallel_workers) as pool:
        for network_code, station_code, etm, error in pool.imap(etm_builder.build_free_fit_job, jobs):
            if error is not None:
                tqdm.write(print_yellow(
                    f' -- Worker exception building free fit for {network_code}.{station_code}, '
                    f'skipping:\n{error}'))
            results[(network_code, station_code)] = etm

    for stn in stacker.sparse_stations:
        stn.free_fit = results[(stn.network_code, stn.station_code)]


def add_sparse_contributions(stacker, system_neq: np.ndarray, system_ceq: np.ndarray) -> Tuple[float, int]:
    """Fold every sparse station's contribution into the base system in place.
    No-op (returns (0., 0)) unless EtmStackerConfig.sparse_constraints_enabled is True.

    Returns (lpl, n_equations) summed over all sparse stations -- the caller (solve())
    must add these into its own lpl/dof for the variance-of-unit-weight bookkeeping to
    stay consistent; see compute_sparse_system_contribution's docstring.
    """
    if not stacker.config.sparse_constraints_enabled or not stacker.sparse_stations:
        return 0., 0

    tqdm.write(f'Adding sparse-station contributions ({len(stacker.sparse_stations)} station(s))...')
    total_lpl = 0.
    total_equations = 0
    for stn in stacker.sparse_stations:
        result = compute_sparse_system_contribution(stacker, stn)
        if result is None:
            continue
        neq_contrib, ceq_contrib, lpl, n_equations = result
        system_neq += neq_contrib
        system_ceq += ceq_contrib
        total_lpl += lpl
        total_equations += n_equations

    return total_lpl, total_equations
