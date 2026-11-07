"""
Per-station normal equations and their assembly into the stacker's system.
"""

from typing import Tuple

import numpy as np
from tqdm import tqdm

from .data_classes import NormalEquations, Station
from ...Utils import stationID


class NormalEquationsMixin:
    """Mixed into EtmStacker: owns self.normal_equations / self.total_parameters."""

    def _create_normal_equations(self, station: Station) -> NormalEquations:
        """Analyze and save the relevant information."""

        # get the observations without the jumps
        l = station.etm.solution_data.transform_to_local()
        a = station.etm.design_matrix.matrix

        n = []
        c = []

        if station.etm.solution_data.solutions < 100:
            tqdm.write(f' -- Upweighting {stationID(station)} because observations count is < 100')
            weight_scale = self.config.station_weight_scale * 100.
        else:
            weight_scale = self.config.station_weight_scale

        lpl = []
        observation_weights = []
        prior_wrms = []
        # rearrange the NEU to ENU
        for i in [1, 0, 2]:
            p = np.diag(1 / station.etm.fit.results[i].obs_sigmas ** 2) * weight_scale
            n.append(a.T @ p @ a)
            c.append(a.T @ p @ l[i])
            lpl.append(l[i].T @ p @ l[i])
            observation_weights.append(1 / station.etm.fit.results[i].obs_sigmas ** 2)
            prior_wrms.append(station.etm.fit.results[i].wrms)

        neq = NormalEquations(
            station=stationID(station),
            neq=n, ceq=c,
            design_matrix=a,
            observation_vector=[l[1], l[0], l[2]],
            weighted_observations=lpl,
            observation_weights=observation_weights,
            weight_scale=weight_scale, dof=a.shape[0] - a.shape[1],
            parameter_count=a.shape[1],
            equation_count=a.shape[0],
            parameter_start_idx=self.total_parameters,
            parameter_range=np.arange(a.shape[1]) + self.total_parameters,
            prior_wrms=prior_wrms
        )
        # save both vectors to the station
        station.normal_equations = neq

        self.total_parameters += a.shape[1]
        self.total_equations += a.shape[0]

        return neq

    def _build_base_normal_equations(self) -> Tuple[np.ndarray, np.ndarray]:
        """Build the base NEQ from individual stations."""

        tqdm.write('Building station system of normal equations')

        tp = self.total_parameters

        system_neq = np.zeros((tp * 3, tp * 3))
        system_ceq = np.zeros((tp * 3,))

        offset = 0
        for neq in self.normal_equations:
            n_params = neq.parameter_count

            for i in range(3):
                neq_comp = neq.neq[i]
                ceq_comp = neq.ceq[i]

                system_neq[
                    i * tp + offset:i * tp + offset + n_params,
                    i * tp + offset:i * tp + offset + n_params] = neq_comp

                system_ceq[
                    i * tp + offset:i * tp + offset + n_params] = ceq_comp

            offset += n_params

        self.solved = False

        return system_neq, system_ceq

    def change_station_weight(self, station_id: str, new_weight: float, silent=False):

        found = False
        for neq in self.normal_equations:
            if neq.station == station_id:
                if not silent:
                    tqdm.write(f'Found {station_id} with weight {neq.weight_scale}, '
                               f'updating to {new_weight}')
                for i in range(3):
                    neq.ceq[i] = neq.ceq[i] / neq.weight_scale * new_weight
                    neq.neq[i] = neq.neq[i] / neq.weight_scale * new_weight
                    neq.weighted_observations[i] = neq.weighted_observations[i] / neq.weight_scale * new_weight

                neq.weight_scale = new_weight

                found = True

        if found and not silent:
            tqdm.write('Do not forget to invoke solve again!')
