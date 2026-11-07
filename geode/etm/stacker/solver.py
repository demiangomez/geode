"""
The linear solve itself: assembling constraints onto the base normal equations,
solving, variance-of-unit-weight bookkeeping, and extracting/reporting results.
"""

from operator import itemgetter
from typing import List, Tuple

import numpy as np
import numpy.linalg.linalg
from tqdm import tqdm

from .data_classes import EtmStackerField
from .types import ConstraintType
from ...Utils import stationID


class SolverMixin:
    """Mixed into EtmStacker: owns self.solution / self.covariance / self.variance / self.fields."""

    def solve(self, interpolate_fields=True) -> Tuple[List, List]:
        """
        Solve the stacking system.
        """
        # rebuild normal equations before solving
        system_neq, system_ceq = self._build_base_normal_equations()

        # collect constraints. Weight changes take effect here
        self.constraint_registry.collect_all_constraints(
            self.stations, self.total_parameters, self.grids,
            earthquakes=self.earthquakes
        )

        # Solve: Apply constraints to system and do not modify original NEQs
        self.total_constraints = self.constraint_registry.add_all_constraints(
            system_neq, self.total_parameters
        )

        tqdm.write('Solving system...')

        x = np.linalg.solve(system_neq, system_ceq)
        self.solution = np.reshape(x, (3, self.total_parameters))
        # compute covariance for the entire system
        self.covariance = np.linalg.inv(system_neq)

        # compute the variance of unit weight
        lpl = sum(stn.normal_equations.weighted_observations[0] +
                  stn.normal_equations.weighted_observations[1] +
                  stn.normal_equations.weighted_observations[2] for stn in self.stations)

        dof = self.total_equations * 3 + self.total_constraints - (self.total_parameters * 3)

        # see Kyle Snow eq 6.36 and 6.37a (y^T P y − c^T N^−1 c): since the constraint
        # rows are already folded into system_neq/system_ceq before solving, this is
        # the TOTAL weighted residual sum (observations + constraints combined), not
        # the observation-only part.
        total_vpv = lpl - system_ceq.T @ x
        # isolate the constraint-only contribution for the diagnostic breakdown below;
        # subtract (not add) it from the total to get the observation-only residual sum
        c_vpv = self._sum_constraint_weighted_residuals()
        o_vpv = total_vpv - c_vpv

        self.variance = total_vpv / dof
        # update the covariance
        self.covariance *= self.variance
        # compute variance of unit weight for each stations
        increment = []
        for stn in self.stations:
            # add constraints to each station
            stn.extract_etm_constraints(self.earthquakes, self.config.relaxation,
                                        self.solution, self.covariance)
            # access normal equations
            neq = stn.normal_equations
            stn.posterior_wrms = []
            wrms_increment = []
            for i in range(3):
                # compute residuals for station
                x = self.solution[i, stn.normal_equations.parameter_range]
                v = neq.observation_vector[i] - neq.design_matrix @ x
                wrms_increment.append(np.sqrt(
                    v.T @ np.diag(neq.observation_weights[i]) @ v / stn.normal_equations.dof)
                )
                stn.posterior_wrms.append(
                    neq.prior_wrms[i] * wrms_increment[i]
                )
            wrms_increment = np.array(wrms_increment)

            increment.append([stationID(stn), np.mean(wrms_increment), wrms_increment])

        tqdm.write('WRMS increment for each station:')
        for stn, wrmsi, wrms in increment:
            tqdm.write(f'{stn} WRMS increment: (total={wrmsi:.2f}) {wrms[0]:.2f} {wrms[1]:.2f} {wrms[2]:.2f}')

        tqdm.write('First five largest WRMS increments:')
        c = 0
        for stn, wrmsi, wrms in sorted(increment, key=itemgetter(1), reverse=True):
            if c == 6:
                break
            tqdm.write(f'{stn} WRMS increment: (total={wrmsi:.2f}) {wrms[0]:.2f} {wrms[1]:.2f} {wrms[2]:.2f}')
            c += 1

        tqdm.write(f'Equations: {self.total_equations * 3}')
        tqdm.write(f'Constraints: {self.total_constraints}')
        tqdm.write(f'Parameters: {self.total_parameters * 3}')
        tqdm.write(f'Sum of squared residuals (obs): {o_vpv:.3f}')
        tqdm.write(f'Sum of squared residuals (con): {c_vpv:.3f}')
        tqdm.write(f'Model redundancy: {dof}')
        tqdm.write(f'SQRT(var) for the stacked system: {np.sqrt(self.variance):.3f} ')
        tqdm.write(f'1/var for the stacked system: {1/self.variance:.4f} ')

        self.solved = True

        if interpolate_fields:
            self.interpolate_fields_to_grid()

        # Extract results
        return self._extract_results()

    def _sum_constraint_weighted_residuals(self):

        # Count active equations first
        n_active = self.total_constraints
        # Pre-allocate
        k = np.zeros((n_active, self.total_parameters * 3))
        idx = 0

        for constraint_type in self.constraint_registry.constraints.keys():
            for const in self.constraint_registry.constraints[constraint_type]:
                for eq in [e for e in const.equations if e.is_active]:
                    # Build K matrix for this constraint
                    ke, kn, ku = eq.constraint_design
                    se, sn, su = eq.constraint_sigma
                    # do not square! will get squared when doing v.T @ v
                    k[idx:idx + 3, :] = np.vstack((ke * (1 / se), kn * (1 / sn), ku * (1 / su)))
                    idx += 3
        # the - comes from z0 − K ξ̂ but in this case, z0 = 0 (see Snow 6.38)
        v = -k @ self.solution.flatten()

        return v.T @ v

    def constraints_rms(self):
        """
        Take the registered constraints and find their rms values.
        """
        wrms = []
        for constraint_type in self.constraint_registry.constraints.keys():
            for const in self.constraint_registry.constraints[constraint_type]:
                v = np.array([])
                v_eq = []
                for eq in [e for e in const.equations if e.is_active]:
                    # get design and weight matrix
                    ke, kn, ku = eq.constraint_design
                    # do not square! will get squared when doing v.T @ v
                    r = np.vstack((ke, kn, ku)) @ self.solution.flatten()
                    v_eq.append([stationID(eq.station), r, np.sqrt((r.T @ r) / 2)])
                    v = np.concatenate((v, r))

                n = len(const.equations)
                # compute the wrms residuals
                if n > 0:
                    wrms.append([const, n, np.sqrt((v.T @ v) / (n - 1)),
                                 sorted(v_eq, key=itemgetter(2), reverse=True)])

        return sorted(wrms, key=itemgetter(2), reverse=True)

    def _extract_results(self) -> Tuple[List, List]:

        interseismic = []
        earthquakes = []
        for stn in self.stations:
            ap = stn.etm.design_matrix.get_polynomial().p.params
            idx = stn.get_velocity_column()
            interseismic.append({
                'station': stationID(stn),
                'lon': stn.lon,
                'lat': stn.lat,
                'a_priori': [ap[1][1], ap[0][1], ap[2][1]],
                'constrained': self.solution[:, idx].tolist(),
                'is_interseismic': stn.is_interseismic
            })
            for event in self.earthquakes:
                idx = stn.get_coseismic_column(event.id)
                if idx:
                    ap = stn.etm.jump_manager.get_geophysical_jump(event.id).p.params
                    earthquakes.append({
                        'station': stationID(stn),
                        'lon': stn.lon,
                        'lat': stn.lat,
                        'event_id': event.id,
                        'relax': 0.0,
                        'a_priori': [ap[1], ap[0], ap[2]],
                        'constrained': self.solution[:, idx].tolist(),
                    })
                for relax in self.config.relaxation:
                    idx = stn.get_postseismic_column(event.id, relax)
                    if idx is not None:
                        jump = stn.etm.jump_manager.get_geophysical_jump(event.id)
                        ap = jump.p.params
                        col = jump.get_relaxation_cols(relax, False)
                        earthquakes.append({
                            'station': stationID(stn),
                            'lon': stn.lon,
                            'lat': stn.lat,
                            'event_id': event.id,
                            'relax': relax,
                            'a_priori': [ap[1][col], ap[0][col], ap[2][col]],
                            'constrained': self.solution[:, idx].tolist(),
                        })

        return interseismic, earthquakes

    def interpolate_fields_to_grid(self):

        if self.solved:
            # clean any previous runs
            self.fields = []

            # iterate through the constraints
            constraint_types = self.constraint_registry.constraints.values()
            for constraint_type in constraint_types:
                for constraint in constraint_type:
                    if constraint.is_collision:
                        tqdm.write(f'Skipping field interpolation for collided event {constraint.event.id} '
                                   f'(parameters are zero-tied)')
                        continue

                    if (constraint.constraint_type != ConstraintType.INTERSEISMIC and
                            getattr(constraint, 'dislocation_model', None) is None):
                        tqdm.write(f'Skipping field interpolation for {constraint.short_description()} '
                                   f'(zero-constrained: no SW-Okada model was built)')
                        continue

                    try:
                        self.fields.append(
                            EtmStackerField.create_field(
                                self.stations, self.solution, self.covariance, self.grids, constraint)
                        )
                    except numpy.linalg.linalg.LinAlgError:
                        tqdm.write(f'Skipping field interpolation for {constraint.short_description()} '
                                   f'(singular matrix: not enough independent stations)')
                        continue

        else:
            tqdm.write('System has not been solved! Invoke solve first')
