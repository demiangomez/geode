"""
In-process wrapper around gnssmultipath.GNSS_MultipathAnalysis.

Only this module imports gnssmultipath; everything downstream
(signals.py, aggregate.py) works on the res dict it returns. See
knowledge/decisions/0003-gnssmultipath-dependency.md for what that
dict actually contains -- no CSV round-trip is needed (hence
write_results_to_csv=False below), azimuth is available for broadcast-nav
runs too, and there is no pre-binned "slips by elevation band" (aggregate.py
derives that from slip periods + elevation arrays).
"""
import shutil
import tempfile
import time

from gnssmultipath import GNSS_MultipathAnalysis


class RunnerResult:
    def __init__(self, res, runtime_s, out_dir):
        self.res       = res
        self.runtime_s = runtime_s
        self.out_dir   = out_dir  # None unless keep_tool_outputs was set


def run(rinex_path, nav_paths=(), sp3_paths=(), systems=(), elev_cutoff_deg=10.0,
       work_dir=None, keep_tool_outputs=False):
    """
    Run gnssmultipath on rinex_path.

    :param nav_paths: up to 4 broadcast navigation file paths.
    :param sp3_paths: up to 3 SP3 file paths. Exactly one of nav_paths/
        sp3_paths should be non-empty (orbits.py enforces this).
    :param systems: single-char GNSS system codes to restrict to, or ()
        for "all systems observed in the file".
    :return: a RunnerResult. Raises whatever gnssmultipath itself raises
        on a genuinely unrecoverable file -- the caller
        (geode.rinex_qc.pipeline) converts that into a failed run record,
        never aborting a batch.
    """
    out_dir = tempfile.mkdtemp(prefix='rinexqc_', dir=work_dir)

    nav_args = {'broadcastNav%d' % i: path for i, path in enumerate(nav_paths[:4], start=1)}
    sp3_args = {'sp3NavFilename_%d' % i: path for i, path in enumerate(sp3_paths[:3], start=1)}

    t0 = time.time()
    try:
        res = GNSS_MultipathAnalysis(
            rinex_path,
            desiredGNSSsystems=list(systems) if systems else None,
            cutoff_elevation_angle=elev_cutoff_deg,
            outputDir=out_dir,
            plotEstimates=False,
            plot_polarplot=False,
            include_SNR=True,
            save_results_as_pickle=False,
            save_results_as_compressed_pickle=False,
            write_results_to_csv=False,
            includeResultSummary=False,
            includeCompactSummary=False,
            includeObservationOverview=False,
            includeLLIOverview=False,
            use_LaTex=False,
            **nav_args,
            **sp3_args,
        )
    finally:
        if not keep_tool_outputs:
            shutil.rmtree(out_dir, ignore_errors=True)

    runtime_s = time.time() - t0
    return RunnerResult(res, runtime_s, out_dir if keep_tool_outputs else None)
