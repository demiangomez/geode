"""
Project: Geodetic Database Engine (GeoDE)
Date: 09/12/2025 09:20 AM
Author: Demian D. Gomez
"""
from typing import Dict, List, Optional, Any, Union
import numpy as np
import logging

logger = logging.getLogger(__name__)

# app
from ...pyDate import Date
from ...Utils import load_json
from ...dbConnection import Cnn
from ..core.type_declarations import PeriodicStatus, JumpType, EtmException
from ..core.logging_config import setup_etm_logging
from ..core.data_classes import (SolutionOptions, ModelingParameters, SolutionType,
                                 ValidationRules, StationMetadata, JumpParameters)
from ..visualization.data_classes import PlotOutputConfig


class EtmConfig:
    """Central configuration manager for ETM operations"""

    def __init__(self,
                 network_code: str = '',
                 station_code: str = '',
                 custom_config: Optional[Dict[str, Any]] = None,
                 cnn: Cnn = None,
                 solution_options: SolutionOptions = None,
                 json_file: Union[str, dict] = None,
                 silent: bool = False,
                 post_seismic_back_lim: Optional[Union[int, Date]] = None,
                 earthquake_magnitude_limit: Optional[float] = None,
                 earthquakes_cherry_picked: Optional[List[str]] = None):
        """
        Initialize ETM configuration

        Args:
            custom_config: Dictionary of custom configuration overrides
            cnn: Database connection (if loading from database)
            network_code: Station network code (if loading from database)
            station_code: Station code (if loading from database)
            json_file: either a json file path or a json dict or string to load data from
            post_seismic_back_lim: overrides ModelingParameters default; applied before
                the database load so the earthquake jump query (ScoreTable) runs once
                with the final value instead of needing a later refresh_config() reload
            earthquake_magnitude_limit: same as above, for the s-score magnitude cutoff
            earthquakes_cherry_picked: same as above, for the forced-event id list
        """
        setup_etm_logging(level=logging.CRITICAL if silent else logging.INFO)

        self.json_file: Union[str, dict] = json_file

        if not json_file:
            self.network_code = network_code
            self.station_code = station_code

            self.solution = SolutionOptions()
            self.modeling = ModelingParameters()
            self.plotting_config = PlotOutputConfig()
            self.validation = ValidationRules()
            self.metadata = StationMetadata()
        else:
            self.load_from_json(json_file)

        if post_seismic_back_lim is not None:
            self.modeling.post_seismic_back_lim = post_seismic_back_lim
        if earthquake_magnitude_limit is not None:
            self.modeling.earthquake_magnitude_limit = earthquake_magnitude_limit
        if earthquakes_cherry_picked is not None:
            self.modeling.earthquakes_cherry_picked = earthquakes_cherry_picked

        # Language support
        self.language = 'eng'
        self._language_dict = {
            'eng': {
                "station": "Station",
                "north": "North",
                "east": "East",
                "up": "Up",
                "table_title": "  Year Day Relx    [mm] Mag D [km]",
                "periodic": "Seasonal Amplitude",
                "velocity": "Velocity",
                "from_model": "from model",
                "acceleration": "Acceleration",
                "position": "Conventional Epoch Pos.",
                "completion": "Completion",
                "other": "other polynomial terms",
                "not_enough": "Not enough solutions to fit an ETM.",
                "table_too_long": "[...]",
                "frequency": "Frequency",
                "N residuals": "N Residuals",
                "E residuals": "E Residuals",
                "U residuals": "U Residuals",
                "histogram plot": "Histogram",
                "residual plot": "Residual Plot",
                "jumps": "Jumps",
                "polynomial": "Polynomial",
                "seasonal": "Seasonal",
                "stochastic": "Stochastic",
                "removed": "Removed",
                "prefit": "Prefit"
            },
            'spa': {
                "station": "Estación",
                "north": "Norte",
                "east": "Este",
                "up": "Arriba",
                "table_title": "  Año  Día Relx    [mm] Mag D [km]",
                "periodic": "Amplitud Estacional",
                "velocity": "Velocidad",
                "from_model": "de modelo",
                "acceleration": "Aceleración",
                "position": "Posición Época Conv.",
                "completion": "Completitud",
                "other": "otros términos polinómicos",
                "not_enough": "No hay suficientes soluciones para ajustar trayectorias.",
                "table_too_long": "[...]",
                "frequency": "Frecuencia",
                "N residuals": "Residuos N",
                "E residuals": "Residuos E",
                "U residuals": "Residuos U",
                "histogram plot": "Histograma",
                "residual plot": "Gráfico de Residuos",
                "jumps": "Saltos",
                "polynomial": "Polinomio",
                "seasonal": "Estacionales",
                "stochastic": "Estocástico",
                "removed": "Removido(s)",
                "prefit": "Pre-ajuste"
            },
            'fra': {
                "station": "Station",
                "north": "Nord",
                "east": "Est",
                "up": "Haut",
                "table_title": "  An   Jou Relx    [mm] Mag D [km]",
                "periodic": "Amplitude Saisonnière",
                "velocity": "Vélocité",
                "from_model": "du modèle",
                "acceleration": "Accélération",
                "position": "Position d'Époque Conventionnelle",
                "completion": "Achèvement",
                "other": "autres termes polynomiaux",
                "not_enough": "Pas assez de solutions pour ajuster un ETM.",
                "table_too_long": "[...]",
                "frequency": "Fréquence",
                "N residuals": "Résidus N",
                "E residuals": "Résidus E",
                "U residuals": "Résidus U",
                "histogram plot": "Histogramme",
                "residual plot": "Graphique des Résidus",
                "jumps": "Sauts",
                "polynomial": "Polynôme",
                "seasonal": "Saisonnier",
                "stochastic": "Stochastique",
                "removed": "Supprimé",
                "prefit": "Prefit"
            }
        }
        if solution_options:
            self.solution = solution_options

        if cnn:
            # loading parameters from the database
            self._load_from_database(cnn)

        if custom_config:
            self.apply_custom_config(custom_config)

    def get_station_id(self) -> str:
        """Get formatted station identifier"""
        return f"{self.network_code}.{self.station_code}"

    def build_filename(self):
        """build a generic file name to use to save files"""
        return self.get_station_id() + "_" + self.solution.stack_name

    def _load_from_database(self, cnn: Cnn) -> None:
        """Load station-specific configuration from database"""
        try:
            self._load_station_metadata(cnn)
            self._load_polynomial_config(cnn)
            self._load_periodic_config(cnn)
            self._load_jump_config(cnn)

            logger.info(f"Loaded configuration from database for {self.network_code}.{self.station_code}")

        except EtmException as e:
            logger.warning(f"Failed to load database config for {self.network_code}.{self.station_code}: {e}")
            logger.info("Using default configuration")

    def _load_station_metadata(self, cnn):
        """Load station metadata from database"""
        query = '''
                SELECT * FROM stations 
                WHERE "NetworkCode" = '%s' AND "StationCode" = '%s'
            ''' % (self.network_code, self.station_code)

        stn = cnn.query_float(query, as_dict=True)

        if not stn or stn[0]['lat'] is None:
            raise ValueError(f"No valid metadata for station {self.get_station_id()}")

        if stn[0]['DateStart'] is None:
            # a few old stations with no DateStart, update table
            cnn.query('''
            UPDATE stations 
                SET "DateStart" = r.min_date,
                    "DateEnd" = r.max_date
                FROM (
                    SELECT min("ObservationFYear") as min_date, 
                           max("ObservationFYear") as max_date
                    FROM rinex 
                    WHERE "NetworkCode" = '%s' AND "StationCode" = '%s'
                ) r
                WHERE "NetworkCode" = '%s' AND "StationCode" = '%s'
            ''' % (self.network_code, self.station_code, self.network_code, self.station_code))
            # run query again to get updated data
            stn = cnn.query_float(query, as_dict=True)

        """Load station reference coordinates and metadata"""
        self.metadata.name = stn[0]['StationName']
        self.metadata.country_code = stn[0]['country_code']
        self.metadata.lat = np.array([float(stn[0]['lat'])])
        self.metadata.lon = np.array([float(stn[0]['lon'])])
        self.metadata.height = np.array([float(stn[0]['height'])])
        self.metadata.auto_x = np.array([float(stn[0]['auto_x'])])
        self.metadata.auto_y = np.array([float(stn[0]['auto_y'])])
        self.metadata.auto_z = np.array([float(stn[0]['auto_z'])])
        if stn[0]['DateStart'] is not None:
            self.metadata.first_obs = Date(fyear=stn[0]['DateStart'])
        if stn[0]['DateEnd'] is not None:
            self.metadata.last_obs = Date(fyear=stn[0]['DateEnd'])
        self.metadata.max_dist = 20.0 if not stn[0]['max_dist'] else stn[0]['max_dist']

        # DateStart/DateEnd drive the earthquake search window in _load_jump_config: if these
        # are wrong (e.g. hand-edited in the stations table) the ETM will silently miss
        # geophysical jumps outside the resulting window, so log them here for easy diagnosis
        logger.info(f"Loaded station metadata for {self.get_station_id()}: "
                    f"lat={self.metadata.lat[0]:.8f} lon={self.metadata.lon[0]:.8f} "
                    f"DateStart={self.metadata.first_obs} DateEnd={self.metadata.last_obs}")

        # as part of the metadata, load the station info
        from ...metadata.station_info import StationInfo, StationInfoException
        try:
            station_info = StationInfo(cnn, self.network_code, self.station_code, allow_empty=True)

            self.metadata.station_information = station_info.records
        except StationInfoException:
            self.metadata.station_information = []

    def _load_polynomial_config(self, cnn) -> None:
        """Load polynomial configuration from etm_params table"""
        query = '''
            SELECT "terms", "Year", "DOY" FROM etm_params 
            WHERE "NetworkCode" = '%s' AND "StationCode" = '%s' 
            AND "object" = 'polynomial' AND "soln" = '%s'
            LIMIT 1
        ''' % (self.network_code, self.station_code,  self.solution.solution_type.code)

        try:
            result = cnn.query_float(query, as_dict=True)

            if result:
                row = result[0]
                if row.get('terms'):
                    self.modeling.poly_terms = int(row['terms'])

                # Set reference epoch if specified
                if row.get('Year') and row.get('DOY'):
                    from geode import pyDate
                    ref_date = pyDate.Date(year=int(row['Year']), doy=int(row['DOY']))
                    self.modeling.reference_epoch = ref_date.fyear

                logger.info(f"Loaded custom polynomial config from database: "
                           f"terms={self.modeling.poly_terms} reference_epoch={self.modeling.reference_epoch}")

        except EtmException as e:
            logger.debug(f"No polynomial config in database: {e}")

    def _load_periodic_config(self, cnn) -> None:
        """Load periodic configuration from etm_params table"""
        query = '''
            SELECT "frequencies" FROM etm_params 
            WHERE "NetworkCode" = '%s' AND "StationCode" = '%s' 
            AND "object" = 'periodic' AND "soln" = '%s'
            LIMIT 1
        ''' % (self.network_code, self.station_code,  self.solution.solution_type.code)

        try:
            result = cnn.query_float(query, as_dict=True)

            if result and result[0].get('frequencies'):
                # Assuming frequencies are stored as array in database
                freqs = result[0]['frequencies']
                if isinstance(freqs, (list, tuple)):
                    self.modeling.frequencies = np.array(freqs)
                    self.modeling.periodic_status = PeriodicStatus.ADDED_BY_USER

                    logger.info(f"Loaded custom periodic config from database: "
                               f"periods={[round(1 / f, 2) for f in freqs]} days")

        except EtmException as e:
            logger.debug(f"No periodic config in database: {e}")

    def _load_jump_config(self, cnn) -> None:
        """Load jump configuration from etm_params table"""
        from ..core.s_score import ScoreTable

        # @todo: analyze if "soln" = 'gamit' always or should also allow 'ppp'

        if self.solution.solution_type != SolutionType.NGL:
            sol = self.solution.solution_type.code
        else:
            sol = SolutionType.PPP.code

        query = '''
            SELECT "Year", "DOY", "action", "jump_type", "relaxation", "soln"
            FROM etm_params 
            WHERE "NetworkCode" = '%s' AND "StationCode" = '%s' 
            AND "object" = 'jump' AND "soln" = '%s' ORDER BY ("Year", "DOY")
        ''' % (self.network_code, self.station_code, sol)

        try:
            result = cnn.query_float(query, as_dict=True)

            if result:
                self.modeling.user_jumps = []
                # Store jump configuration for later use
                for jump in result:
                    if jump['jump_type'] == 1:
                        jump_type = JumpType.COSEISMIC_JUMP_DECAY
                    elif jump['jump_type'] == 2:
                        jump_type = JumpType.POSTSEISMIC_ONLY
                    else:
                        jump_type = JumpType.MECHANICAL_MANUAL

                    jump_params = JumpParameters(
                        jump_type=jump_type,
                        relaxation=jump['relaxation'],
                        date=Date(year=int(jump['Year']), doy=int(jump['DOY'])),
                        action=jump['action'])

                    self.modeling.user_jumps.append(jump_params)
            else:
                self.modeling.user_jumps = []

            logger.info(f"Loaded {len(self.modeling.user_jumps)} manual jump override(s) "
                       f"from etm_params for soln={sol}")

            if isinstance(self.modeling.post_seismic_back_lim, Date):
                sdate = self.modeling.post_seismic_back_lim
            else:
                sdate = self.metadata.first_obs - self.modeling.post_seismic_back_lim
            # now earthquakes
            # no information yet of data dates, load everything that is possible
            # NOTE: this window is anchored on metadata.first_obs/last_obs (stations.DateStart/
            # DateEnd) -- if those are wrong, earthquakes outside the resulting window are
            # dropped here and never reach JumpManager, regardless of magnitude_limit
            score = ScoreTable(cnn, self.network_code, self.station_code,
                               self.metadata.lat[0], self.metadata.lon[0],
                               sdate, self.metadata.last_obs,
                               magnitude_limit=self.modeling.earthquake_magnitude_limit,
                               force_events=self.modeling.earthquakes_cherry_picked)

            self.modeling.earthquake_jumps = score.table

            logger.info(f"Earthquake catalog window (post_seismic_back_lim={self.modeling.post_seismic_back_lim}): "
                       f"{len(self.modeling.earthquake_jumps)} candidate earthquake(s) found "
                       f"(magnitude_limit={self.modeling.earthquake_magnitude_limit})")

        except EtmException as e:
            logger.debug(f"No jump config in database: {e}")
            self.modeling.user_jumps = []

    def refresh_config(self, cnn: Cnn):
        self._load_jump_config(cnn)

    def get_label(self, key: str) -> str:
        """Get localized label"""
        return self._language_dict[self.language].get(key, key)

    def apply_custom_config(self, config: Dict[str, Any]) -> None:
        """Apply custom configuration overrides"""
        for section_name, section_config in config.items():
            if hasattr(self, section_name):
                section = getattr(self, section_name)
                for key, value in section_config.items():
                    if hasattr(section, key):
                        setattr(section, key, value)

    def validate_config(self) -> List[str]:
        """Validate configuration and return list of issues"""
        issues = []

        if self.modeling.poly_terms < 1:
            issues.append("Polynomial terms must be >= 1")

        if self.modeling.sigma_floor_h <= 0 or self.modeling.sigma_floor_v <= 0:
            issues.append("Sigma floor values must be > 0")

        if self.validation.min_solutions_for_etm < 2:
            issues.append("Minimum solutions for ETM must be >= 2")

        return issues

    def load_from_json(self, _json: Union[dict, str] = None):
        # load basic fields from json file
        data = load_json(_json)

        self.network_code = data['network_code']
        self.station_code = data['station_code']
        self.solution = SolutionOptions(**data['solution_options'])
        self.modeling = ModelingParameters(**data['modeling_params'])
        self.plotting_config = PlotOutputConfig()
        self.validation = ValidationRules()
        self.metadata = StationMetadata(**data['station_meta'])

