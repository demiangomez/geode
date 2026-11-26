"""
The linear solve itself: assembling constraints onto the base normal equations,
solving, variance-of-unit-weight bookkeeping, and extracting/reporting results.
"""

import logging
from operator import itemgetter
from typing import List, Tuple

import numpy as np
from tqdm import tqdm

from . import console
from .constraints import adaptive_weight
from .constraints import separability
from .constraints import sparse as sparse_constraint
from .data_classes import EtmStackerField
from .exceptions import EtmStackerException
from .types import ConstraintType
from ...Utils import stationID

logger = logging.getLogger(__name__)


class SolverMixin:
    """Mixed into EtmStacker: owns self.solution / self.covariance / self.variance / self.fields."""

    def solve(self, interpolate_fields=True) -> Tuple[List, List]:
        """
        Solve the stacking system.
        """
        if getattr(self, 'system_stale', False):
            raise EtmStackerException(
                'the station set changed since build_system last ran, so the grids and '
                'constraints no longer match it; call build_system(cnn) again before '
                'solving (the station ETMs are kept, so this is quick)')

        console.phase('solve')

        # rebuild normal equations before solving
        system_neq, system_ceq = self._build_base_normal_equations()

        # Decide which stations may define the interseismic field before collecting
        # the constraint that depends on that decision. The probe below assembles the
        # system with only the co/postseismic ties -- what the network knows about each
        # station's decay amplitudes -- and asks how much of each velocity's
        # uncertainty is still owed to not knowing them. See constraints/separability.py.
        #
        # Deliberately excludes the interseismic constraint itself: it is what is being
        # decided, and including it would let a station's eligibility rest partly on
        # the field it is about to help define.
        if self.config.interseismic_max_inflation:
            self.constraint_registry.collect_all_constraints(
                self.stations, self.total_parameters, self.grids,
                earthquakes=self.earthquakes, skip=('interseismic',), quiet=True
            )
            probe = system_neq.copy()
            self.constraint_registry.add_all_constraints(
                probe, self.total_parameters, skip=('interseismic',))
            separability.apply_interseismic_eligibility(self, probe)
            del probe

        # collect constraints. Weight changes take effect here
        self.constraint_registry.collect_all_constraints(
            self.stations, self.total_parameters, self.grids,
            earthquakes=self.earthquakes
        )

        # Tighten (never loosen) a flagged full station's own velocity/postseismic-decay
        # mutual-constraint weight when its own diagonal implies far more confidence
        # than that fixed, global weight -- see constraints/adaptive_weight.py. No-op
        # when EtmStackerConfig.adaptive_constraint_weighting_enabled is False. Must run
        # after collect_all_constraints (equations must exist) and before
        # add_all_constraints (so the tightened sigma is what's actually applied).
        adaptive_weight.apply_adaptive_weighting(self)

        # Sparse stations' own contribution (if enabled): folded in here, alongside the
        # base per-station normal equations, before the smoothing constraints below --
        # see sparse_constraint.py. No-op (returns (0, 0)) when sparse_constraints_enabled
        # is False. sparse_lpl/sparse_equations must feed into lpl/dof below, same as
        # neq_contrib/ceq_contrib feed into system_neq/system_ceq here.
        sparse_lpl, sparse_equations = sparse_constraint.add_sparse_contributions(
            self, system_neq, system_ceq
        )

        # Solve: Apply constraints to system and do not modify original NEQs
        self.total_constraints = self.constraint_registry.add_all_constraints(
            system_neq, self.total_parameters
        )

        x = np.linalg.solve(system_neq, system_ceq)
        self.solution = np.reshape(x, (3, self.total_parameters))
        # compute covariance for the entire system
        self.covariance = np.linalg.inv(system_neq)

        # compute the variance of unit weight
        lpl = sum(stn.normal_equations.weighted_observations[0] +
                  stn.normal_equations.weighted_observations[1] +
                  stn.normal_equations.weighted_observations[2] for stn in self.stations)
        lpl += sparse_lpl

        dof = (self.total_equations * 3 + sparse_equations +
               self.total_constraints - (self.total_parameters * 3))

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
        # Kept on the object, not just printed: a weighting or regularization sweep needs
        # the observation and constraint halves separately to tell "the model now fits the
        # data worse" from "the constraints are being asked to do more".
        self.obs_vpv = o_vpv
        self.constraints_vpv = c_vpv
        self.dof = dof
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

        # The full per-station list goes to the log; only the tail of the distribution
        # is worth reading on screen, and that is what you look at anyway.
        for stn, wrmsi, wrms in increment:
            logger.info(f'{stn} WRMS increment: (total={wrmsi:.2f}) '
                        f'{wrms[0]:.2f} {wrms[1]:.2f} {wrms[2]:.2f}')

        console.step('full-station WRMS increment (10 largest; 1.0 = unchanged by stacking)')
        worst = sorted(increment, key=itemgetter(1), reverse=True)[:10]
        console.table(['station', 'total', 'E', 'N', 'U'],
                      [[stn, f'{m:.2f}', f'{w[0]:.2f}', f'{w[1]:.2f}', f'{w[2]:.2f}']
                       for stn, m, w in worst])
        all_inc = np.array([m for _, m, _ in increment])
        console.info(f'median {np.median(all_inc):.2f}   '
                     f'90th pct {np.percentile(all_inc, 90):.2f}   '
                     f'max {all_inc.max():.2f}   ({len(increment)} stations, full list in the log)')

        # Sparse stations never appear in the table above: it reads each station's own
        # normal equations, and a sparse station owns none. This is their counterpart.
        sparse_constraint.report_sparse_misfit(self)

        console.step('solution')
        console.result('Observation equations', f'{self.total_equations * 3}')
        console.result('Constraint equations', f'{self.total_constraints}')
        console.result('Parameters', f'{self.total_parameters * 3}')
        console.result('Model redundancy', f'{dof}')
        console.result('Sum of squared residuals, observations', f'{o_vpv:.3f}')
        console.result('Sum of squared residuals, constraints', f'{c_vpv:.3f}')
        console.result('SQRT(variance of unit weight)', f'{np.sqrt(self.variance):.3f}')

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
                    # n - 1 would be zero for a single-equation constraint, which is
                    # exactly the case a zero-tie produces; there is no redundancy to
                    # divide by, so report the residual itself rather than inf.
                    wrms.append([const, n, np.sqrt((v.T @ v) / max(n - 1, 1)),
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
                    jump = stn.etm.jump_manager.get_geophysical_jump(event.id)
                    ap = jump.p.params
                    # for a combined COSEISMIC_JUMP_DECAY fit, ap[*] holds both the jump
                    # and decay amplitude (shape (2,) per component) -- index down to just
                    # the jump term (get_jump_col's local, non-design-matrix column) so
                    # a_priori lines up with 'constrained', which is only ever the jump
                    # column (get_coseismic_column never returns the decay column).
                    col = jump.get_jump_col(return_col_of_design_matrix=False)
                    earthquakes.append({
                        'station': stationID(stn),
                        'lon': stn.lon,
                        'lat': stn.lat,
                        'event_id': event.id,
                        'relax': 0.0,
                        'a_priori': [ap[1][col], ap[0][col], ap[2][col]],
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
                                self.solution, self.covariance, self.grids, constraint)
                        )
                    except np.linalg.LinAlgError:
                        tqdm.write(f'Skipping field interpolation for {constraint.short_description()} '
                                   f'(singular matrix: not enough independent stations)')
                        continue

        else:
            tqdm.write('System has not been solved! Invoke solve first')
