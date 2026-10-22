"""
Project: Geodetic Database Engine (GeoDE)
Date: 9/21/25 4:54 PM
Author: Demian D. Gomez
"""

from enum import IntEnum, auto

from ..least_squares.ls_collocation import gaussian_func
from ..least_squares.ls_collocation import cauchy_func


class EtmException(Exception):
    pass


class FitStatus(IntEnum):
    """Enum for fit status"""
    PREFIT = auto()
    POSTFIT = auto()
    UNABLE_TO_FIT = auto()

    @property
    def description(self) -> str:
        descriptions = {
            FitStatus.PREFIT: 'Prefit',
            FitStatus.POSTFIT: 'Postfit',
            FitStatus.UNABLE_TO_FIT: 'Unable to fit data'
        }
        return descriptions.get(self, 'UNKNOWN')

class PeriodicStatus(IntEnum):
    """Enum for periodic term status"""
    AUTOMATICALLY_ADDED = auto()
    ADDED_BY_USER = auto()
    UNABLE_TO_FIT = auto()

    @property
    def description(self) -> str:
        descriptions = {
            PeriodicStatus.AUTOMATICALLY_ADDED: 'Automatic component',
            PeriodicStatus.ADDED_BY_USER: 'User component',
            PeriodicStatus.UNABLE_TO_FIT: 'Unable to fit component'
        }
        return descriptions.get(self, 'UNKNOWN')

    @property
    def code(self) -> str:
        code = {
            PeriodicStatus.AUTOMATICALLY_ADDED: 'A',
            PeriodicStatus.ADDED_BY_USER: 'R',
            PeriodicStatus.UNABLE_TO_FIT: 'D'
        }
        return code.get(self, 'A')

class EtmSolutionType(IntEnum):
    """Enum for jump types to replace scattered constants"""
    MODEL = auto()
    OBSERVATION = auto()
    @property
    def description(self) -> str:
        descriptions = {
            EtmSolutionType.MODEL: 'Solution from model',
            EtmSolutionType.OBSERVATION: 'Solution from direct observation'
        }
        return descriptions.get(self, 'UNKNOWN')


class JumpType(IntEnum):
    """Enum for jump types to replace scattered constants"""
    UNDETERMINED = -1
    MECHANICAL_MANUAL = 1
    MECHANICAL_ANTENNA = 2
    AUTO_DETECTED = 3
    REFERENCE_FRAME = 5
    COSEISMIC_JUMP_DECAY = 10
    COSEISMIC_ONLY = 15
    POSTSEISMIC_ONLY = 20

    @property
    def color(self) -> str:
        """Get the plotting color for this jump type"""
        color_map = {
            JumpType.UNDETERMINED: 'gray',
            JumpType.MECHANICAL_MANUAL: 'darkcyan',
            JumpType.MECHANICAL_ANTENNA: 'blue',
            JumpType.AUTO_DETECTED: 'darkorange',
            JumpType.REFERENCE_FRAME: 'green',
            JumpType.COSEISMIC_JUMP_DECAY: 'red',
            JumpType.COSEISMIC_ONLY: 'purple',
            JumpType.POSTSEISMIC_ONLY: 'orange'
        }
        return color_map.get(self, 'black')

    @property
    def description(self) -> str:
        descriptions = {
            JumpType.UNDETERMINED: 'UNDETERMINED',
            JumpType.MECHANICAL_MANUAL: 'MECHANICAL (MANUAL)',
            JumpType.MECHANICAL_ANTENNA: 'MECHANICAL (ANTENNA CHANGE)',
            JumpType.AUTO_DETECTED: 'AUTO DETECTED (UNKNOWN)',
            JumpType.REFERENCE_FRAME: 'REFERENCE FRAME CHANGE',
            JumpType.COSEISMIC_JUMP_DECAY: 'CO+POSTSEISMIC',
            JumpType.COSEISMIC_ONLY: 'COSEISMIC ONLY',
            JumpType.POSTSEISMIC_ONLY: 'POSTSEISMIC ONLY'
        }
        return descriptions.get(self, 'UNKNOWN')


class NoiseModels(IntEnum):
    """Enum for noise models"""
    WHITE_ONLY = auto()
    WHITE_FLICKER = auto()
    WHITE_FLICKER_RW = auto()


class AdjustmentModels(IntEnum):
    """Enum for adjustment models"""
    ROBUST_LEAST_SQUARES = auto()
    LSQ_COLLOCATION = auto()
    NOISE_COVARIANCE_GLS = auto()
    @property
    def description(self) -> str:
        descriptions = {
            AdjustmentModels.ROBUST_LEAST_SQUARES: 'Robust Least Squares',
            AdjustmentModels.LSQ_COLLOCATION: 'Least Squares Collocation',
            AdjustmentModels.NOISE_COVARIANCE_GLS: 'GLS with power-law + periodic noise covariance'
        }
        return descriptions.get(self, 'UNKNOWN')


class CovarianceFunction(IntEnum):
    """Enum for covariance models"""
    GAUSSIAN = auto()
    CAUCHY = auto()
    ARMA = auto()

    @property
    def description(self) -> str:
        descriptions = {
            CovarianceFunction.GAUSSIAN: 'Gaussian Covariance Function',
            CovarianceFunction.CAUCHY: 'Cauchy Covariance Function',
            CovarianceFunction.ARMA: 'Autoregressive Moving Average Covariance Function'
        }
        return descriptions.get(self, 'UNKNOWN')

    @property
    def get_function(self):
        functions = {
            CovarianceFunction.GAUSSIAN: gaussian_func,
            CovarianceFunction.CAUCHY: cauchy_func,
            CovarianceFunction.ARMA: gaussian_func # this function is the default in case ARMA fails
        }
        return functions.get(self, None)

class SolutionType(IntEnum):
    """Enum for noise models"""
    GAMIT = auto()
    PPP = auto()
    NGL = auto()
    DRA = auto()

    @classmethod
    def from_code(cls, code: str) -> 'SolutionType':
        for member in cls:
            if member.code == code:
                return member
        return SolutionType.GAMIT

    @property
    def description(self) -> str:
        descriptions = {
            SolutionType.GAMIT: 'GAMIT: GNSS at MIT',
            SolutionType.PPP: 'Precise Point Positioning',
            SolutionType.NGL: 'Nevada Geodetic Laboratory (GipsyX)',
            SolutionType.DRA: 'Daily repeatability analysis (DRA)'
        }
        return descriptions.get(self, 'UNKNOWN')

    @property
    def code(self) -> str:
        code = {
            SolutionType.GAMIT: 'gamit',
            SolutionType.PPP: 'ppp',
            SolutionType.NGL: 'ngl',
            SolutionType.DRA: 'dra'
        }
        return code.get(self, 'UNKNOWN')