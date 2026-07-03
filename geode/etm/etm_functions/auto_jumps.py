"""
Project: Geodetic Database Engine (GeoDE)
Date: 9/29/25 12:12 PM
Author: Demian D. Gomez
Modified from
# Author:  Michael Heflin
# Date:  September 12, 2014
# Organization:  JPL, Caltech
"""
from typing import List
from sklearn.cluster import DBSCAN
import numpy as np
import logging

logger = logging.getLogger(__name__)

from ..core.etm_config import EtmConfig
from ..core.type_declarations import JumpType
from ..etm_functions.jumps import JumpFunction
from ...pyDate import Date


class AutoJumps:
    """
    Class to find jumps automatically
    """
    def __init__(self, config: EtmConfig,
                 method: str = 'angry'):
        self.config = config
        self.jumps = []
        self.method = method

        logger.info('Detecting jumps using method ' + method)

    def detect(self, time_vector: np.ndarray, observations: List[np.ndarray]):
        # `observations` come from SolutionData.transform_to_local(), which already
        # applies the fit-window mask, so it can be shorter than the full time_vector
        # (the caller passes the full vector so JumpFunction design matrices built
        # elsewhere stay consistently shaped). Window time_vector the same way here
        # so the break-detection regression sees matching lengths, but keep the full
        # time_vector around to build the resulting JumpFunction objects.
        mask = self.config.modeling.get_observation_mask(time_vector)
        windowed_time_vector = time_vector[mask]

        if self.method.lower() == 'angry':
            self._angry_search(windowed_time_vector, observations, time_vector)
        elif self.method.lower() == 'dbscan':
            self._dbscan(windowed_time_vector, observations, time_vector)
        else:
            raise TypeError('auto detection method not implemented')

        return self.jumps

    def _angry_search(self, time_vector: np.ndarray,
                      observations: List[np.ndarray],
                      full_time_vector: np.ndarray,
                      ftest: float = 300.):
        # Initialize
        I = []
        L = []
        Z = None

        N, E, U = observations

        # Initialize parameters
        ndat = N.shape[0]
        I.append(ndat - 1)
        A = np.zeros((ndat, 2))
        A[0:ndat, 0] = np.ones(ndat)
        A[0:ndat, 1] = time_vector - time_vector.min()
        B = np.zeros((ndat, 3))
        B[0:ndat, 0] = N
        B[0:ndat, 1] = E
        B[0:ndat, 2] = U
        a, b, c, d = np.linalg.lstsq(A, B, rcond=-1)
        c0 = b[0] + b[1] + b[2] / 4
        p0 = 2
        cmax = c0
        imax = 0
        # Search for breaks
        search = 1
        p1 = p0 + 1
        while search:
            A = np.hstack((A, np.zeros((ndat, 1))))
            fmax = 0
            for i in range(0, ndat):
                A[i, p1 - 1] = 1
                fit = 1
                for j in range(0, len(I)):
                    if i == I[j]:
                        fit = 0
                if fit == 1:
                    a, b, c, d = np.linalg.lstsq(A, B, rcond=-1)
                    c1 = b[0] + b[1] + b[2] / 4
                    f = ((c0 - c1) / c1) * ((ndat - p1) / (p1 - p0))
                    if f > fmax:
                        imax = i
                        fmax = f
                        cmax = c1
                        Z = np.copy(A)

            if fmax > float(ftest):
                p0 = p1
                p1 = p1 + 1
                c0 = cmax
                I.append(imax)
                A = np.copy(Z)
                L.append(time_vector[imax + 1])
            else:
                search = 0

        # Sort breaks
        L.sort(key=float)

        for j in L:
            self.jumps.append(JumpFunction(
                self.config,
                metadata='auto-jump',
                time_vector=full_time_vector,
                date=Date(fyear=j),
                jump_type=JumpType.AUTO_DETECTED
            ))

    def _dbscan(self, time_vector: np.ndarray, observations: List[np.ndarray],
               full_time_vector: np.ndarray, eps_value: float = 0.003):
        # scale the time to match the scale of the gnss positions
        time_scaled = (time_vector - time_vector.min()) / (1 / 365.25) * 0.0001

        l = observations

        E_data = np.column_stack((time_scaled, l[1]))
        N_data = np.column_stack((time_scaled, l[0]))

        # Apply DBSCAN clustering with tuned parameters
        min_samples_value = 15  # value from tests

        dbscan = DBSCAN(eps=eps_value, min_samples=min_samples_value)
        e_cluster_labels = dbscan.fit_predict(E_data)
        n_cluster_labels = dbscan.fit_predict(N_data)

        # from the detected jumps, remove duplicates with a +- 2 day difference
        jump_times = self._find_sets(self._find_jumps(e_cluster_labels, time_vector) +
                                              self._find_jumps(n_cluster_labels, time_vector))
        jump_times.sort()

        for j in jump_times:
            self.jumps.append(JumpFunction(
                self.config,
                metadata='auto-jump',
                time_vector=full_time_vector,
                date=Date(fyear=j),
                jump_type=JumpType.AUTO_DETECTED
            ))

    def _find_jumps(self, cluster_labels, time):

        jump_times = []
        p_cluster = 0
        p_time = 0
        for i in range(1, len(cluster_labels)):
            if cluster_labels[i] != -1:
                if cluster_labels[i] != p_cluster:
                    if time[i] - p_time > 20 / 365.25:
                        # only allow jump if more than 20 days have passed since last jump
                        jump_times.append(time[i])
                    p_cluster = cluster_labels[i]
                    p_time = time[i]

        return jump_times

    def _find_sets(self, numbers, tolerance=2 / 365.25):
        """
        Finds sets of jumps within a given tolerance in day (as fyear).

        Args:
            numbers (list): List of numbers to group into sets.
            tolerance (float): The maximum allowed difference between two numbers in a set (default 2 days).

        Returns:
            list: set of unique values.
        """

        set = []
        for number in numbers:
            found_set = False
            for i, set_ in enumerate(set):
                if abs(number - set_) <= tolerance:
                    found_set = True
                    break
            if not found_set:
                set.append(number)

        return set