"""
Fault geometry and Okada dislocation model for coseismic constraints.
"""

from dataclasses import dataclass
from typing import List, Tuple, TYPE_CHECKING
import numpy as np
from tqdm import tqdm

from ....Utils import stationID, azimuthal_equidistant
from ....elasticity.elastic_interpolation import get_qpw, get_radius
from ...core.data_classes import Earthquake

if TYPE_CHECKING:
    from ..grid_system import GridSystem
    from ..data_classes import Station


@dataclass
class PatchGrid:
    """Fault patch discretization."""
    grid_dd: np.ndarray       # downdip coordinates in fault system [km]
    grid_ss: np.ndarray       # along-strike coordinates in fault system [km]
    grid_dep: List[np.ndarray]  # depths for each dip angle [km]
    radius_W: float           # half-size along down-dip direction [km]
    radius_L: float           # half-size along strike direction [km]
    n_patches: int            # number of patches


class FaultGeometry:
    """
    Manages fault geometry and Okada dislocation model computations.

    Handles:
    - Fault dimension estimation from magnitude (Wells & Coppersmith)
    - Patch grid computation with separate discretizations for horizontal/vertical
    - Fault plane determination (nodal plane selection)
    - Okada response matrix computation
    """

    def __init__(self, event: Earthquake, stations: List['Station'], relaxation: float, grid: 'GridSystem'):
        """
        Initialize fault geometry from earthquake and station list.

        Parameters
        ----------
        event : Earthquake
            Earthquake with magnitude, location, and focal mechanism
        stations : List[Station]
            Stations with coseismic observations
        """
        self.event = event

        # Fault dimensions from Wells & Coppersmith (1994), inflated for safety
        self.along_strike = 10. ** (-3.22 + 0.69 * event.magnitude) * 1.2  # [km] (inflate 20%)
        self.down_dip = 10. ** (-1.01 + 0.32 * event.magnitude) * 1.6      # [km] (inflate 60%)

        # Separate patch grids for horizontal and vertical
        # Horizontal has 2*N observations, vertical has N observations
        self.patches_h: PatchGrid = None
        self.patches_v: PatchGrid = None

        # Restrict to stations that actually have a jump for this event.
        # Building le/ln/lu with `if j` but indexing with full-list positions causes
        # an IndexError whenever stations without jumps are present (the compressed
        # array is shorter than the index values from the full list).

        self.station_list = [stn for stn in stations if stn.get_coseismic_column(event.id) is not None]
        # if not enough coseismic observations, choose postseismic with the largest relaxation value
        if len(self.station_list) < 2:
            self.station_list = [stn for stn in stations if stn.get_postseismic_column(event.id, relaxation) is not None]

        if len(self.station_list) < 2:
            # 0 stations  → nothing to fit.
            # 1 station   → NN-distance matrix diagonal is forced to inf,
            #               making local_reg = inf and get_qpw return NaN;
            #               plane comparison is impossible.
            # In both cases default to plane 0 and skip the solve.
            msg = ('No stations have' if not self.station_list
                   else 'Only 1 station has')
            tqdm.write(f'  WARNING: {msg} a jump for {self.event.id}; '
                       f'cannot determine plane — defaulting to plane 0')
            self.plane = 0
            self._compute_patch_grids(len(self.station_list) if self.station_list else 1)
            self.station_list = [stationID(stn) for stn in self.station_list]
        else:
            jumps = [stn.etm.jump_manager.get_geophysical_jump(self.event.id) for stn in self.station_list]

            # Selected fault plane (0 or 1)
            self.plane = None
            self._compute_patch_grids(len(self.station_list))

            # get the postseismic mask which is used to compute the scale length for the spline interpolation
            mask = grid.earthquake_masks[event.id][1]
            self.determine_plane(self.station_list, jumps, grid, mask, 0.1)

    def _compute_patch_grids(self, n_stations: int):
        """
        Compute separate patch grids for horizontal and vertical components.

        Horizontal: 2*N observations -> n_patches = floor(2*N/4) = floor(N/2)
        Vertical:   N observations   -> n_patches = floor(N/4)

        Each patch has 2 slip components (strike-slip, dip-slip), so the Okada
        matrix has 2*n_patches columns. For well-conditioned inversion, need
        observations > 2*n_patches.
        """
        # Horizontal: 2 observations per station (east, north)
        n_patches_h = max(1, int(np.floor(n_stations / 2)))
        # Vertical: 1 observation per station
        n_patches_v = max(1, int(np.floor(n_stations / 4)))

        tqdm.write(f'Event {self.event.id} {self.event.date.yyyyddd()}: {n_stations} stations -> '
                   f'{n_patches_h} horizontal patches, {n_patches_v} vertical patches')
        tqdm.write(f'  Fault dimensions: AS={self.along_strike:.1f} km, DD={self.down_dip:.1f} km')

        self.patches_h = self._build_patch_grid(n_patches_h)
        self.patches_v = self._build_patch_grid(n_patches_v)

    def _build_patch_grid(self, n_patches: int, verbose: bool = True) -> PatchGrid:
        """
        Build a fault patch grid with a regular rectangular layout.

        Matches MATLAB's okada_grid approach: divide the fault into a
        ps × ps regular grid where ps = floor(sqrt(n_patches)).  Each
        patch is a rectangle covering its cell exactly, so the patches
        tile the fault without overlap or gap.

        Parameters
        ----------
        n_patches : int
            Target number of patches (actual count = ps*ps)

        Returns
        -------
        PatchGrid
            Patch grid with coordinates and depths
        """
        sind = lambda x: np.sin(np.deg2rad(x))

        # Fault boundaries in fault coordinate system
        L1 = -self.along_strike / 2;  L2 = -L1   # along-strike
        W1 = -self.down_dip / 2;      W2 = -W1   # down-dip

        # Square patch layout: ps patches per side (matches MATLAB)
        ps = max(1, int(np.floor(np.sqrt(n_patches))))
        actual_patches = ps * ps

        L_edges = np.linspace(L1, L2, ps + 1)
        W_edges = np.linspace(W1, W2, ps + 1)

        # Patch centres: stride over strike first, then dip
        stk_centers = (L_edges[:-1] + L_edges[1:]) / 2   # (ps,)
        dd_centers  = (W_edges[:-1] + W_edges[1:]) / 2   # (ps,)
        grid_ss, grid_dd = np.meshgrid(stk_centers, dd_centers)
        grid_ss = grid_ss.ravel()
        grid_dd = grid_dd.ravel()

        radius_L = (L2 - L1) / (2 * ps)   # half-width along strike
        radius_W = (W2 - W1) / (2 * ps)   # half-width down dip

        if verbose:
            tqdm.write(f'    Rectangular patch grid: {ps}×{ps} = {actual_patches} patches '
                       f'(ΔL={2*radius_L:.1f} km, ΔW={2*radius_W:.1f} km)')

        # Compute depths for each possible dip angle
        grid_dep = []
        for dip in self.event.dip:
            fault_dep = self.event.depth + grid_dd * sind(dip)

            # Ensure no patches stick out of the ground
            if np.any(fault_dep.round() <= 0):
                min_dep = fault_dep.min()
                adjustment = min_dep - radius_W * sind(dip) - 1.
                fault_dep = fault_dep - adjustment
                # tqdm.write(f'    Adjusted depths for dip={dip}: shift={-adjustment:.1f} km')

            grid_dep.append(fault_dep)

        return PatchGrid(
            grid_dd=grid_dd,
            grid_ss=grid_ss,
            grid_dep=grid_dep,
            radius_W=radius_W,
            radius_L=radius_L,
            n_patches=actual_patches
        )

    def determine_plane(self, stations: List['Station'], jumps, grid: 'GridSystem',
                        mask: np.ndarray, spline_tension: float) -> Tuple[np.ndarray, np.ndarray]:
        """
        Determine which nodal plane best fits the observations.

        Tests both fault planes and selects the one with lower horizontal RMS.

        Parameters
        ----------
        stations : List[Station]
            Stations with coseismic observations
        grid : GridSystem
            Grid system for coordinate transforms
        mask : np.ndarray
            Boolean mask for active grid points
        spline_tension : float
            Spline tension parameter for vertical interpolation

        Returns
        -------
        Tuple[np.ndarray, np.ndarray]
            Design matrix (a) and regularization matrix (p) for selected plane
        """
        from scipy.linalg import block_diag

        def rms_horizontal(residuals: np.ndarray, n_stations: int) -> float:
            """Compute RMS of horizontal (east + north) residuals only."""
            return np.sqrt(np.mean(residuals[:2*n_stations]**2))

        sites_lon = np.array([stn.lon for stn in stations])
        sites_lat = np.array([stn.lat for stn in stations])
        le = np.array([j.p.params[1][0] for j in jumps])
        ln = np.array([j.p.params[0][0] for j in jumps])
        lu = np.array([j.p.params[2][0] for j in jumps])
        obs = np.concatenate([le.ravel(), ln.ravel(), lu.ravel()])

        sites_nam = [stationID(stn) for stn in stations]
        N = len(stations)

        # Filter stations for plane determination: all three sigma components < 0.001 m
        sigma_threshold = 0.001
        keep_idx = []
        filtered_sites = []
        for idx, j in enumerate(jumps):
            s_n = j.p.sigmas[0][0] if len(j.p.sigmas[0]) > 0 else np.inf
            s_e = j.p.sigmas[1][0] if len(j.p.sigmas[1]) > 0 else np.inf
            s_u = j.p.sigmas[2][0] if len(j.p.sigmas[2]) > 0 else np.inf
            if s_n < sigma_threshold and s_e < sigma_threshold and s_u < sigma_threshold:
                keep_idx.append(idx)
            else:
                filtered_sites.append(sites_nam[idx])

        if filtered_sites:
            tqdm.write(f'  Stations excluded from plane determination '
                       f'(sigma >= {sigma_threshold:.3f} m in >= 1 component): '
                       + ', '.join(filtered_sites))

        if len(keep_idx) < 2:
            # A single station produces a 1×1 NN-distance matrix whose diagonal
            # is forced to inf, making local_reg = inf and get_qpw return NaN.
            # At least 2 stations are needed for a finite nearest-neighbour offset
            # and a meaningful plane RMS comparison.
            if keep_idx:
                tqdm.write(f'  WARNING: Only {len(keep_idx)} station passes the sigma filter '
                           f'(minimum 2 required); using all stations for plane determination')
            else:
                tqdm.write('  WARNING: No stations pass the sigma filter; '
                           'using all stations for plane determination')
            keep_idx = list(range(N))

        keep_idx = np.array(keep_idx)
        N_filt = len(keep_idx)
        sites_lon_filt = sites_lon[keep_idx]
        sites_lat_filt = sites_lat[keep_idx]
        obs_filt = np.concatenate([le[keep_idx], ln[keep_idx], lu[keep_idx]])

        tqdm.write(f'Testing fault planes for {self.event.id} with {N} stations '
                   f'({N_filt} used for plane determination)')

        a_list = []
        p_list = []
        v_list = []
        m_list = []
        rms_list = []

        # Test both fault planes
        for i in range(2):
            strike, dip = self.event.strike[i], self.event.dip[i]
            tqdm.write(f'  Plane {i}: strike={strike:.1f}, dip={dip:.1f}')

            # Full system for all stations: used for table printout and return value
            # call with default Okada weight to ensure compliance with Okada
            a, p = self._compute_sw_okada_system(
                grid, sites_lon, sites_lat, strike, dip, mask, spline_tension
            )

            # Full solve for table printout
            n_mat = a.T @ a + p
            x = np.linalg.solve(n_mat, a.T @ obs)
            fitted = a @ x
            v = obs - fitted

            # Filtered system for plane selection: only reliable stations contribute
            a_filt, p_filt = self._compute_sw_okada_system(
                grid, sites_lon_filt, sites_lat_filt, strike, dip, mask, spline_tension
            )
            n_mat_filt = a_filt.T @ a_filt + p_filt
            x_filt = np.linalg.solve(n_mat_filt, a_filt.T @ obs_filt)
            rms_h = rms_horizontal(obs_filt - a_filt @ x_filt, N_filt)

            a_list.append(a)
            p_list.append(p)
            v_list.append(v)
            m_list.append(fitted)
            rms_list.append(rms_h)

        # Print observed / modeled / residual table
        obs_mat = obs.reshape((3, N))   # rows: E, N, U; cols: stations
        s0, d0 = self.event.strike[0], self.event.dip[0]
        s1, d1 = self.event.strike[1], self.event.dip[1]
        col_w = 7

        # plane_label_width = mod_s width (3*col_w+2) + '   ' (3) + res_s width (3*col_w+2) = 49
        plane_label_width = 3 * col_w + 2 + 3 + 3 * col_w + 2
        hdr1 = (f'  {"Station":<12} | {"Observed [mm]":^23} '
                f'| {f"Plane 0  str={s0:.1f}  dip={d0:.1f}":^{plane_label_width}} '
                f'| {f"Plane 1  str={s1:.1f}  dip={d1:.1f}":^{plane_label_width}}')
        hdr2 = (f'  {"":12} | {"E":>{col_w}} {"N":>{col_w}} {"U":>{col_w}} '
                f'| {"Mod E":>{col_w}} {"Mod N":>{col_w}} {"Mod U":>{col_w}} '
                f'  {"Res E":>{col_w}} {"Res N":>{col_w}} {"Res U":>{col_w}} '
                f'| {"Mod E":>{col_w}} {"Mod N":>{col_w}} {"Mod U":>{col_w}} '
                f'  {"Res E":>{col_w}} {"Res N":>{col_w}} {"Res U":>{col_w}}')
        tqdm.write(hdr1)
        tqdm.write(hdr2)

        for i in range(N):
            obs_s = ' '.join([f'{obs_mat[j, i] * 1000.:>{col_w}.1f}' for j in range(3)])
            plane_parts = []
            for k in range(2):
                m_mat = m_list[k].reshape((3, N))
                v_mat = v_list[k].reshape((3, N))
                mod_s = ' '.join([f'{m_mat[j, i] * 1000.:>{col_w}.1f}' for j in range(3)])
                res_s = ' '.join([f'{v_mat[j, i] * 1000.:>{col_w}.1f}' for j in range(3)])
                plane_parts.append(f'{mod_s}   {res_s}')
            tqdm.write(f'  {sites_nam[i]:<12} | {obs_s} | {plane_parts[0]} | {plane_parts[1]}')

        # Select plane with lower horizontal RMS
        self.plane = int(np.argmin(rms_list))
        self.station_list = sites_nam

        tqdm.write(f'Horizontal RMS: plane0={rms_list[0]*1000:.2f}mm, plane1={rms_list[1]*1000:.2f}mm')
        tqdm.write(f'Selected plane {self.plane}: strike={self.event.strike[self.plane]:.1f}, '
                   f'dip={self.event.dip[self.plane]:.1f}')

        return a_list[self.plane], p_list[self.plane]

    def _compute_sw_okada_system(self, grid: 'GridSystem',
                                  stnlon: np.ndarray, stnlat: np.ndarray,
                                  strike: float, dip: float,
                                  mask: np.ndarray, spline_tension: float,
                                  h_weight: float = 10,
                                  v_weight: float = None) -> Tuple[np.ndarray, np.ndarray]:
        """
        Compute the SW-Okada constrained interpolation system.

        Builds:
        - Design matrix a = block_diag(ah, av) for SW (horizontal) and spline (vertical)
        - Regularization matrix p = block_diag(P_h, P_z) with Okada annihilator constraints

        Parameters
        ----------
        grid : GridSystem
            Grid system with interpolation parameters
        stnlon, stnlat : np.ndarray
            Station coordinates
        strike, dip : float
            Fault plane parameters
        mask : np.ndarray
            Boolean mask for active grid points
        spline_tension : float
            Spline tension parameter
        h_weight, v_weight : float
            Weights for horizontal and vertical Okada constraints

        Returns
        -------
        Tuple[np.ndarray, np.ndarray]
            Design matrix (a) and regularization matrix (p)
        """
        from geode.pyOkada import okada
        from scipy.linalg import block_diag

        sind = lambda x: np.sin(np.deg2rad(x))
        cosd = lambda x: np.cos(np.deg2rad(x))

        N = len(stnlon)

        # Project stations relative to the earthquake EPICENTER, not the stacker grid origin.
        # The Okada function positions the fault at (X=0, Y=0), so station coordinates must
        # be relative to the epicenter. Using grid.origin (mean of all stacker stations,
        # potentially hundreds of km away) shifts all stations far from the fault and
        # makes the Okada response matrix near-zero.
        x, y = azimuthal_equidistant(
            np.array(self.event.lon), np.array(self.event.lat), stnlon, stnlat
        )

        # Compute local SW regularization offset from event-specific stations only.
        # Matches MATLAB: reg = max(8, median_nearest_neighbour * 0.5)
        # grid.offset is computed from all stacker stations and would be far too large here.
        # When N < 2 (leave-one-out with only 1 remaining station), the nearest-neighbour
        # distance is undefined (diagonal = inf after fill) and median([inf]) = inf, which
        # propagates as NaN through get_qpw.  Fall back to the minimum safe offset.
        if N >= 2:
            r_ev, _, _ = get_radius(np.column_stack([x, y]), np.column_stack([x, y]))
            np.fill_diagonal(r_ev, np.inf)
            local_reg = max(8.0, float(np.median(r_ev.min(axis=1))) * 0.5)
        else:
            local_reg = 8.0
        # tqdm.write(f'  SW local regularization: {local_reg:.1f} km  (global grid offset: {grid.offset:.1f} km)')

        q, p, w = get_qpw(np.column_stack([x, y]), np.column_stack([x, y]), local_reg, grid.poisson_ratio)
        ah = np.block([[q, w], [w, p]])

        # Compute spline design matrix for vertical
        _, av = grid._compute_spline_responses(
            np.array([x, y]),
            grid.interpolation_grid[0][mask],
            grid.interpolation_grid[1][mask],
            spline_tension
        )

        # Joint design matrix
        a = block_diag(ah, av)

        # Transform stations to fault coordinate system
        R = np.array([[ cosd(90 - strike), sind(90 - strike)],
                      [-sind(90 - strike), cosd(90 - strike)]])
        T = R @ np.array([x, y])
        tx, ty = T[0, :], T[1, :]

        # Build patch grids sized for the current N so D_ok is always tall
        # (2*N rows, ~N cols).  Using self.patches_h, which is sized for the
        # full coseismic station count, would make D_ok fat when N_other <
        # N_coseismic/2, collapsing Pok to zero and killing the Okada weight.
        local_patches_h = self._build_patch_grid(max(1, int(np.floor(N / 2))), verbose=False)
        local_patches_v = self._build_patch_grid(max(1, int(np.floor(N / 4))), verbose=False)

        # Compute Okada response matrices using local patch grids
        D_ok = self._compute_okada_horizontal(tx, ty, dip, grid.poisson_ratio, patches=local_patches_h)
        D_oz = self._compute_okada_vertical(tx, ty, dip, grid.poisson_ratio, patches=local_patches_v)

        # Rotate horizontal responses back to geographic coordinates
        D_ok = self._rotate_okada_horizontal(D_ok, R, N)

        # Normalize columns
        norms_ok = np.linalg.norm(D_ok, axis=0)
        norms_oz = np.linalg.norm(D_oz, axis=0)
        # Avoid division by zero
        norms_ok[norms_ok == 0] = 1
        norms_oz[norms_oz == 0] = 1
        D_ok = D_ok / norms_ok
        D_oz = D_oz / norms_oz

        # Compute annihilator matrices (use pinv for rank-deficient cases)
        Pok = np.eye(2 * N) - D_ok @ np.linalg.pinv(D_ok)
        Poz = np.eye(N) - D_oz @ np.linalg.pinv(D_oz)

        # Auto-scale weights so that h/v_weight=1 gives an Okada regularization
        # term equal in Frobenius norm to the corresponding data term.  This makes
        # both weights interpretable as dimensionless relative strengths regardless
        # of station count, fault size, or Green's function scale.  Without this,
        # h_weight and v_weight are raw multipliers whose effect depends entirely on
        # the accident of matrix norms, making tuning unpredictable.
        term_ah  = np.linalg.norm(ah.T @ ah,       'fro')
        term_Pok = np.linalg.norm(ah.T @ Pok @ ah, 'fro')
        term_av  = np.linalg.norm(av.T @ av,        'fro')
        term_Poz = np.linalg.norm(av.T @ Poz @ av,  'fro')

        h_weight_eff = h_weight                          * term_ah / max(term_Pok, 1e-10)
        v_weight_eff = (5.0 if v_weight is None else v_weight) * term_av / max(term_Poz, 1e-10)

        # Force balance constraint: sum of alphas = 0 (field decays at infinity)
        C_z = np.ones((1, N))
        mu_z = 1e4 * term_av / N

        # Build regularization matrices
        P_h = h_weight_eff * ah.T @ Pok @ ah
        P_z = v_weight_eff * av.T @ Poz @ av + mu_z * C_z.T @ C_z

        return a, block_diag(P_h, P_z)

    def _compute_okada_horizontal(self, tx: np.ndarray, ty: np.ndarray,
                                   dip: float, poisson_ratio: float,
                                   patches: 'PatchGrid' = None) -> np.ndarray:
        """
        Compute Okada horizontal displacement matrix using horizontal patch grid.

        Parameters
        ----------
        tx, ty : np.ndarray
            Station coordinates in fault system
        dip : float
            Fault dip angle
        poisson_ratio : float
            Poisson ratio (alpha = mu/(lambda+mu)) passed to the Okada kernel

        Returns
        -------
        np.ndarray
            Displacement matrix (2*N, 2*n_patches_h) in fault coordinates
        """
        from geode.pyOkada import okada

        sind = lambda x: np.sin(np.deg2rad(x))
        cosd = lambda x: np.cos(np.deg2rad(x))

        N = len(tx)
        if patches is None:
            patches = self.patches_h
        n_patches = patches.n_patches

        # D_ok: (2*N, 2*n_patches) - [ux; uy] for each slip component
        D_ok = np.zeros((2 * N, 2 * n_patches))

        # Okada depth is the reference depth of the coordinate system (event depth),
        # not the depth of each patch. L1,L2,W1,W2 define patch geometry relative to this.
        depth = self.event.depth

        for i, (dd, stk) in enumerate(zip(patches.grid_dd, patches.grid_ss)):
            L1 = stk - patches.radius_L
            L2 = stk + patches.radius_L
            W1 = dd  - patches.radius_W
            W2 = dd  + patches.radius_W

            # Strike-slip component
            ux, uy, _ = okada(poisson_ratio, tx, ty, depth,
                              L1, L2, W1, W2, sind(dip), cosd(dip), 1, 0, 0)
            D_ok[:N, 2*i + 1] = np.atleast_1d(ux)
            D_ok[N:, 2*i + 1] = np.atleast_1d(uy)

            # Dip-slip component
            ux, uy, _ = okada(poisson_ratio, tx, ty, depth,
                              L1, L2, W1, W2, sind(dip), cosd(dip), 0, 1, 0)
            D_ok[:N, 2*i] = np.atleast_1d(ux)
            D_ok[N:, 2*i] = np.atleast_1d(uy)

        return D_ok

    def _compute_okada_vertical(self, tx: np.ndarray, ty: np.ndarray,
                                 dip: float, poisson_ratio: float,
                                 patches: 'PatchGrid' = None) -> np.ndarray:
        """
        Compute Okada vertical displacement matrix using vertical patch grid.

        Parameters
        ----------
        tx, ty : np.ndarray
            Station coordinates in fault system
        dip : float
            Fault dip angle
        poisson_ratio : float
            Poisson ratio (alpha = mu/(lambda+mu)) passed to the Okada kernel

        Returns
        -------
        np.ndarray
            Displacement matrix (N, 2*n_patches_v)
        """
        from geode.pyOkada import okada

        sind = lambda x: np.sin(np.deg2rad(x))
        cosd = lambda x: np.cos(np.deg2rad(x))

        N = len(tx)
        if patches is None:
            patches = self.patches_v
        n_patches = patches.n_patches

        # D_oz: (N, 2*n_patches)
        D_oz = np.zeros((N, 2 * n_patches))

        # Okada depth is the reference depth of the coordinate system (event depth),
        # not the depth of each patch. L1,L2,W1,W2 define patch geometry relative to this.
        depth = self.event.depth

        for i, (dd, stk) in enumerate(zip(patches.grid_dd, patches.grid_ss)):
            L1 = stk - patches.radius_L
            L2 = stk + patches.radius_L
            W1 = dd  - patches.radius_W
            W2 = dd  + patches.radius_W

            # Strike-slip component
            _, _, uz = okada(poisson_ratio, tx, ty, depth,
                             L1, L2, W1, W2, sind(dip), cosd(dip), 1, 0, 0)
            D_oz[:, 2*i + 1] = np.atleast_1d(uz)

            # Dip-slip component
            _, _, uz = okada(poisson_ratio, tx, ty, depth,
                             L1, L2, W1, W2, sind(dip), cosd(dip), 0, 1, 0)
            D_oz[:, 2*i] = np.atleast_1d(uz)

        return D_oz

    def _rotate_okada_horizontal(self, D_ok: np.ndarray, R: np.ndarray,
                                  N: int) -> np.ndarray:
        """
        Rotate Okada horizontal displacements from fault to geographic coordinates.

        Parameters
        ----------
        D_ok : np.ndarray
            Displacement matrix in fault coordinates (2*N, 2*n_patches)
        R : np.ndarray
            Rotation matrix (fault -> geographic)
        N : int
            Number of stations

        Returns
        -------
        np.ndarray
            Displacement matrix in geographic coordinates
        """
        n_cols = D_ok.shape[1]
        D_rotated = np.zeros_like(D_ok)

        for col in range(n_cols):
            ux = D_ok[:N, col]
            uy = D_ok[N:, col]
            rotated = R.T @ np.vstack([ux, uy])
            D_rotated[:N, col] = rotated[0, :]
            D_rotated[N:, col] = rotated[1, :]

        return D_rotated

    def get_strike_dip(self) -> Tuple[float, float]:
        """Get strike and dip of selected fault plane."""
        if self.plane is None:
            raise ValueError("Fault plane not yet determined")
        return self.event.strike[self.plane], self.event.dip[self.plane]
