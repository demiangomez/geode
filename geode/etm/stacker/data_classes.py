"""
ETM Stacker data classes.
"""

from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Union, TYPE_CHECKING
import numpy as np

from ..core.type_declarations import JumpType
from ..core.data_classes import BaseDataClass, Earthquake, AdjustmentResults
from ..core.etm_config import EtmConfig
from ..etm_functions.polynomial import PolynomialFunction
from ..etm_functions.jumps import JumpFunction
from ...pyDate import Date

if TYPE_CHECKING:
    from ..core.etm_engine import EtmEngine
    from .grid_system import GridSystem
    from .types import ConstraintType
    from .constraints.interseismic import InterseismicConstraint
    from .constraints.coseismic import CoseismicConstraint
    from .constraints.postseismic import PostseismicConstraint

@dataclass
class EtmStackerConfig(BaseDataClass):
    """Configuration for ETM Stacker."""
    max_condition_number: float = 15
    earthquake_magnitude_limit: float = 8.0
    post_seismic_back_lim: Union[float, Date] = field(default_factory=lambda: Date(year=1990, doy=1))
    relaxation: np.ndarray = field(default_factory=lambda: np.array([0.05, 1.0]))
    earthquakes_cherry_picked: list = field(default_factory=list)
    earthquakes_to_remove: list = field(default_factory=list)
    station_weight_scale: float = 1.0
    interseismic_h_sigma: float = 0.0005
    interseismic_v_sigma: float = 0.001
    coseismic_h_sigma: float = 1
    coseismic_v_sigma: float = 1
    postseismic_h_sigma: float = 0.001   # 1 mm — matches PostseismicConstraint class default
    postseismic_v_sigma: float = 0.003   # 3 mm — matches PostseismicConstraint class default
    vertical_method: str = 'spline2d'
    vertical_load_radius: float = 50  # in km
    tension: float = 0.10
    grid_spacing: float = 25  # in km
    min_span_completion: float = 75.0
    min_time_span: float = 2.5
    # Off by default: when False, sparse stations are detected and set aside
    # (self.sparse_stations) exactly as before, but never feed back into the shared
    # normal-equation system. When True, each sparse station gets a dedicated
    # NoiseCovarianceGLS free fit (Station.free_fit) and its cleaned data is folded
    # directly into the base normal equations -- see constraints/sparse.py.
    sparse_constraints_enabled: bool = False
    # How many full stations need to already have data before the network's spatial
    # fields are trusted at all (see constraints/sparse.overall_validity_date): the
    # validity date is the epoch by which at least this many full stations have data,
    # full stop -- a property of the network's own coverage, not of any one field.
    sparse_validity_min_stations: int = 2
    # Overrides the auto-computed "model validity" start date (fyear) that
    # constraints/sparse.py uses to drop epochs predating adequate full-station coverage
    # (see overall_validity_date there) -- both for folding a sparse station's data into
    # the system and for windowing its diagnostic constrained-ETM plot. None (default)
    # keeps the auto-computed date.
    sparse_validity_override: Union[float, None] = None
    # Experimental: a common-mode (rank-1, not-diagonal) trust ceiling added to every
    # sparse station's own observation covariance before whitening -- see
    # constraints/sparse._whiten_and_weight's docstring. 0.0 (default) leaves behavior
    # unchanged; this is here to test the idea empirically, not yet validated.
    sparse_common_mode_sigma: float = 0.0
    # Experimental: when True, a sparse station's contribution is collapsed to one point
    # estimate per field (via pseudo-inverse) and injected as a constraint capped at that
    # field's own h_sigma/v_sigma, instead of being folded into the base system at full
    # data strength -- see constraints/sparse._collapse_to_tempered_constraint. False
    # (default) keeps the original direct-injection behavior.
    sparse_constraint_mode: bool = False
    # On by default: tightens (never loosens) the mutual-constraint weight on a full
    # station's own velocity/postseismic-decay ties when that station's own
    # normal-equation diagonal implies far more confidence than its external tie, which
    # happens when velocity and a relaxation term are highly correlated in that
    # station's own fit (e.g. a postseismic event whose decay has mostly saturated by
    # the time most data arrives, making it look almost like a straight trend line) --
    # see constraints/adaptive_weight.py. A well-conditioned station's own diagonal
    # never triggers this, so it's a no-op for most stations.
    adaptive_constraint_weighting_enabled: bool = True
    # Caps the ratio between a flagged station's own diagonal and its external tie's
    # weight at this value (own_diag / tie_weight <= target_ratio). Smaller means the
    # external (spatially-interpolated) field is trusted more over that station's own
    # data; empirically the result is insensitive to this below ~1 (converges to the
    # field-only prediction) and degrades gradually, not sharply, above it -- see
    # constraints/adaptive_weight.py's module docstring for the reasoning. 5.0 was
    # validated against a severely entangled case (~0.88 correlation) without visibly
    # affecting well-conditioned stations.
    adaptive_constraint_target_ratio: float = 5.0
    # Off by default (slow -- see parallel_workers below): fit every full station's own
    # ETM (both the unconstrained Step 1 and the cherry-picked Step 2 build_etm does, so
    # the Step 3 obs_sigmas/noise_covariance_lag restoration stays unbiased -- see
    # etm_builder.build_etm) with NoiseCovarianceGLS instead of the default robust least
    # squares, and use the resulting colored-noise covariance (not a diagonal weight) in
    # that station's own contribution to the shared normal equations -- see
    # normal_equations._create_normal_equations. This complements, not replaces,
    # adaptive_constraint_weighting_enabled: GLS corrects an overconfident own-diagonal
    # (the actual cause), the adaptive weight corrects the remaining imbalance once that
    # diagonal is honest. Off by default because full-network GLS is considerably
    # slower than robust least squares; use parallel_workers to offset that cost.
    gls_for_all_stations: bool = False
    # Number of worker processes used to build each station's own ETM (the unit of work
    # etm_builder.build_etm already does independently per station -- see its own module
    # docstring). 1 (default) runs serially, same as before this existed. Only affects
    # how stations are built, not the shared normal-equation solve itself, which stays
    # single-process. See stacker.add_stations_parallel.
    parallel_workers: int = 1


@dataclass
class NormalEquations:
    """Holds normal equation components for a station."""
    station: str
    # stored as ENU
    neq: List[np.ndarray] = field(
        default_factory=lambda: [np.array([]), np.array([]), np.array([])]
    )
    # stored as ENU
    ceq: List[np.ndarray] = field(
        default_factory=lambda: [np.array([]), np.array([]), np.array([])]
    )
    design_matrix: np.ndarray = field(
        default_factory=lambda: np.array([])
    )
    # stored as ENU
    observation_vector: List[np.ndarray] = field(
        default_factory=lambda: [np.array([]), np.array([]), np.array([])]
    )
    observation_weights: List[np.ndarray] = field(
        default_factory=lambda: [np.array([]), np.array([]), np.array([])]
    )
    weighted_observations: List[float] = field(
        default_factory=lambda: [0., 0., 0.]
    )
    weight_scale: float = 1.0
    dof: int = 0
    prior_wrms: List[float] = field(
        default_factory=lambda: [0., 0., 0.]
    )

    parameter_count: int = 0
    equation_count: int = 0
    parameter_start_idx: int = 0
    # range of indices in the general normal equations matrix
    parameter_range: List[np.ndarray] = field(
        default_factory=lambda: [np.array([])]
    )


@dataclass
class Station:
    """Pure data about a station - no computation logic."""
    network_code: str
    station_code: str
    lon: float
    lat: float
    first_obs: Date = None
    etm: 'EtmEngine' = None
    normal_equations: NormalEquations = None

    # Geometric properties
    projected_coords: Tuple[float, float] = (0., 0.)
    vertical_response: np.ndarray = field(default_factory=lambda: np.array([]))

    # Parameter tracking
    is_interseismic: bool = False
    etm_constraints: List = None
    posterior_wrms: List = None

    # Cache for a sparse station's "free fit": the same kind of plain, no-cherry-picked
    # ETM fit that etm_builder.build_etm already computes for every station (there, to
    # get unbiased obs_sigmas and detect mechanical jumps for every station, not just
    # sparse ones). It is cached here too because a sparse station's own cherry-picked
    # ETM (station.etm) is not a safe stand-in for it: that fit jointly estimates
    # nuisance terms (constant/periodic/mechanical jumps) alongside geophysical jumps,
    # so using its output to clean the station's data would reintroduce that bias.
    # Only ever populated for stations in EtmStacker.sparse_stations, and only when
    # EtmStackerConfig.sparse_constraints_enabled is True.
    free_fit: 'EtmEngine' = None

    def __str__(self):
        return f'{self.network_code}.{self.station_code}'

    def get_velocity_column(self):
        polynomial = self.etm.design_matrix.get_polynomial()
        return polynomial.column_index[1] + self.normal_equations.parameter_start_idx

    def get_periodic_column(self, frequency: float):
        """By default returns the index of sin."""
        periodic = self.etm.design_matrix.get_periodic()
        if periodic is not None:
            return np.array(periodic.get_periodic_cols(frequency)) + self.normal_equations.parameter_start_idx
        else:
            return None

    def get_coseismic_column(self, jump_or_id: Union[Earthquake, str]):
        """Get the column of a given earthquake or earthquake id."""
        if isinstance(jump_or_id, Earthquake):
            event_id = jump_or_id.id
        else:
            event_id = jump_or_id

        jumps = [j for j in self.etm.jump_manager.jumps if j.is_geophysical()
                 and j.earthquake is not None and j.p.jump_type < JumpType.POSTSEISMIC_ONLY and
                 j.earthquake.id == event_id and j.fit]

        if jumps:
            return jumps[0].get_jump_col() + self.normal_equations.parameter_start_idx
        else:
            return None

    def get_postseismic_column(self, jump_or_id: Union[Earthquake, str], relaxation: float):
        """Get the column of a given earthquake or earthquake id and relaxation."""
        if isinstance(jump_or_id, Earthquake):
            event_id = jump_or_id.id
        else:
            event_id = jump_or_id

        jumps = [j for j in self.etm.jump_manager.jumps if j.is_geophysical()
                 and j.earthquake is not None and j.p.jump_type != JumpType.COSEISMIC_ONLY and
                 j.earthquake.id == event_id and j.fit]

        if jumps:
            return np.array(jumps[0].get_relaxation_cols(relaxation)) + self.normal_equations.parameter_start_idx
        else:
            return None

    def get_constrained_velocity(self, solution, covariance):
        """Return the velocity and sigma."""
        idx = self.get_velocity_column()
        tp = solution.shape[1]
        return solution[:, idx], np.sqrt(np.diag(covariance)[[idx, idx + tp, idx + 2 * tp]])

    def get_constrained_periodic(self, frequency: float, solution, covariance):
        """Return the periodic and sigma."""
        idx = self.get_periodic_column(frequency)
        tp = solution.shape[1]
        return solution[:, idx], np.sqrt(np.diag(covariance)[[idx, idx + tp, idx + 2 * tp]])

    def get_constrained_jump(self, event: Union[Earthquake, str],
                             solution: np.ndarray,
                             covariance: np.ndarray) -> Union[Tuple[np.ndarray, np.ndarray], Tuple[None, None]]:
        """Return the jump and sigma or none if not fit."""
        idx = self.get_coseismic_column(event)
        if idx:
            tp = solution.shape[1]
            return solution[:, idx], np.sqrt(np.diag(covariance)[[idx, idx + tp, idx + 2 * tp]])
        else:
            return None, None

    def get_constrained_relax(self, event: Union[Earthquake, str],
                              relaxation: float,
                              solution: np.ndarray,
                              covariance: np.ndarray) -> Union[Tuple[np.ndarray, np.ndarray], Tuple[None, None]]:
        """Return the postseismic relaxation and sigma."""
        idx = self.get_postseismic_column(event, relaxation)
        tp = solution.shape[1]
        if idx is not None:
            # this only requests one relaxation so solution[:, idx] can be flattened to be only 1d
            return solution[:, idx].flatten(), np.sqrt(np.diag(covariance)[[idx, idx + tp, idx + 2 * tp]]).flatten()
        else:
            return None, None

    def extract_etm_constraints(self,
                                earthquakes: List[Earthquake],
                                relaxations: np.ndarray,
                                solution: np.ndarray,
                                covariance: np.ndarray):
        """Extract ETM constraints from solution."""
        # for mapping NEU to ENU
        col = [1, 0, 2]

        self.etm_constraints = []

        # create the polynomial constraint
        poly = PolynomialFunction(self.etm.config)
        vel, sig = self.get_constrained_velocity(solution, covariance)
        # MULT sigma to force ETM to honor the stacked parameters
        MULT = 100
        for j in range(3):
            # assign the values
            poly.p.params[j][1] = vel[col[j]]
            poly.p.sigmas[j][1] = sig[col[j]] / MULT

        self.etm_constraints.append(poly)

        # create a constraint for each earthquake
        for event in earthquakes:
            jump = self.etm.jump_manager.get_geophysical_jump(event.id)

            if jump and jump.fit:
                # create the constraint
                jc = JumpFunction(self.etm.config, time_vector=np.array([0]),
                                  date=jump.date, jump_type=jump.p.jump_type, fit=False)

                if jump.p.jump_type < JumpType.POSTSEISMIC_ONLY:
                    par, sig = self.get_constrained_jump(event, solution, covariance)

                    for j in range(3):
                        # assign the values (coseismic, always 0)
                        jc.p.params[j][0] = par[col[j]]
                        jc.p.sigmas[j][0] = sig[col[j]] / MULT

                for relax in relaxations:
                    par, sig = self.get_constrained_relax(event, relax, solution, covariance)
                    idj = jump.get_relaxation_cols(relax, False)

                    for j in range(3):
                        # assign the values
                        jc.p.params[j][idj] = par[col[j]]
                        jc.p.sigmas[j][idj] = sig[col[j]] / MULT

                self.etm_constraints.append(jc)
        return

    def __hash__(self):
        """Hash based on station identity only."""
        return hash((self.network_code, self.station_code))

    def __eq__(self, other):
        """Two stations are equal if they have the same network and station code."""
        if not isinstance(other, Station):
            return False
        return (self.network_code == other.network_code and
                self.station_code == other.station_code)


@dataclass
class ConstraintEquation:
    """Parameters for a single constraint equation."""
    station: Station
    # design matrix of the constraint
    constraint_design: Tuple[np.ndarray, np.ndarray, np.ndarray] = field(
        default_factory=lambda: [np.array([]), np.array([]), np.array([])]
    )
    constraint_sigma: np.ndarray = field(default_factory=lambda: np.array([]))  # Constraint sigma (for weighting)
    is_active: bool = True  # Can be toggled on/off


@dataclass
class EtmStackerField(BaseDataClass):
    """Field data for ETM stacking results."""
    base_type: 'ConstraintType'
    grids: 'GridSystem'
    onset_date: Date = None
    event: Earthquake = None
    relaxation: float = None
    enu_field: np.ndarray = None
    enu_sigma: Union[np.ndarray, None] = None
    enu_covar: Union[np.ndarray, None] = None
    constrain_stations: List[Station] = field(default_factory=list)
    constrained_parameters: np.ndarray = None
    convex_hull: np.ndarray = None

    @property
    def description(self) -> str:
        # Import here to avoid circular imports
        from .types import ConstraintType

        if self.base_type == ConstraintType.COSEISMIC:
            event = f' {self.event.date.yyyyddd()} {self.event.id:.16}'
        elif self.base_type == ConstraintType.POSTSEISMIC:
            event = f' {self.event.date.yyyyddd()} {self.event.id:.16} ({self.relaxation:.3f})'
        else:
            event = ''
        return self.base_type.description + event

    @classmethod
    def create_field(cls, stations: List[Station],
                     solution: np.ndarray,
                     covariance: np.ndarray,
                     grids: 'GridSystem',
                     constraint: Union['InterseismicConstraint', 'CoseismicConstraint', 'PostseismicConstraint']):
        """Create field(s) from solution."""

        # Import here to avoid circular imports
        from .types import ConstraintType

        params, sigmas = constraint.get_parameters_and_covariance(solution, covariance)

        if constraint.constraint_type == ConstraintType.INTERSEISMIC:
            velocity_field, velocity_sigma, velocity_cova = grids.interpolate_field(stations, params, sigmas)

            return EtmStackerField(
                ConstraintType.INTERSEISMIC, grids, enu_field=velocity_field, enu_sigma=velocity_sigma,
                constrain_stations=stations, constrained_parameters=params, enu_covar=velocity_cova)
        else:
            seismic_field, seismic_sigma, seismic_covar = grids.predict_seismic_deformation(
                constraint.event, constraint.station_list, params, sigmas, constraint
            )

            return EtmStackerField(
                constraint.constraint_type, grids, enu_field=seismic_field, onset_date=constraint.event.date,
                enu_sigma=seismic_sigma, constrain_stations=constraint.station_list,
                event=constraint.event, relaxation=constraint.relaxation,
                constrained_parameters=params, enu_covar=seismic_covar
            )

    def get_interpolation_grid(self) -> Tuple[np.ndarray, np.ndarray]:
        """Get interpolation grid coordinates."""
        from .types import ConstraintType

        if self.base_type == ConstraintType.INTERSEISMIC:
            return self.grids.interpolation_grid[0], self.grids.interpolation_grid[1]
        elif self.base_type == ConstraintType.POSTSEISMIC:
            mask = self.grids.earthquake_masks[self.event.id][1]
        else:
            mask = self.grids.earthquake_masks[self.event.id][0]

        return self.grids.interpolation_grid[0][mask], self.grids.interpolation_grid[1][mask]

    def get_interpolation_grid_geographic(self) -> Tuple[np.ndarray, np.ndarray]:
        """Get interpolation grid in geographic coordinates."""
        from .types import ConstraintType

        if self.base_type == ConstraintType.INTERSEISMIC:
            return self.grids.interpolation_geographic[0], self.grids.interpolation_geographic[1]
        elif self.base_type == ConstraintType.POSTSEISMIC:
            mask = self.grids.earthquake_masks[self.event.id][1]
        else:
            mask = self.grids.earthquake_masks[self.event.id][0]

        return self.grids.interpolation_geographic[0][mask], self.grids.interpolation_geographic[1][mask]

    def get_values_geo(self, lon: float, lat: float) -> Tuple[Union[Tuple[np.ndarray, np.ndarray, np.ndarray], None],
        Union[Tuple[np.ndarray, np.ndarray, np.ndarray], None]]:
        """Get the field value interpolated to input lon lat."""
        from .types import ConstraintType

        # first check if the station needs this field (CO or POST)
        if self.base_type in (ConstraintType.COSEISMIC, ConstraintType.POSTSEISMIC):
            # check if we really need to apply
            mask = self.grids.earthquake_masks[self.event.id][2]
            s_score, p_score = mask.score(lat, lon)
            if s_score == 0 and self.base_type == ConstraintType.COSEISMIC:
                return None, None
            elif p_score == 0 and self.base_type == ConstraintType.POSTSEISMIC:
                return None, None

        x, y = self.grids.transform_coordinate(lon, lat)

        return self.get_values_proj(x, y)

    def get_values_proj(self,
                        x: float,
                        y: float) -> Union[Tuple[None, None],
                        Tuple[Tuple[np.ndarray, np.ndarray, np.ndarray], Tuple[np.ndarray, np.ndarray, np.ndarray]]]:
        """Get the field value interpolated to input x y."""
        from .types import ConstraintType
        from ...elasticity.elastic_interpolation import get_radius
        from scipy.interpolate import griddata

        # unpack the grid values
        xg, yg = self.get_interpolation_grid()
        # unpack the field values
        ve, vn, vu = self.enu_field
        if self.enu_sigma is not None:
            se, sn, su = self.enu_sigma
        # determine the valid locations
        valid = ~np.isnan(ve)

        # find the data within a certain radius of the requested point
        r, _, _ = get_radius(np.column_stack([xg, yg]), np.column_stack([x, y]))
        # invalidate range to points outside the valid area
        r[0, ~valid] = np.inf
        # get 10 closest points (for safety)
        sorted_indices = np.argsort(r, axis=None)
        valid[sorted_indices[10:]] = False

        if np.sum(valid) > 3:
            ve_int = griddata((xg[valid], yg[valid]), ve[valid], (x, y), method='cubic')
            vn_int = griddata((xg[valid], yg[valid]), vn[valid], (x, y), method='cubic')
            vu_int = griddata((xg[valid], yg[valid]), vu[valid], (x, y), method='cubic')
            # now interpolate sigmas
            if self.enu_sigma is not None:
                se_int = griddata((xg[valid], yg[valid]), se[valid], (x, y), method='cubic')
                sn_int = griddata((xg[valid], yg[valid]), sn[valid], (x, y), method='cubic')
                su_int = griddata((xg[valid], yg[valid]), su[valid], (x, y), method='cubic')

            if not np.isnan(ve_int):
                if self.base_type == ConstraintType.INTERSEISMIC:
                    ve_out = np.array([0, ve_int])
                    vn_out = np.array([0, vn_int])
                    vu_out = np.array([0, vu_int])

                    if self.enu_sigma is not None:
                        se_out = np.array([0, se_int])
                        sn_out = np.array([0, sn_int])
                        su_out = np.array([0, su_int])
                    else:
                        se_out = np.array([0, 0])
                        sn_out = np.array([0, 0])
                        su_out = np.array([0, 0])
                else:
                    ve_out = np.array([ve_int])
                    vn_out = np.array([vn_int])
                    vu_out = np.array([vu_int])

                    if self.enu_sigma is not None:
                        se_out = np.array([se_int])
                        sn_out = np.array([sn_int])
                        su_out = np.array([su_int])
                    else:
                        se_out = np.array([0, 0])
                        sn_out = np.array([0, 0])
                        su_out = np.array([0, 0])
            else:
                return None, None
        else:
            return None, None

        return (ve_out, vn_out, vu_out), (se_out, sn_out, su_out)

    def get_etm_function(self, lon: float, lat: float, time_vector: np.ndarray,
                         network_code: str = '', station_code: str = ''):
        """
        Given a position in lat lon and a time vector (optionally provide a network and station code)
        get the corresponding EtmFunction.
        """
        config = EtmConfig(network_code, station_code)
        config.metadata.lon = np.array([lon])
        config.metadata.lat = np.array([lat])
        config.modeling.relaxation = np.array([self.relaxation])

        # init the AdjustmentResults to pass to the etm_function
        result = [AdjustmentResults(), AdjustmentResults(), AdjustmentResults()]
        values, sigmas = self.get_values_geo(lon, lat)

        if values is not None:
            for i in range(3):
                result[i].parameters = values[i]
                result[i].parameter_sigmas = sigmas[i]
                # dummy covariance matrix
                result[i].covariance_matrix = np.ones((2, 2))

            funct = self.base_type.function(config=config, time_vector=time_vector, date=self.onset_date)

            funct.column_index = np.arange(0, funct.param_count)
            funct.load_parameters(result)

            return funct
        else:
            return None

    def eval(self, lon: float, lat: float, time_vector: np.ndarray):
        """Evaluate the field at a given location."""
        from .types import ConstraintType

        # a generic config to pass to the functions
        funct = self.get_etm_function(lon, lat, time_vector)

        if funct is not None:
            values = []
            for i in range(3):
                if self.base_type != ConstraintType.INTERSEISMIC:
                    # must remove interseismic
                    val = funct.eval(i, remove_postseismic=True)
                else:
                    val = funct.eval(i)

                if np.any(np.isnan(val)):
                    values.append(0)
                else:
                    values.append(val)

            return np.array(values)
        return np.zeros((3, time_vector.size))
