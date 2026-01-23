"""
Project: Geodesy Database Engine (GeoDE)
Date: 9/12/25 9:32 AM
Author: Demian D. Gomez
"""

# solution_data.py
from abc import ABC, abstractmethod
from typing import List, Optional, Union
from dataclasses import dataclass, field
import json
import numpy as np
import logging

logger = logging.getLogger(__name__)

# app
from ...dbConnection import Cnn
from ...pyDate import Date
from ...Utils import crc32, load_json
from ..core.etm_config import EtmConfig
from ..core.type_declarations import SolutionType
from ..etm_functions.jumps import JumpFunction, JumpType

class SolutionDataException(Exception):
    pass


@dataclass
class CoordinateTimeSeries:
    """Dataclass for coordinate time series data with JSON serialization support"""
    xyz: np.ndarray = field(default_factory=lambda: np.array([]))
    neu: np.ndarray = field(default_factory=lambda: np.array([]))
    time_vector: np.ndarray = field(default_factory=lambda: np.array([]))
    time_vector_mjd: np.ndarray = field(default_factory=lambda: np.array([]))
    dates: List[Date] = field(default_factory=list)  # Store date info as dicts for JSON

    @classmethod
    def from_arrays(cls, xyz: np.ndarray,
                    time_vector: np.ndarray,
                    time_vector_mjd: np.ndarray,
                    dates: List[Date]) -> 'CoordinateTimeSeries':
        """Create from numpy arrays and Date objects"""
        return cls(
            xyz=xyz,
            time_vector=time_vector,
            time_vector_mjd=time_vector_mjd,
            dates=dates
        )

    def __len__(self) -> int:
        """Return number of coordinate points"""
        return self.xyz.shape[1]

    def is_empty(self) -> bool:
        """Check if coordinate series is empty"""
        return self.xyz.size == 0

    def validate(self) -> List[str]:
        """Validate coordinate time series consistency"""
        issues = []
        lengths = [self.xyz.size, self.time_vector.size,
                   self.time_vector_mjd.size, len(self.dates)]

        if not all(l == lengths[0] for l in lengths):
            issues.append("Coordinate and time arrays have different lengths")

        return issues

    def to_json(self, filepath: Optional[str] = None) -> Union[str, None]:
        """Save to JSON file or return JSON string"""
        data = {
            'xyz': self.xyz,
            'time_vector': self.time_vector,
            'time_vector_mjd': self.time_vector_mjd,
            'dates': self.dates
        }

        if filepath:
            with open(filepath, 'w') as f:
                json.dump(data, f, indent=2)
            return None
        else:
            return json.dumps(data, indent=2)

    @classmethod
    def from_json(cls, filepath: Optional[str] = None, json_string: Optional[str] = None) -> 'CoordinateTimeSeries':
        """Load from JSON file or string"""
        if filepath:
            with open(filepath, 'r') as f:
                data = json.load(f)
        elif json_string:
            data = json.loads(json_string)
        else:
            raise ValueError("Either filepath or json_string must be provided")

        return cls(**data)


class SolutionData(ABC):
    """Base class for all solution data types with JSON serialization support"""

    def __init__(self, config: EtmConfig):
        self.network_code = config.network_code
        self.station_code = config.station_code
        self.config = config

        # Common attributes
        self.solutions: int = 0
        self.completion: float = 0.0
        self.soln: str = ""
        self.stack_name: str = ""
        self.project: str = ""

        # Replace individual arrays with dataclass
        self.coordinates = CoordinateTimeSeries()

        # Reference coordinates
        self.metadata = config.metadata

        # Quality control
        self.blunders: List = []
        self.excluded: List = []
        self.gaps: np.ndarray = np.array([])

        # Time series for plotting
        self.time_vector_cont: np.ndarray = np.array([])
        self.time_vector_cont_mjd: np.ndarray = np.array([])

        # no solution and blunders
        self.time_vector_ns: np.ndarray = np.array([])
        self.time_vector_blunders: np.ndarray = np.array([])
        self.rnx_no_ppp: List = []

    # Properties for backward compatibility
    @property
    def lat(self) -> np.ndarray:
        return self.metadata.lat

    @property
    def lon(self) -> np.ndarray:
        return self.metadata.lon

    @property
    def height(self) -> np.ndarray:
        return self.metadata.height

    @property
    def auto_x(self) -> np.ndarray:
        return self.metadata.auto_x

    @property
    def auto_y(self) -> np.ndarray:
        return self.metadata.auto_y

    @property
    def auto_z(self) -> np.ndarray:
        return self.metadata.auto_z

    @property
    def max_dist(self) -> np.ndarray:
        return self.metadata.max_dist

    @property
    def x(self) -> np.ndarray:
        """Get X coordinates as numpy array"""
        return self.coordinates.xyz[0]

    @property
    def y(self) -> np.ndarray:
        """Get Y coordinates as numpy array"""
        return self.coordinates.xyz[1]

    @property
    def z(self) -> np.ndarray:
        """Get Z coordinates as numpy array"""
        return self.coordinates.xyz[2]

    @property
    def time_vector(self) -> np.ndarray:
        """Get time vector as numpy array"""
        return self.coordinates.time_vector

    @property
    def time_vector_mjd(self) -> np.ndarray:
        """Get MJD time vector as numpy array"""
        return self.coordinates.time_vector_mjd

    @property
    def date(self) -> List[Date]:
        """Get dates as list of Date objects"""
        return self.coordinates.dates

    @property
    def hash(self):
        if not self.solutions:
            raise SolutionDataException('Hash value requested but no solutions were loaded!')
        else:
            return self.compute_hash(self.soln + self.stack_name)

    def _set_coordinates_from_arrays(self, xyz: np.ndarray,
                                     time_vector: np.ndarray, time_vector_mjd: np.ndarray,
                                     dates: List[Date]) -> None:
        """Set coordinates from numpy arrays (internal method)"""
        self.coordinates = CoordinateTimeSeries.from_arrays(
            xyz, time_vector, time_vector_mjd, dates
        )
        # immediately transform to neu to leave them ready for export
        self.coordinates.neu = np.array(self.transform_to_local(ignore_data_window=True))
        self.solutions = self.coordinates.xyz.shape[1]

    def _process_coordinate_solutions(self, solutions: List, solution_type: str = "solutions") -> None:
        """Common processing for coordinate solutions"""
        if not len(solutions):
            raise SolutionDataException(f"No {solution_type} for {self.get_station_id()}")

        # Filter by distance from reference coordinates
        coordinates = np.array([(s[0], s[1], s[2]) for s in solutions])
        dates = np.array([(s[3], s[4]) for s in solutions]).astype(int)

        # determine the type of coordinate being passed
        if np.sqrt(np.sum(np.square(coordinates[0, 0:3]))) > 6.3e3:
            reference = np.array([self.auto_x[0], self.auto_y[0], self.auto_z[0]])
        else:
            reference = np.array([0, 0, 0])

        valid_mask = self.filter_by_distance(coordinates, reference)
        valid_solutions = coordinates[valid_mask]
        valid_dates = dates[valid_mask]
        if not valid_solutions.any():
            min_dist = np.sqrt(np.sum(np.square(coordinates - reference), axis=1)).min()
            raise SolutionDataException(
                f"No viable {solution_type} for {self.get_station_id()} "
                f"(minimum distance: {min_dist:.1f}m)"
            )

        # Create time vectors
        dates = [Date(year=year, doy=doy) for year, doy in valid_dates]
        time_vector = np.array([date.fyear for date in dates])
        time_vector_mjd = np.array([date.mjd for date in dates])

        # Set using the new dataclass
        self._set_coordinates_from_arrays(valid_solutions.T, time_vector, time_vector_mjd, dates)

    def _compute_completion_percentage(self, time_vector_ns: np.ndarray) -> None:
        """Common completion percentage calculation"""
        self.time_vector_ns = time_vector_ns
        total_epochs = len(self.time_vector_ns) + len(self.coordinates)
        if total_epochs > 0:
            self.completion = 100.0 - (len(self.time_vector_ns) / total_epochs * 100.0)
        else:
            self.completion = 0.0

    def _validate_basic_data(self, issues: List[str]) -> List[str]:
        """Common validation checks"""
        if self.solutions < self.config.validation.min_solutions_for_etm:
            issues.append(
                f"Insufficient solutions ({self.solutions} < "
                f"{self.config.validation.min_solutions_for_etm})"
            )

        # Use dataclass validation
        coord_issues = self.coordinates.validate()
        issues.extend(coord_issues)

        return issues

    @staticmethod
    def _execute_query(cnn, query: str, query_params: tuple = ()) -> List:
        """Common query execution with logging"""
        if query_params:
            formatted_query = query % query_params
        else:
            formatted_query = query

        return cnn.query_float(formatted_query)

    def create_continuous_time_vector(self) -> None:
        """Create continuous time vectors for plotting"""
        if len(self.coordinates) > 0:
            mjd_array = self.time_vector_mjd
            ts = np.arange(np.min(mjd_array), np.max(mjd_array) + 1, 1)
            self.time_vector_cont_mjd = ts
            self.time_vector_cont = np.array([Date(mjd=mjd).fyear for mjd in ts])
            self.gaps = np.setdiff1d(self.time_vector_cont_mjd, mjd_array)

    def compute_hash(self, additional_data: str = "") -> int:
        """Compute hash for the solution data"""
        hash_input = (
            f"{len(self.coordinates)}_{len(self.blunders)}_"
            f"{self.auto_x[0] if len(self.auto_x) > 0 else 0}_"
            f"{self.auto_y[0] if len(self.auto_y) > 0 else 0}_"
            f"{self.auto_z[0] if len(self.auto_z) > 0 else 0}_"
            f"{self.time_vector.min() if len(self.coordinates) > 0 else 0}_"
            f"{self.time_vector.max() if len(self.coordinates) > 0 else 0}_"
            f"{self.config.modeling.data_model_window}_"
            f"{','.join([repr(model) for model in self.config.modeling.prefit_models])}_"
            f"{additional_data}"
        )
        return crc32(hash_input)

    def transform_to_local(self, ignore_data_window=False) -> List[np.ndarray]:
        """Transform ECEF coordinates to local NEU frame"""
        from geode.Utils import ct2lg

        # apply observation mask (if any)
        if not ignore_data_window:
            mask = self.config.modeling.get_observation_mask(self.time_vector)
        else:
            mask = np.ones(self.time_vector.shape).astype(bool)

        ecef_diff = np.array([
            self.x[mask] - self.auto_x[0],
            self.y[mask] - self.auto_y[0],
            self.z[mask] - self.auto_z[0]
        ])
        neu = list(ct2lg(ecef_diff[0], ecef_diff[1], ecef_diff[2], self.lat[0], self.lon[0]))

        # @ todo: apply detrending models to x y z as well?
        neu = self.apply_prefit_models(self.time_vector[mask], neu)

        return neu

    def apply_prefit_models(self, time_vector: np.ndarray, observations: List[np.ndarray]):
        """detrend the data using the provided models"""
        for model in self.config.modeling.prefit_models:
            logger.info('Applying prefit model ' + str(model))
            for i in range(3):
                if model.p.object == 'jump':
                    observations[i] -= model.eval(i, time_vector, remove_postseismic=True)
                else:
                    observations[i] -= model.eval(i, time_vector)

        return observations

    def transform_to_ecef(self, neu_coords: List[np.ndarray]) -> List[np.ndarray]:
        """Transform local NEU coordinates back to ECEF"""
        from geode.Utils import lg2ct

        ecef_diff = lg2ct(neu_coords[0], neu_coords[1], neu_coords[2],
                          self.lat[0], self.lon[0])
        # Add reference coordinates back
        ecef = [
            ecef_diff[0] + self.auto_x[0],
            ecef_diff[1] + self.auto_y[0],
            ecef_diff[2] + self.auto_z[0]
        ]
        return ecef

    def _load_json(self, json_: Union[str, dict]):
        # load basic fields from json file
        data = load_json(json_)

        if data['observations'] is not None:
            x = data['observations']['xyz'][0]
            y = data['observations']['xyz'][1]
            z = data['observations']['xyz'][2]
            yr = [d['year'] for d in data['observations']['dates']]
            doy = [d['doy'] for d in data['observations']['dates']]

            # deal with prefit functions
            # @todo: finish the other functions
            for i, model in enumerate(self.config.modeling.prefit_models):
                if model['object'] == 'jump':
                    self.config.modeling.prefit_models[i] = JumpFunction(
                        self.config,
                        np.array(data['observations']['time_vector']),
                        Date(**model['jump_date']),
                        JumpType(model['jump_type']),
                        metadata=model['metadata']
                    )
                    self.config.modeling.prefit_models[i].p.params = [np.array(p) for p in model['params']]
                    self.config.modeling.prefit_models[i].p.sigmas = [np.array(p) for p in model['sigmas']]

            self._process_coordinate_solutions([[x,y,z,yr,doy] for x,y,z,yr,doy in zip(x,y,z,yr,doy)])

            self.project = data['solution_options']['project']
            # no info on missing solutions when coming from json
            self.rnx_no_ppp = []
            self.time_vector_ns = np.array([])

            total_epochs = len(self.time_vector_ns) + len(self.time_vector)
            if total_epochs > 0:
                self.completion = 100.0 - (len(self.time_vector_ns) / total_epochs * 100.0)
            else:
                self.completion = 0.0
        else:
            raise SolutionDataException('observations section not present in json file')

    def save_to_json(self, filepath: str) -> None:
        """Save solution data to JSON file"""
        data = {
            'network_code': self.network_code,
            'station_code': self.station_code,
            'solutions': self.solutions,
            'completion': self.completion,
            'soln': self.soln,
            'stack_name': self.stack_name,
            'project': self.project,
            'coordinates': self.coordinates.__dict__,
            'time_vector_ns': self.time_vector_ns.tolist(),
            'gaps': self.gaps.tolist(),
            'excluded': self.excluded,
            'rnx_no_ppp': self.rnx_no_ppp
        }

        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

        logger.info(f"Saved solution data to {filepath}")

    @classmethod
    def create_instance(cls, config: EtmConfig) -> 'SolutionData':
        """Determine the type of object needed and return it to the called"""

        # Determine which subclass to create based on solution type
        if config.solution.solution_type == SolutionType.PPP:
            instance = PPPSolutionData(config)
        elif config.solution.solution_type == SolutionType.GAMIT:
            instance = GAMITSolutionData(config.solution.stack_name, config)
        elif config.solution.solution_type == SolutionType.DRA:
            instance = DRASolutionData(config.solution.project, config)
        elif config.solution.solution_type == SolutionType.NGL:
            instance = FileSolutionData(config)
        else:
            raise ValueError(f"Unknown solution type "
                             f"{config.solution.solution_type.description} not implemented")

        return instance

    # Abstract methods
    @abstractmethod
    def load_data(self, cnn: Cnn = None, **kwargs) -> None:
        """Load data from database or other source"""
        pass

    @abstractmethod
    def validate_data(self) -> List[str]:
        """Validate loaded data and return list of issues"""
        pass

    def get_station_id(self) -> str:
        """Get formatted station identifier"""
        return f"{self.network_code}.{self.station_code}"

    def filter_by_distance(self, coordinates: np.ndarray, reference: np.ndarray) -> np.ndarray:
        """Filter coordinates by distance from reference"""
        distances = np.sqrt(np.sum(np.square(coordinates - reference), axis=1))
        return distances <= self.max_dist


class PPPSolutionData(SolutionData):
    """PPP-specific solution data implementation with JSON support"""

    def __init__(self, config: EtmConfig):
        super().__init__(config)
        self.soln = 'ppp'
        self.stack_name = 'ppp'
        self.project = 'from_ppp'

        # Update config to reflect which solution we are working with
        config.solution.stack_name = 'ppp'
        self.config = config

    def load_data(self, cnn: Cnn = None, **kwargs) -> None:
        """Load PPP solutions from database"""

        if self.config.json_file:
            # if self.config.json_file is set, try to load
            self._load_json(self.config.json_file)
        else:
            self._load_ppp_solutions(cnn)
            self._load_excluded_solutions(cnn)
            self._compute_completion_stats(cnn)

        self.create_continuous_time_vector()

    def _load_ppp_solutions(self, cnn) -> None:
        """Load PPP coordinate solutions"""
        query = '''
            SELECT "X", "Y", "Z", "Year", "DOY" FROM ppp_soln p1
            WHERE p1."NetworkCode" = '%s' AND p1."StationCode" = '%s' 
            ORDER BY "Year", "DOY"
        '''
        logger.info(f'Loading PPP solutions for {self.get_station_id()}')
        solutions = self._execute_query(cnn, query, (self.network_code, self.station_code))

        # Use shared processing method
        self._process_coordinate_solutions(solutions, "PPP solutions")

    def _load_excluded_solutions(self, cnn) -> None:
        """Load list of excluded solutions"""
        query = '''
            SELECT "Year", "DOY" FROM ppp_soln_excl
            WHERE "NetworkCode" = '%s' AND "StationCode" = '%s'
        '''
        self.excluded = self._execute_query(cnn, query, (self.network_code, self.station_code))

    def _compute_completion_stats(self, cnn) -> None:
        """Compute completion percentage and missing solution epochs"""
        query = '''
            SELECT r."ObservationFYear" FROM rinex_proc as r
            LEFT JOIN ppp_soln as p ON 
                r."NetworkCode" = p."NetworkCode" AND
                r."StationCode" = p."StationCode" AND  
                r."ObservationYear" = p."Year" AND
                r."ObservationDOY" = p."DOY"
            WHERE r."NetworkCode" = '%s' AND r."StationCode" = '%s' AND
                p."NetworkCode" IS NULL
        '''
        missing = self._execute_query(cnn, query, (self.network_code, self.station_code))
        time_vector_ns = np.array([float(item[0]) for item in missing])

        # Use shared completion calculation
        self._compute_completion_percentage(time_vector_ns)

    def validate_data(self) -> List[str]:
        """Validate PPP solution data"""
        issues = []

        # Use shared validation
        issues = self._validate_basic_data(issues)

        # PPP-specific validation
        if len(self.coordinates) > 0:
            coord_range = np.ptp([self.x, self.y, self.z])
            if coord_range > 1000:  # 1km seems unreasonable for a single station
                issues.append(f"Coordinate range suspiciously large: {coord_range:.1f}m")

        return issues


class GAMITSolutionData(SolutionData):
    """GAMIT-specific solution data implementation with JSON support"""

    def __init__(self, stack_name: str, config: EtmConfig):
        super().__init__(config)
        self.soln = 'gamit'
        self.stack_name = stack_name

        # Update config to reflect which solution we are working with
        config.solution.stack_name = stack_name
        self.config = config

    def load_data(self, cnn: Cnn = None,
                  polyhedrons: Optional[List] = None, **kwargs) -> None:
        """Load GAMIT solutions from database or polyhedron list"""
        if polyhedrons is None and cnn:
            polyhedrons = self._load_polyhedrons_from_db(cnn)
        elif self.config.json_file:
            # if self.config.json_file is set, try to load
            self._load_json(self.config.json_file)
        elif polyhedrons is None and cnn is None:
            raise SolutionDataException('No source for solution given')

        self._process_polyhedrons(polyhedrons)

        if cnn:
            self._load_project_info(cnn)
            self._load_missing_solutions(cnn)

        self.create_continuous_time_vector()

    def _load_polyhedrons_from_db(self, cnn) -> List:
        """Load polyhedrons from stacks table"""
        query = '''
            SELECT "X", "Y", "Z", "Year", "DOY" FROM stacks
            WHERE "name" = '%s' AND "NetworkCode" = '%s' AND "StationCode" = '%s'
            ORDER BY "Year", "DOY", "NetworkCode", "StationCode"
        ''' % (self.stack_name, self.network_code, self.station_code)
        return cnn.query_float(query)

    def _load_project_info(self, cnn) -> None:
        """Load project information"""
        query = '''
            SELECT "Project" FROM stacks 
            WHERE name = '%s' AND "NetworkCode" = '%s' AND "StationCode" = '%s' 
            LIMIT 1
        ''' % (self.stack_name, self.network_code, self.station_code)
        result = cnn.query_float(query, as_dict=True)
        if result:
            self.project = result[0]['Project']
            self.config.solution.project = self.project

    def _process_polyhedrons(self, polyhedrons: List) -> None:
        """Process polyhedron data into coordinate arrays"""
        if not len(polyhedrons):
            raise SolutionDataException(f"No GAMIT polyhedrons available for {self.get_station_id()} "
                                        f"in stack {self.stack_name}")

        # Use shared processing method
        self._process_coordinate_solutions(polyhedrons, "GAMIT solutions")

    def _load_missing_solutions(self, cnn) -> None:
        """Load epochs with RINEX files but no solutions"""
        query = '''
            SELECT r.* FROM rinex_proc as r
            LEFT JOIN stacks as p ON
                r."NetworkCode" = p."NetworkCode" AND
                r."StationCode" = p."StationCode" AND
                r."ObservationYear" = p."Year" AND
                r."ObservationDOY" = p."DOY" AND
                p."name" = '%s'
            WHERE r."NetworkCode" = '%s' AND r."StationCode" = '%s' AND
                p."NetworkCode" IS NULL
        ''' % (self.stack_name, self.network_code, self.station_code)

        missing = cnn.query(query)
        self.rnx_no_ppp = missing.dictresult()
        self.time_vector_ns = np.array([float(item['ObservationFYear']) for item in self.rnx_no_ppp])

        total_epochs = len(self.time_vector_ns) + len(self.time_vector)
        if total_epochs > 0:
            self.completion = 100.0 - (len(self.time_vector_ns) / total_epochs * 100.0)
        else:
            self.completion = 0.0

    def validate_data(self) -> List[str]:
        """Validate GAMIT solution data"""
        issues = []

        # Use shared validation
        issues = self._validate_basic_data(issues)

        # GAMIT-specific validation
        if not self.project:
            issues.append("No project information available")

        return issues


class DRASolutionData(GAMITSolutionData):
    def __init__(self, project: str, config: EtmConfig):
        super().__init__(project, config)
        self.soln = 'gamit'
        self.stack_name = f'DRA {project}'
        self.project = project

        # Update config to reflect which solution we are working with
        config.solution.project = project
        self.config = config

    def _load_project_info(self, cnn) -> None:
        # no project info to extract from stacks, since no stack yet!
        pass


class FileSolutionData(SolutionData):
    """GAMIT-specific solution data implementation with JSON support"""

    def __init__(self, config: EtmConfig):
        super().__init__(config)
        self.soln = 'ngl'
        self.stack_name = 'external file'
        self.config = config

    def load_data(self, cnn: Cnn = None, **kwargs) -> None:
        """Load GAMIT solutions from database or polyhedron list"""
        # execute on a file with wk XYZ coordinates

        logger.info(f'Loading from external file {self.config.solution.filename}')

        ts = np.genfromtxt(self.config.solution.filename)

        dd = []; x = []; y = []; z = []
        for k in ts:
            d = {}
            for i, f in enumerate(self.config.solution.format):
                if f in ('gpsWeek', 'gpsWeekDay', 'year', 'doy', 'fyear', 'month', 'day', 'mjd'):
                    d[f] = k[i]
                if f == 'x':
                    x.append(k[i])
                elif f == 'y':
                    y.append(k[i])
                elif f == 'z':
                    z.append(k[i])
            dd.append(d)

        dd = [Date(**d) for d in dd]

        polyhedrons = np.array((x, y, z, [d.year for d in dd], [d.doy for d in dd])).transpose()

        self._process_polyhedrons(polyhedrons.tolist())

        self.create_continuous_time_vector()

    def _process_polyhedrons(self, polyhedrons: List) -> None:
        """Process polyhedron data into coordinate arrays"""
        if not polyhedrons:
            raise SolutionDataException(f"No solution available for {self.get_station_id()} "
                                        f"in {self.config.solution.filename}")

        # Use shared processing method
        self._process_coordinate_solutions(polyhedrons, "Text file solutions")

    def _load_missing_solutions(self, cnn) -> None:
        """Default completion of 100%"""
        self.completion = 100.0

    def validate_data(self) -> List[str]:
        """Validate GAMIT solution data"""
        issues = []

        # Use shared validation
        issues = self._validate_basic_data(issues)

        return issues