# A minimal setup.py file to make a Python project installable.
#
# Stolen from https://github.com/fperez/mytoy/blob/main/setup.py
#
# Note that while we are following modern packaging practices
# with setuptools metadata being declaratively stored in setup.cfg
# and build configuration listed in pyproj.toml, pip/setuptools
# as of this writing (early 2022) still require a minimal setup.py
# file in order to support editable development installs (pip install -e .)

from setuptools import setup

if __name__ == "__main__":
    setup(name="geode-gnss",
    package_data={
        "geode.elasticity": ["data/*.txt"],
    },
    include_package_data=True,
    scripts=[
        'com/ArchiveService.py',
        'com/CampaignPlanner.py',
        'com/GenerateKml.py',
        'com/PlotETM.py',
        'com/AlterETM.py',
        'com/CreateEtmFunctions.py',
        'com/CloseStationInfo.py',
        'com/ConvertDate.py',
        'com/ConvertMetadata.py',
        'com/ConvertTrimble.py',
        'com/DownloadSources.py',
        'com/DownloadSourcesFill.py',
        'com/DRA.py',
        'com/EtmStacker.py',
        'com/FixPlate.py',
        'com/GenerateSinex.py',
        'com/IntegrityCheck.py',
        'com/LocateRinex.py',
        'com/ModelProcessing.py',
        'com/NEQStack.py',
        'com/OTL_FES2014b.py',
        'com/ParallelGamit.py',
        'com/PlotMapView.py',
        'com/QueryETM.py',
        'com/S-score.py',
        'com/ScanArchive.py',
        'com/Stacker.py',
        'com/StationInfoEdit.py',
        'com/StationKmz.py',
        'com/StationReport.py',
        'com/SyncOrbits.py',
        'com/SyncMetadata.py',
        'com/TrajectoryFit.py',
        'com/UpdateEarthquakes.py',
        'com/WeeklyCombination.py',
        'com/Ztd2trp.py',
        # these are scripts that are outside dependencies but added for convenience
        'scripts/crz2rnx',
        'scripts/rnx2crz',
        'scripts/rename_crinex.sh',
        'scripts/rename_with_logs.sh',
        'scripts/rename_crinex2lower.sh']
          )
