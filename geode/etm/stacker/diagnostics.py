"""
Interactive diagnostics for the stacker: the map/grid plot, and re-running a single
station's ETM with the stacked solution (or, for a sparse station, the interpolated
field) folded in as a constraint, for visual inspection.
"""

import copy
from typing import Optional, Tuple

import numpy as np
from tqdm import tqdm

from . import etm_builder
from .constraints import sparse as sparse_constraint
from .types import ConstraintType
from ..core.etm_engine import EtmEngine
from ..core.type_declarations import SolutionType, JumpType
from ..core.data_classes import AdjustmentModels
from ..etm_functions.polynomial import PolynomialFunction
from ..etm_functions.jumps import JumpFunction
from ..visualization.plot_fields import plot_velocity_field
from ...dbConnection import Cnn
from ...Utils import stationID


class DiagnosticsMixin:
    """Mixed into EtmStacker: read-only consumer of self.stations/self.fields/self.config."""

    def plot_grid_result(self, sigmas=False, output_file=None):

        input_names = [stationID(stn) for stn in self.stations]
        input_lon = [stn.lon for stn in self.stations]
        input_lat = [stn.lat for stn in self.stations]

        sparse_names = [stationID(stn) for stn in self.sparse_stations]
        sparse_lon = np.array([stn.lon for stn in self.sparse_stations])
        sparse_lat = np.array([stn.lat for stn in self.sparse_stations])
        n_sparse = len(self.sparse_stations)

        available_fields, station_data, sparse_data, grid_lon, grid_lat, fields, fcovar = \
            [], [], [], [], [], [], []

        # Per-panel caption, appended alongside each entry below so it keys off the field
        # object rather than its label -- the coseismic and postseismic panels for one
        # event share an event id, and only the postseismic amplitudes are entangled. The
        # caption goes on the figure rather than in the field selector because the
        # selector is already the width of its longest event label.
        entangled = self.postseismic_entanglement() if not sigmas else {}
        field_notes = []

        postseismic = []
        for ifield in self.fields:
            if ifield.base_type == ConstraintType.POSTSEISMIC:
                if ifield.event.id in postseismic:
                    # field already in
                    continue

            parameters = np.zeros((3, len(self.stations)))
            station_data.append(parameters)
            sparse_params = np.full((3, n_sparse), np.nan)
            available_fields.append(ifield.description)
            hit = (entangled.get(ifield.event.id)
                   if ifield.base_type == ConstraintType.POSTSEISMIC else None)
            field_notes.append(
                f'not separately determined: r = {hit[0]:+.2f} with {hit[1]}  '
                f'-- read the Postseismic rate panel instead'
                if hit else None)
            lon, lat = ifield.get_interpolation_grid_geographic()
            grid_lon.append(lon)
            grid_lat.append(lat)
            if sigmas:
                # do not plot data from stations in uncertainty mode
                fields.append(ifield.enu_sigma)
                fcovar.append(ifield.enu_covar)
            else:
                if ifield.base_type == ConstraintType.POSTSEISMIC:
                    r_field = 0
                    for f in [ff for ff in self.fields if ff.base_type == ConstraintType.POSTSEISMIC]:
                        if ifield.event == f.event:
                            cols, keep = self._parameter_columns(f)
                            # pick the max relaxation and use it a dt
                            r_field += f.enu_field * np.log(1 + self.config.relaxation.max()/f.relaxation)
                            # assign values to where they belong
                            parameters[:, cols] += (f.constrained_parameters[:, keep] *
                                                    np.log(1 + self.config.relaxation.max()/f.relaxation))
                            # predicted value at each sparse station (summed across relaxations)
                            dt_weight = np.log(1 + self.config.relaxation.max()/f.relaxation)
                            for k, stn in enumerate(self.sparse_stations):
                                vals, _ = f.get_values_geo(stn.lon, stn.lat)
                                if vals is not None:
                                    if np.isnan(sparse_params[:, k]).all():
                                        sparse_params[:, k] = 0.0
                                    for j in range(3):
                                        sparse_params[j, k] += vals[j][0] * dt_weight
                    # append postseismic field to keep track of which earthquakes were processed already
                    postseismic.append(ifield.event.id)
                    fields.append(r_field)
                else:
                    cols, keep = self._parameter_columns(ifield)
                    # assign values to where they belong
                    parameters[:, cols] = ifield.constrained_parameters[:, keep]
                    fields.append(ifield.enu_field)

                    value_idx = 1 if ifield.base_type == ConstraintType.INTERSEISMIC else 0
                    for k, stn in enumerate(self.sparse_stations):
                        vals, _ = ifield.get_values_geo(stn.lon, stn.lat)
                        if vals is not None:
                            for j in range(3):
                                sparse_params[j, k] = vals[j][value_idx]

            sparse_data.append(sparse_params)

        # Two more panels: every event's postseismic contribution summed at one epoch,
        # as a rate and as a cumulative displacement. See postseismic_combined for why
        # these are the views to read rather than the per-event panels above. Both follow
        # the epoch slider, so they are recomputed through epoch_callback below.
        epoch_indices, epoch_limits, epoch_callback = [], None, None
        if not sigmas:
            components = self._postseismic_components(n_sparse)
            epoch_limits = self.postseismic_epoch_limits(components)
            if epoch_limits is not None:
                epoch0 = epoch_limits[1]
                for cumulative, name in ((False, 'Postseismic rate'),
                                         (True, 'Postseismic cumulative')):
                    f_, p_, s_, used = self.postseismic_combined(components, epoch0, cumulative)
                    if not used:
                        continue
                    epoch_indices.append(len(available_fields))
                    available_fields.append(name)
                    fields.append(f_)
                    station_data.append(p_)
                    sparse_data.append(s_)
                    grid_lon.append(self.grids.interpolation_geographic[0])
                    grid_lat.append(self.grids.interpolation_geographic[1])
                    fcovar.append(None)
                    field_notes.append(None)

                cumulative_by_index = {epoch_indices[i]: bool(i)
                                       for i in range(len(epoch_indices))}

                def epoch_callback(field_index, epoch, _c=components,
                                   _m=cumulative_by_index):
                    """Recompute one epoch-dependent panel; called by the epoch slider."""
                    f_, p_, s_, _u = self.postseismic_combined(_c, epoch, _m[field_index])
                    return f_, p_, s_

        # do the thing
        return plot_velocity_field(grid_lon, grid_lat, fields,
                                   np.array(input_lon), np.array(input_lat), station_data, input_names,
                                   self.plot_constrained_etm, available_fields, plot_sigmas=sigmas,
                                   covar=fcovar,
                                   sparse_lon=sparse_lon, sparse_lat=sparse_lat,
                                   sparse_data=sparse_data, sparse_names=sparse_names,
                                   on_sparse_click=self.plot_constrained_sparse_etm if n_sparse > 0 else None,
                                   field_notes=field_notes,
                                   epoch_callback=epoch_callback,
                                   epoch_limits=epoch_limits,
                                   epoch_indices=epoch_indices,
                                   output_file=output_file)

    def postseismic_entanglement(self, threshold: float = 0.5) -> dict:
        """event id -> (worst signed correlation, the other event's id), for postseismic
        amplitudes that are not separately determined.

        Two events close together in time have nearly collinear log(1 + dt/tau) columns
        over a given station's span, so their amplitudes trade off: the pair is well
        determined, each one alone is not. The correlation is read straight off the
        solved covariance, per station and component, and the median taken -- so this
        reports what the solution itself says about separability, not a guess from the
        event dates.

        Only pairs above `threshold` in absolute value are returned.
        """
        from .types import ConstraintType

        events = {f.event.id: f.event for f in self.fields
                  if f.base_type == ConstraintType.POSTSEISMIC}
        if len(events) < 2:
            return {}

        tp = self.solution.shape[1]
        cols = {}
        for event_id in events:
            per_station = {}
            for station in self.stations:
                for relax in self.config.relaxation:
                    col = station.get_postseismic_column(event_id, relax)
                    if col is not None:
                        per_station[(stationID(station), relax)] = int(np.atleast_1d(col)[0])
            cols[event_id] = per_station

        worst = {}
        ids = list(events)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                shared = set(cols[a]) & set(cols[b])
                rs = []
                for key in shared:
                    for comp in range(3):
                        ga = cols[a][key] + comp * tp
                        gb = cols[b][key] + comp * tp
                        denom = self.covariance[ga, ga] * self.covariance[gb, gb]
                        if denom > 0:
                            rs.append(self.covariance[ga, gb] / np.sqrt(denom))
                if not rs:
                    continue
                r = float(np.median(rs))
                if abs(r) < threshold:
                    continue
                for one, other in ((a, b), (b, a)):
                    if one not in worst or abs(r) > abs(worst[one][0]):
                        worst[one] = (r, other)

        return worst

    def postseismic_rate_epoch(self) -> Optional[float]:
        """Decimal year the postseismic rate panel is evaluated at: the last epoch any
        station in the stack actually observed.

        Not datetime.now(): the model is a fit to data that ends here, and evaluating the
        rate past that is extrapolation the panel should not quietly present as a result.
        """
        last = [float(stn.etm.solution_data.time_vector.max())
                for stn in self.stations
                if stn.etm is not None and stn.etm.solution_data.time_vector.size]
        return max(last) if last else None

    def _postseismic_components(self, n_sparse: int) -> list:
        """Each postseismic field reduced to the pieces an epoch combination needs.

        Precomputed once so moving the epoch slider is a weighted sum over these rather
        than a re-interpolation: the sparse-station values in particular come from
        get_values_geo, which runs a griddata interpolation per station per field.
        """
        from .types import ConstraintType

        out = []
        for f in self.fields:
            if f.base_type != ConstraintType.POSTSEISMIC:
                continue

            cols, keep = self._parameter_columns(f)
            sparse = np.full((3, n_sparse), np.nan)
            for k, stn in enumerate(self.sparse_stations):
                vals, _ = f.get_values_geo(stn.lon, stn.lat)
                if vals is None:
                    continue
                sparse[:, k] = [vals[j][0] for j in range(3)]

            out.append(dict(
                mask=self.grids.earthquake_masks[f.event.id][1],
                grid=f.enu_field,
                cols=cols, keep=keep,
                params=f.constrained_parameters,
                sparse=sparse,
                onset=f.event.date.fyear,
                relaxation=f.relaxation,
            ))
        return out

    def postseismic_combined(self, components: list, epoch: float, cumulative: bool):
        """Sum every event's postseismic contribution at one epoch.

        cumulative=False gives the rate, A / (tau + dt), i.e. d/dt of A log(1 + dt/tau),
        in the same units as the interseismic field. cumulative=True gives the
        displacement accumulated since each event's own onset, A log(1 + dt/tau).

        Either way the point is the summation. The per-event panels are not independently
        readable: amplitudes of events close in time are strongly anti-correlated -- on
        the Ecuador stack 2016-107 and 2016-139, 32 days apart, sit at -0.98 -- so each
        amplitude alone is a marginal quantity with a marginal uncertainty while only the
        combination is determined. At col.psto the two East amplitudes are -10.3 +/- 10.0
        and +1.6 +/- 9.4 mm, both consistent with zero, and their sum is 8.7 +/- 1.1 mm.
        Summing along the direction they trade off against each other is what makes the
        displayed quantity mean something.

        Returns (field, station_parameters, sparse_parameters, n_events_used).
        """
        total = np.zeros((3, self.grids.points))
        parameters = np.zeros((3, len(self.stations)))
        sparse_params = np.full((3, len(self.sparse_stations)), np.nan)
        used = 0

        for c in components:
            dt = epoch - c['onset']
            if dt <= 0:
                continue
            weight = (np.log(1.0 + dt / c['relaxation']) if cumulative
                      else 1.0 / (c['relaxation'] + dt))
            used += 1

            total[:, c['mask']] += c['grid'] * weight
            parameters[:, c['cols']] += c['params'][:, c['keep']] * weight

            live = ~np.isnan(c['sparse'][0])
            if live.any():
                blank = np.isnan(sparse_params[0]) & live
                sparse_params[:, blank] = 0.0
                sparse_params[:, live] += c['sparse'][:, live] * weight

        return total, parameters, sparse_params, used

    def postseismic_epoch_limits(self, components: list):
        """(first onset, last observed epoch) for the epoch slider, or None."""
        last = self.postseismic_rate_epoch()
        onsets = [c['onset'] for c in components]
        if last is None or not onsets or min(onsets) >= last:
            return None
        return min(onsets), last

    def _parameter_columns(self, field) -> Tuple[np.ndarray, np.ndarray]:
        """Map a field's own parameter columns onto columns of self.stations.

        Returns (cols, keep): field.constrained_parameters[:, keep] belongs at
        parameters[:, cols]. Needed because constrained_parameters is indexed by
        field.constrain_stations -- which is constraint.station_list, i.e. constraining
        stations first and then the ones being constrained -- while the plot's own array
        is indexed by self.stations. A boolean np.isin mask was used here, which fills in
        self.stations order and therefore silently pairs each station's parameters with a
        different station's coordinates whenever the two orders differ.
        """
        position = {stationID(stn): k for k, stn in enumerate(self.stations)}
        cols, keep = [], []
        for k, stn in enumerate(field.constrain_stations):
            column = position.get(stationID(stn))
            if column is not None:
                cols.append(column)
                keep.append(k)

        return np.array(cols, dtype=int), np.array(keep, dtype=int)

    def _run_constrained_etm(self, cnn: Cnn, stn, constraints, folder=None, catch_errors=False,
                             validity_date=None):
        """
        Shared by plot_constrained_etm and plot_constrained_sparse_etm: build an ETM for
        `stn` with `constraints` installed (the stacked solution, or for a sparse station
        the interpolated field), run it, and plot it.

        validity_date (fyear), when given, windows the fit to epochs from that date on --
        see constraints/sparse.overall_validity_date. Plotting the full span including
        pre-validity epochs is exactly how you'd confirm the model doesn't -- and isn't
        expected to -- explain a sparse station before the full-station network itself
        had enough data; this windows the diagnostic to the span it's actually meant to
        cover.
        """
        etm_config = etm_builder.apply_config(self.config, stn.network_code, stn.station_code, cnn)
        etm_config.solution.solution_type = SolutionType.PPP
        etm_config.modeling.least_squares_strategy.constraints = constraints
        # add the prefit models that got removed from the ETM when we did the stack
        etm_config.modeling.prefit_models = copy.deepcopy(stn.etm.config.modeling.prefit_models)
        if validity_date is not None:
            tqdm.write(f' -- Windowing constrained ETM for {stationID(stn)} to {validity_date:.3f} onward '
                       f'(model validity: fewer than {self.config.sparse_validity_min_stations} '
                       f'full stations with data before then)')
            etm_config.modeling.data_model_window = [[validity_date, 9999.]]

        for const in etm_config.modeling.least_squares_strategy.constraints:
            par = ''
            for p in const.p.params:
                par += '[' + ' '.join([f'{a * 1000.:.2f}' for a in p.tolist()]) + '] '

            tqdm.write(f' -- Etm constrain: {const} {par}')

        if folder is None:
            etm_config.plotting_config.interactive = True
        else:
            etm_config.plotting_config.filename = folder

        etm_config.plotting_config.plot_show_outliers = True
        etm_config.plotting_config.plot_residuals_mode = True
        # Always RobustLeastSquares here, regardless of config.gls_for_all_stations: the
        # whole point of this diagnostic is to see how well the station's own data
        # matches the externally-imposed (network) constraint. A GLS fit's stochastic
        # signal is a collocation prediction built from the residuals against that
        # constraint -- it can't tell a real misfit (the constraint disagreeing with the
        # data) apart from genuine correlated noise, so it smooths right over exactly
        # the disagreement this plot exists to show. RLS has no stochastic signal, so the
        # residuals plotted are a direct, untouched measure of that disagreement.
        etm_config.modeling.least_squares_strategy.adjustment_model = AdjustmentModels.ROBUST_LEAST_SQUARES
        etm = EtmEngine(etm_config, cnn=cnn, silent=True)

        # deactivate mechanical jumps
        etm_builder.deactivate_mechanical_jumps(etm)
        # ...and drop the geophysical jumps the stack has nothing to say about, which a
        # config-driven rebuild re-admits -- see sync_geophysical_jumps.
        etm_builder.sync_geophysical_jumps(etm, constraints)

        if catch_errors:
            try:
                etm.run_adjustment(cnn=cnn, try_save_to_db=False, try_loading_db=False)
                etm.plot()
            except Exception as e:
                tqdm.write(f'Could not fit constrained ETM for {stationID(stn)}: {e}')
        else:
            etm.run_adjustment(cnn=cnn, try_save_to_db=False, try_loading_db=False)
            etm.plot()

        print('(EtmStacker) > ', end='', flush=True)

    def plot_constrained_etm(self, station_index, folder=None):
        cnn = Cnn('gnss_data.cfg')
        stn = self.stations[station_index]
        tqdm.write(f'Estimating constrained etm for {stationID(stn)}')
        self._run_constrained_etm(cnn, stn, stn.etm_constraints, folder, catch_errors=False)

    def plot_constrained_sparse_etm(self, sparse_station_index, folder=None):
        cnn = Cnn('gnss_data.cfg')
        stn = self.sparse_stations[sparse_station_index]
        tqdm.write(f'Estimating constrained etm for sparse station {stationID(stn)}')
        validity_date = sparse_constraint.overall_validity_date(self)
        self._run_constrained_etm(cnn, stn, self._build_sparse_etm_constraints(stn), folder,
                                  catch_errors=True, validity_date=validity_date)

    @staticmethod
    def _sanitize_sigma(sigma):
        """Replace NaN/negative sigmas with 1e-4 (cap floor)."""
        if np.isnan(sigma) or sigma <= 0:
            return 1e-4
        return sigma

    def _build_sparse_etm_constraints(self, stn):
        col = [1, 0, 2]
        MULT = 100
        constraints = []

        # Velocity from the interseismic field
        for ifield in self.fields:
            if ifield.base_type == ConstraintType.INTERSEISMIC:
                vals, sigs = ifield.get_values_geo(stn.lon, stn.lat)
                if vals is not None:
                    poly = PolynomialFunction(stn.etm.config)
                    for j in range(3):
                        poly.p.params[j][1] = vals[col[j]][1]
                        poly.p.sigmas[j][1] = self._sanitize_sigma(sigs[col[j]][1] / MULT)
                    constraints.append(poly)
                break

        # Coseismic and postseismic from earthquake fields
        for event in self.earthquakes:
            jump = stn.etm.jump_manager.get_geophysical_jump(event.id)
            if not (jump and jump.fit):
                continue

            jc = JumpFunction(stn.etm.config, time_vector=np.array([0]),
                              date=jump.date, jump_type=jump.p.jump_type, fit=False)

            if jump.p.jump_type < JumpType.POSTSEISMIC_ONLY:
                for ifield in self.fields:
                    if (ifield.base_type == ConstraintType.COSEISMIC and
                            ifield.event.id == event.id):
                        x, y = ifield.grids.transform_coordinate(stn.lon, stn.lat)
                        vals, sigs = ifield.get_values_proj(x, y)
                        if vals is not None:
                            for j in range(3):
                                jc.p.params[j][0] = vals[col[j]][0]
                                jc.p.sigmas[j][0] = self._sanitize_sigma(sigs[col[j]][0] / MULT)
                        break

            for relax in self.config.relaxation:
                idj = jump.get_relaxation_cols(relax, False)
                if not idj:
                    continue
                for ifield in self.fields:
                    if (ifield.base_type == ConstraintType.POSTSEISMIC and
                            ifield.event.id == event.id and
                            ifield.relaxation == relax):
                        x, y = ifield.grids.transform_coordinate(stn.lon, stn.lat)
                        vals, sigs = ifield.get_values_proj(x, y)
                        if vals is not None:
                            for j in range(3):
                                jc.p.params[j][idj] = vals[col[j]][0]
                                jc.p.sigmas[j][idj] = self._sanitize_sigma(sigs[col[j]][0] / MULT)
                        break

            # tie any unconstrained parameters to zero
            for j in range(3):
                nan_mask = np.isnan(jc.p.params[j])
                if np.any(nan_mask):
                    jc.p.params[j][nan_mask] = 0.0
                    jc.p.sigmas[j][nan_mask] = 1e-4

            constraints.append(jc)

        return constraints
