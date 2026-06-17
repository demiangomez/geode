from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("geode")
except PackageNotFoundError:
    # package is not installed
    __version__ = "0.0.0"

__all__ = [
    'clustering',
    'network',
    'pyRinexName',
    'Utils',
    'pyJobServer',
    'pyParseAntex',
    'pyStatic1d',
    'snxParse',
    'pyLeastSquares',
    'pyProducts',
    'metadata.station_info',
    'dbConnection',
    'pyDate',
    'pyOTL',
    'pyRinex',
    'pyArchiveStruct',
    'pyOkada',
    'ConvertRaw',
    'pyETM',
    'pyOptions',
    'pyRunWithRetry',
    'pyBunch',
    'pyEvents',
    'pyPPP',
    'gamit.ztd',
    'pyStack',
    'gamit.gamit_config',
    'gamit.gamit_session',
    'gamit.gamit_task',
    'gamit.globk_task',
    'gamit.parse_ztd',
    'pyStation'
]

from importlib import import_module


def __getattr__(name):
    if name in __all__:
        mod = import_module(f'.{name}', __name__)
        globals()[name] = mod
        return mod
    raise AttributeError(f"module 'geode' has no attribute {name!r}")
