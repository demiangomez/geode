"""
Which stations' velocities are separated from their own postseismic decay, and may
therefore define the interseismic field.

etm_builder.create_station answers this with a data-availability test: a station is
is_interseismic if its first geophysical jump postdates its first observation, i.e. if
it has clean pre-event data. That is a proxy for the question that matters -- is this
station's velocity parameter actually the interseismic rate -- and the proxy fails in
both directions.

It is too generous when a pre-onset decay term has been dropped from the fit: the column
is gone but the transient is not, so the velocity absorbs its gradient and the station
is promoted to interseismic while carrying exactly the bias it is then asked to define
the field with. It is too strict in a subduction zone with a generous earthquake
catalogue, where every station has some event before its first epoch and the eligible
set collapses to zero -- at which point InterseismicConstraint has nothing to
interpolate between at all.

What actually decides it is not whether the station saw the event, but whether the
MODEL separates its velocity from its decay amplitudes -- and in a stacker that is not a
property of the station alone. A station that came online years after an event cannot
resolve the decay from its own data, but the shared postseismic field can supply the
amplitude from the stations that do have early post-event data, and once it does, the
station's velocity parameter is free to be interseismic.

So the test here is posterior rather than prior: it asks how much of the velocity's
uncertainty is owed to not knowing the decay amplitudes, in the system as it actually
stands with the postseismic ties already applied.
"""

from typing import Dict, List

import numpy as np

from .. import console
from ..data_classes import Station
from ....Utils import stationID


def _station_block(stacker, stn: Station, component: int) -> List[int]:
    """Global column indices of this station's velocity (first) and every active decay
    amplitude, for one component. Coseismic offsets are excluded: a step is not
    collinear with a trend, so it does not compete with velocity the way a decay does
    (the same exclusion adaptive_weight makes, for the same reason)."""
    tp = stacker.total_parameters
    offset = component * tp

    cols = [int(stn.get_velocity_column()) + offset]
    for jump in stn.etm.jump_manager.jumps:
        if not jump.is_geophysical() or not jump.fit:
            continue
        for relax in stacker.config.relaxation:
            col = stn.get_postseismic_column(jump.earthquake.id, relax)
            if col is None:
                continue
            for c in np.atleast_1d(col):
                cols.append(int(c) + offset)
    return cols


def velocity_inflation(stacker, system_neq: np.ndarray, stn: Station) -> float:
    """
    By what factor is this station's velocity uncertainty inflated because its own
    postseismic decay amplitudes are unknown?

        R = sqrt( [N_block^-1]_vv * N_vv )

    where N is the system information matrix WITH the co/postseismic constraints already
    applied, and N_block is N restricted to this station's velocity plus its decay
    columns for one component.

    N_vv is the velocity's precision if every decay amplitude were known exactly;
    [N_block^-1]_vv is its variance with them unknown and everything outside the block
    held fixed. R >= 1 by construction. R = 1.1 means the entanglement costs ten per
    cent of the velocity precision; R = 8 means the velocity only looks as good as it
    does because of correlation structure, and is not separately determined.

    Returned as the worst (largest) value over the three components, so a station is
    only called separated when all three are.

    A station with no active decay terms gets 1.0: there is nothing to separate from.
    """
    worst = 1.0
    for component in range(3):
        cols = _station_block(stacker, stn, component)
        if len(cols) < 2:
            continue

        n_vv = float(system_neq[cols[0], cols[0]])
        if not (n_vv > 0.) or not np.isfinite(n_vv):
            return np.inf

        block = system_neq[np.ix_(cols, cols)]
        try:
            marginal_variance = float(np.linalg.inv(block)[0, 0])
        except np.linalg.LinAlgError:
            # perfectly collinear: nothing separates them at all
            return np.inf
        if not (marginal_variance > 0.) or not np.isfinite(marginal_variance):
            return np.inf

        worst = max(worst, float(np.sqrt(marginal_variance * n_vv)))

    return worst


def apply_interseismic_eligibility(stacker, system_neq: np.ndarray) -> Dict[str, float]:
    """
    Re-decide station.is_interseismic from the inflation ratio above, in place.

    system_neq must already carry the co/postseismic constraints and must NOT carry the
    interseismic one: those ties are what the network knows about this station's decays,
    while the interseismic field is the thing being decided, and including it would let a
    station's eligibility rest partly on the field it is about to help define.

    No-op (leaving create_station's pre-event-data rule in place) when
    EtmStackerConfig.interseismic_max_inflation is 0.
    """
    threshold = stacker.config.interseismic_max_inflation
    if not threshold:
        return {}

    ratios = {}
    changed = []
    for stn in stacker.stations:
        name = stationID(stn)
        ratio = velocity_inflation(stacker, system_neq, stn)
        ratios[name] = ratio

        eligible = ratio <= threshold
        if eligible != stn.is_interseismic:
            changed.append((name, stn.is_interseismic, eligible, ratio))
        stn.is_interseismic = eligible

    eligible = [n for n, r in ratios.items() if r <= threshold]
    console.result('Interseismic-eligible stations',
                   f'{len(eligible)} of {len(ratios)} '
                   f'(velocity inflation <= {threshold:g})')

    if changed:
        console.step(f'interseismic eligibility changed for {len(changed)} station(s)')
        console.table(['station', 'was', 'now', 'inflation'],
                      [[n, 'yes' if was else 'no', 'yes' if now else 'no',
                        'inf' if not np.isfinite(r) else f'{r:.2f}']
                       for n, was, now, r in sorted(changed, key=lambda c: c[3])],
                      align='lllr')

    if len(eligible) < 2:
        console.warn(f'only {len(eligible)} station(s) can separate velocity from '
                     f'postseismic decay; the interseismic field cannot be '
                     f'interpolated and the solve will refuse')

    return ratios
