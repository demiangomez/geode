"""
Interactive diagnostics for the stacker: the map/grid plot, and re-running a single
station's ETM with the stacked solution (or, for a sparse station, the interpolated
field) folded in as a constraint, for visual inspection.
"""

import copy

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
                            idx = np.isin(np.array([stationID(stn) for stn in self.stations]),
                                          np.array([stationID(stn) for stn in f.constrain_stations]))
                            # pick the max relaxation and use it a dt
                            r_field += f.enu_field * np.log10(1 + self.config.relaxation.max()/f.relaxation)
                            # assign values to where they belong
                            parameters[:, idx] += (f.constrained_parameters *
                                                   np.log10(1 + self.config.relaxation.max()/f.relaxation))
                            # predicted value at each sparse station (summed across relaxations)
                            dt_weight = np.log10(1 + self.config.relaxation.max()/f.relaxation)
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
                    idx = np.isin(np.array([stationID(stn) for stn in self.stations]),
                                  np.array([stationID(stn) for stn in ifield.constrain_stations]))
                    # assign values to where they belong
                    parameters[:, idx] = ifield.constrained_parameters
                    fields.append(ifield.enu_field)

                    value_idx = 1 if ifield.base_type == ConstraintType.INTERSEISMIC else 0
                    for k, stn in enumerate(self.sparse_stations):
                        vals, _ = ifield.get_values_geo(stn.lon, stn.lat)
                        if vals is not None:
                            for j in range(3):
                                sparse_params[j, k] = vals[j][value_idx]

            sparse_data.append(sparse_params)

        # do the thing
        return plot_velocity_field(grid_lon, grid_lat, fields,
                                   np.array(input_lon), np.array(input_lat), station_data, input_names,
                                   self.plot_constrained_etm, available_fields, plot_sigmas=sigmas,
                                   covar=fcovar,
                                   sparse_lon=sparse_lon, sparse_lat=sparse_lat,
                                   sparse_data=sparse_data, sparse_names=sparse_names,
                                   on_sparse_click=self.plot_constrained_sparse_etm if n_sparse > 0 else None,
                                   output_file=output_file)

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
