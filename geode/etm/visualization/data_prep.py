"""
Project: Geodetic Database Engine (GeoDE)
Date: 9/22/25 8:53 AM
Author: Demian D. Gomez

Data preparation utilities for ETM plotting.
"""

from typing import List, Optional, Tuple, NamedTuple
import numpy as np
import logging

logger = logging.getLogger(__name__)

from ..core.etm_config import EtmConfig
from ..core.type_declarations import FitStatus
from ..data.solution_data import SolutionData
from ..least_squares.least_squares import EtmFit
from ..visualization.data_classes import (TimeSeriesPlotData, ComponentData, PlotOutputConfig,
                                          NoiseModelPlotData, EllipsePlotData)

# Re-export PlotTemplate for backward compatibility
from ..visualization.plot_template import PlotTemplate

# Conversion factor from meters to millimeters
M_TO_MM = 1000.


class JumpTableData(NamedTuple):
    """Container for jump table data used in plotting"""
    north: list
    east: list
    up: list
    functions: list  # Jump function references for each table entry


class PlotDataPreparer:
    """Prepares ETM results for plotting by transforming coordinates and organizing data"""

    def __init__(self, config: EtmConfig):
        self.config = config
        self.output_config = config.plotting_config

    def prepare_time_series_data(self, solution_data: SolutionData,
                                 etm_fit: EtmFit) -> TimeSeriesPlotData:
        """Prepare data for time series plotting"""
        # Transform coordinates to local frame
        local_coords = solution_data.transform_to_local(ignore_data_window=True)

        # Prepare component data for N, E, U
        component_data = self._prepare_all_components(local_coords, solution_data, etm_fit)
        jump_tables = self._prepare_jump_tables(etm_fit)

        # Create plot data container
        plot_data = TimeSeriesPlotData(
            station_id=solution_data.get_station_id().upper(),
            solution_type=solution_data.soln,
            stack_name=solution_data.stack_name,
            completion=solution_data.completion,
            span_completion=solution_data.span_completion,
            latitude=solution_data.lat[0],
            longitude=solution_data.lon[0],
            north_data=component_data[0],
            east_data=component_data[1],
            up_data=component_data[2],
            covariance_matrix=etm_fit.covar,
            has_etm_results=self.config.modeling.status == FitStatus.POSTFIT,
            jump_tables=(jump_tables.north, jump_tables.east, jump_tables.up),
            jumps=jump_tables.functions,
            missing_solutions=solution_data.time_vector_ns
        )

        # Add ETM-specific summaries
        if etm_fit:
            plot_data.parameter_summary = self._generate_parameter_summary(etm_fit, solution_data)
            plot_data.periodic_summary = self._generate_periodic_summary(etm_fit)
            plot_data.wrms_values = (
                etm_fit.results[0].wrms * M_TO_MM,
                etm_fit.results[1].wrms * M_TO_MM,
                etm_fit.results[2].wrms * M_TO_MM
            )

        return plot_data

    @staticmethod
    def prepare_noise_model_data(solution_data: SolutionData, etm_fit: EtmFit) -> NoiseModelPlotData:
        """Prepare data for the noise model diagnostic plot"""
        return NoiseModelPlotData(
            station_id=solution_data.get_station_id().upper(),
            solution_type=solution_data.soln,
            stack_name=solution_data.stack_name,
            completion=solution_data.completion,
            span_completion=solution_data.span_completion,
            latitude=solution_data.lat[0],
            longitude=solution_data.lon[0],
            noise_models=[result.noise_model for result in etm_fit.results],
            cross_covariance=etm_fit.cross_covariance
        )

    @staticmethod
    def prepare_ellipse_data(solution_data: SolutionData, etm_fit: EtmFit) -> Optional[EllipsePlotData]:
        """
        Prepare data for the velocity error-ellipse plot: the full rate covariance block of each pair,
        from the polynomial function's own within-component (.covar) and cross-component
        (.cross_covar_borrowed/.cross_covar_power_law) blocks, local index 1 = velocity (polynomial
        columns are always [offset, velocity, acceleration], see etm_functions/polynomial.py). Returns
        None if there is no fitted polynomial rate term to draw (EtmEngine.plot_ellipses then warns).
        """
        poly = etm_fit.design_matrix.get_polynomial()
        if (poly is None or not poly.fit or poly.param_count <= 1 or
                any(c.size == 0 for c in poly.p.covar)):
            # no rate term, or the covariance wasn't (re)computed (e.g. a DB-cached fit loaded without
            # force_computation -- see EtmEngine.plot_ellipses)
            return None

        labels = 'NEU'
        pairs = {}
        for i, j in ((0, 1), (0, 2), (1, 2)):
            pair = f'{labels[i]}{labels[j]}'
            var_i, var_j = poly.p.covar[i][1, 1], poly.p.covar[j][1, 1]

            def block(cov_ij):
                return np.array([[var_i, cov_ij], [cov_ij, var_j]]) * M_TO_MM ** 2

            borrowed = pair in poly.p.cross_covar_borrowed
            power_law = pair in poly.p.cross_covar_power_law
            pairs[pair] = {
                'borrowed': block(poly.p.cross_covar_borrowed[pair]['matrix'][1][1]) if borrowed else None,
                'power_law': block(poly.p.cross_covar_power_law[pair]['matrix'][1][1]) if power_law else None
            }

        return EllipsePlotData(
            station_id=solution_data.get_station_id().upper(),
            solution_type=solution_data.soln,
            stack_name=solution_data.stack_name,
            completion=solution_data.completion,
            span_completion=solution_data.span_completion,
            latitude=solution_data.lat[0],
            longitude=solution_data.lon[0],
            pairs=pairs
        )

    @staticmethod
    def _prepare_jump_tables(etm_results: EtmFit) -> JumpTableData:
        """Extract jump parameter tables from ETM results for plot annotations"""
        tables = JumpTableData(north=[], east=[], up=[], functions=[])

        for funct in etm_results.design_matrix.functions:
            if funct.p.object == 'jump':
                param_tables = funct.print_parameters()
                # Add function reference for each table row (handles multi-line jumps like co+postseismic)
                for _ in range(len(param_tables[0])):
                    tables.functions.append(funct)
                # Extend component tables
                tables.north.extend(param_tables[0])
                tables.east.extend(param_tables[1])
                tables.up.extend(param_tables[2])

        return tables

    def _prepare_all_components(self, observations: List, solution_data: SolutionData,
                                etm_results: EtmFit) -> List[ComponentData]:
        """Prepare plot data for all three coordinate components (N, E, U)"""
        mask = etm_results.config.modeling.get_observation_mask(solution_data.time_vector)
        is_postfit = etm_results.config.modeling.status == FitStatus.POSTFIT

        component_data = []
        for i in range(3):
            data = self._create_base_component_data(observations[i], solution_data, mask)

            if is_postfit:
                self._add_model_and_residuals(data, i, solution_data, etm_results)
                self._apply_signal_removals(data, i, solution_data, etm_results)
                self._add_confidence_bounds(data, i, etm_results)
                data.outlier_flags = etm_results.outlier_flags

            component_data.append(data)

        return component_data

    @staticmethod
    def _create_base_component_data(obs: np.ndarray, solution_data: SolutionData,
                                    mask: np.ndarray) -> ComponentData:
        """Create ComponentData with basic observation data"""
        return ComponentData(
            observations=obs * M_TO_MM,
            observations_fit=obs[mask] * M_TO_MM,
            observations_not_fit=obs[~mask] * M_TO_MM,
            time_vector=solution_data.time_vector,
            time_vector_fit=solution_data.time_vector[mask],
            time_vector_not_fit=solution_data.time_vector[~mask],
            time_range=(solution_data.time_vector.min(), solution_data.time_vector.max()),
            mask=mask
        )

    @staticmethod
    def _add_model_and_residuals(data: ComponentData, component_idx: int,
                                 solution_data: SolutionData, etm_results: EtmFit) -> None:
        """
        Add model values and residuals to component data. model_values is always the pure
        deterministic model (polynomial + periodic + jumps) -- it never includes the
        collocation-predicted stochastic signal, so with no -rm flag at all, nothing is added to
        or removed from either the model or the data (see _handle_stochastic_signal). residuals is
        computed directly as observations minus this model, so it is exact by construction (for
        LSQ_COLLOCATION/NOISE_COVARIANCE_GLS, this is the full stochastic realization: noise +
        signal together) and stays consistent under any combination of removals applied afterward.
        """
        result = etm_results.results[component_idx]
        design = etm_results.design_matrix

        model_values = design.alternate_time_vector(solution_data.time_vector_cont) @ result.parameters

        data.model_time_vector = solution_data.time_vector_cont
        data.model_values = model_values * M_TO_MM

        model_fit = design.alternate_time_vector(solution_data.time_vector[data.mask]) @ result.parameters
        data.residuals = data.observations_fit - model_fit * M_TO_MM

        if np.any(~data.mask):
            # create a residual vector for the data not fit
            model_not_fit = (design.alternate_time_vector(solution_data.time_vector[~data.mask])
                             @ result.parameters) * M_TO_MM
            data.residuals_not_fit = data.observations_not_fit - model_not_fit

    def _apply_signal_removals(self, data: ComponentData, component_idx: int,
                               solution_data: SolutionData, etm_results: EtmFit) -> None:
        """Remove requested signal components from observations and model"""
        cfg = self.output_config

        for funct in etm_results.design_matrix.functions:
            obj_type = funct.p.object

            # Remove periodic, polynomial, or jump signals if requested
            if self._should_remove_signal(obj_type, cfg) and funct.fit:
                self._subtract_signal_from_data(data, component_idx, funct,
                                                solution_data.time_vector_cont)

            # Handle stochastic signal specially
            if obj_type == 'stochastic':
                self._handle_stochastic_signal(data, component_idx, funct, solution_data)

    @staticmethod
    def _should_remove_signal(obj_type: str, cfg: PlotOutputConfig) -> bool:
        """Check if a signal type should be removed based on config"""
        return ((obj_type == 'periodic' and cfg.plot_remove_periodic) or
                (obj_type == 'polynomial' and cfg.plot_remove_polynomial) or
                (obj_type == 'jump' and cfg.plot_remove_jumps))

    @staticmethod
    def _subtract_signal_from_data(data: ComponentData, component_idx: int,
                                   funct, time_vector_cont: np.ndarray) -> None:
        """Subtract a signal component from all relevant data arrays"""
        data.observations -= funct.eval(component_idx, data.time_vector) * M_TO_MM
        data.observations_fit -= funct.eval(component_idx, data.time_vector_fit) * M_TO_MM
        if np.any(~data.mask):
            data.observations_not_fit -= funct.eval(component_idx, data.time_vector_not_fit) * M_TO_MM
        data.model_values -= funct.eval(component_idx, time_vector_cont) * M_TO_MM

    def _handle_stochastic_signal(self, data: ComponentData, component_idx: int,
                                  funct, solution_data: SolutionData) -> None:
        """Independently remove the stochastic signal and/or noise from the observations and
        residuals. Neither is ever part of model_values (always the pure deterministic model, see
        _add_model_and_residuals) -- with both flags false (the default), this is a no-op: nothing
        is added to or removed from the data or the model. data.residuals already equals noise +
        signal by direct construction, so subtracting either term here keeps
        observations - model_values == residuals exactly, for any combination of the two flags."""
        cfg = self.output_config
        time_mjd = solution_data.time_vector_mjd

        if cfg.plot_remove_stochastic_signal:
            signal_fit = funct.eval(component_idx, time_mjd[data.mask]) * M_TO_MM
            data.observations -= funct.eval(component_idx, time_mjd) * M_TO_MM
            data.observations_fit -= signal_fit
            if np.any(~data.mask):
                data.observations_not_fit -= funct.eval(component_idx, time_mjd[~data.mask]) * M_TO_MM
            data.residuals -= signal_fit

        if cfg.plot_remove_stochastic_noise:
            noise_fit = funct.eval_noise(component_idx, time_mjd[data.mask]) * M_TO_MM
            data.observations -= funct.eval_noise(component_idx, time_mjd) * M_TO_MM
            data.observations_fit -= noise_fit
            if np.any(~data.mask):
                data.observations_not_fit -= funct.eval_noise(component_idx, time_mjd[~data.mask]) * M_TO_MM
            data.residuals -= noise_fit

    def _add_confidence_bounds(self, data: ComponentData, component_idx: int,
                               etm_results: EtmFit) -> None:
        """Add confidence bounds based on WRMS and sigma filter limit"""
        limit = self.config.modeling.least_squares_strategy.sigma_filter_limit
        sigma = etm_results.results[component_idx].wrms * M_TO_MM
        data.confidence_bounds = (
            data.model_values - limit * sigma,
            data.model_values + limit * sigma
        )

    @staticmethod
    def _generate_parameter_summary(etm_results: EtmFit, solution_data: SolutionData) -> str:
        """Generate polynomial parameter summary string for plot title"""
        output = ['']

        for funct in etm_results.design_matrix.functions:
            if funct.p.object == 'polynomial' and funct.fit:
                # Retrieve conventional epoch position
                ce_position = solution_data.transform_to_ecef(
                    etm_results.get_time_continuous_model(np.array([funct.p.t_ref])))
                output, _, _ = funct.print_parameters(ce_position=ce_position)

        return output[0]

    @staticmethod
    def _generate_periodic_summary(etm_results: EtmFit) -> str:
        """Generate periodic parameter summary string for plot title"""
        output = ['']

        for funct in etm_results.design_matrix.functions:
            if funct.p.object == 'periodic':
                output, _, _ = funct.print_parameters()

        return output[0]
