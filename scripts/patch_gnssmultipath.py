#!/usr/bin/env python
"""
Patches three bugs in the installed gnssmultipath==2.2.0 package (pinned in
pyproject.toml/environment.yml) that surface on real multi-GNSS RINEX 2
files declaring more than 9 observation types. Run once after
`conda env update --file environment.yml` (or any reinstall of
gnssmultipath); idempotent -- running it again on an already-patched
install is a no-op.

See knowledge/decisions/0004-patch-gnssmultipath-multisystem-bugs.md for
what each bug is and why it has to be fixed here rather than in GeoDE's
own code -- the RINEX files that trigger these are valid; these are bugs
in gnssmultipath's own multi-system/epoch bookkeeping.
"""
import sys

_MARKER = 'GeoDE patch'


def _module_file(dotted_name):
    """Import and return __file__ for a module, bypassing attribute lookups
    that a package's __init__.py may have shadowed with a same-named
    function (gnssmultipath.readers does `from .readRinexObs import
    readRinexObs`, which rebinds the submodule's own name on the package)."""
    import importlib
    return importlib.import_module(dotted_name).__file__


def _patch_file(path, old, new, label):
    with open(path, encoding='utf-8') as f:
        src = f.read()
    if new in src:
        print(f'OK (already patched): {label}')
        return True
    if old not in src:
        print(f'SKIP: {label} -- expected text not found in {path}. '
              f'gnssmultipath version mismatch?', file=sys.stderr)
        return False
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src.replace(old, new))
    print(f'PATCHED: {label}')
    return True


def main():
    readRinexObs_file = _module_file('gnssmultipath.readers.readRinexObs')
    signalAnalysis_file = _module_file('gnssmultipath.signalAnalysis')
    gma_file = _module_file('gnssmultipath.GNSS_MultipathAnalysis')

    ok = True

    ok &= _patch_file(
        readRinexObs_file,
        '                Sys = line_.pop(0)[0] # assingning nObs to variable and removing it from the list\n'
        '                if Sys not in ["G","R","E","C"]: # added this line 29.01.2023 to fix bug where Only one system and several lines with Obscodes in rinex file\n'
        '                    continue\n'
        '                if (includeAllGNSSsystems and Sys in ["G", "R", "E", "C"] or Sys in desiredGNSSsystems):',
        '                Sys = line_.pop(0)[0] # assingning nObs to variable and removing it from the list\n'
        f'                if Sys in ["G","R","E","C"] and (includeAllGNSSsystems and Sys in ["G", "R", "E", "C"] or Sys in desiredGNSSsystems):  # {_MARKER}: see knowledge/decisions/0004',
        'readRinexObs.py: GNSSsystems collapses to one system on multi-line PRN / # OF OBS',
    )

    ok &= _patch_file(
        signalAnalysis_file,
        '        for ep in range(1, nepochs):\n'
        '            out[ep] = gnss_lli[ep + 1][:, col]\n'
        '        return out',
        f'        for ep in range(1, nepochs):  # {_MARKER}: see knowledge/decisions/0004\n'
        '            epoch_lli = gnss_lli.get(ep + 1)\n'
        '            out[ep] = -999 if epoch_lli is None else epoch_lli[:, col]\n'
        '        return out',
        'signalAnalysis.py: KeyError on a receiver-gap epoch missing from gnss_lli',
    )

    ok &= _patch_file(
        gma_file,
        '    common = set(sat_pos.keys()) & set(GNSSsystems.values())\n'
        '    return (\n'
        '        {k: v for k, v in GNSS_obs.items() if k in common},\n'
        '        {k: v for k, v in GNSS_LLI.items() if k in common},\n'
        '        {k: v for k, v in GNSS_SS.items() if k in common},\n'
        '        {k: v for k, v in GNSS_SVs.items() if k in common},\n'
        '        {k: v for k, v in GNSSsystems.items() if v in common},\n'
        '    )',
        f'    common = set(sat_pos.keys()) & set(GNSSsystems.values())  # {_MARKER}: see knowledge/decisions/0004\n'
        '    filtered_values = [v for k, v in sorted(GNSSsystems.items()) if v in common]\n'
        '    return (\n'
        '        {k: v for k, v in GNSS_obs.items() if k in common},\n'
        '        {k: v for k, v in GNSS_LLI.items() if k in common},\n'
        '        {k: v for k, v in GNSS_SS.items() if k in common},\n'
        '        {k: v for k, v in GNSS_SVs.items() if k in common},\n'
        '        {i + 1: v for i, v in enumerate(filtered_values)},\n'
        '    )',
        'GNSS_MultipathAnalysis.py: filter_common_gnss_keys does not renumber GNSSsystems after filtering',
    )

    if not ok:
        print('\nOne or more patches could not be applied -- see SKIP lines above.', file=sys.stderr)
        sys.exit(1)
    print('\ngnssmultipath patches applied successfully.')


if __name__ == '__main__':
    main()
