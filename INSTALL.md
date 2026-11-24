Installation
=================

Currently geode requires Python 3.10 or later.


Once installed, you can run geode from any folder containing the .cfg file
with the configuration to access the RINEX and orbits archive, database, etc.
Commands are included in your PATH so you can execute, for example `PlotETM.py
igm1`

As a library
------------

Parallel.GAMIT can be installed using pip:

```
pip install geode-gnss
```

This will install the python libraries and dependencies, but does not setup
the database tools. If you would like to install the pgadmin4-desktop 
dependencies, you can do so on Debian based systems (including Ubuntu) with:

```
sudo sh -c 'echo "deb https://ftp.postgresql.org/pub/pgadmin/pgadmin4/apt/focal/ pgadmin4 main" > /etc/apt/sources.list.d/pgadmin4.list && apt update'
sudo apt install pgadmin4-desktop
```

As a development or production environment
------------------------------------------

To install the latest version of geode from our master branch, we
recommend using either [conda](https://github.com/conda/conda) or 
[mamba](https://github.com/mamba-org/mamba); see
[here](https://mamba.readthedocs.io/en/latest/index.html) for instructions on
installing mamba.

Once mamba is installed, [download](https://github.com/demiangomez/Parallel.GAMIT/archive/refs/heads/master.zip)
and unzip, or clone the project repository using git:

```
git clone git@github.com:demiangomez/Parallel.GAMIT.git        # if you have a github user configured
git clone https://github.com/demiangomez/Parallel.GAMIT.git    # if you don't have a github user configured
```

From the project directory, build and activate the environment using:

```
conda env update --file environment.yml
conda activate geode
```

You can deactivate the environment using `conda deactivate`, and 
reactivate (without reinstalling) with `conda activate geode`. To update
the environment, edit the environment.yml file and then run:

```
conda env update --file environment.yml --name geode
```

RINEX QC (`com/RinexQC.py`) needs the `ringo` binary — not a pip/conda package, a standalone
executable from GSI (Japan). Install it with:

```
scripts/install_ringo.sh
```

This downloads the right platform build fresh from GSI and places it in `$HOME/.local/bin` (add
that to your `PATH` if the script tells you to). See
[knowledge/decisions/0005-switch-to-ringo.md](knowledge/decisions/0005-switch-to-ringo.md) for why
RINGO and not gnssmultipath (ADR-0003/0004, now superseded).

Other external dependencies that require a licence
--------------------------------------------------

> [!IMPORTANT]
> geode still requires access to some executables (and GAMIT) which are not installed by default. These programs, however, are not all needed if you are planning to just execute time series analysis. The external dependencies are:
> + GAMIT/GLOBK: http://www-gpsg.mit.edu/gg/
> + GFZRNX: https://gnss.gfz-potsdam.de/services/gfzrnx
> + rnx2crx / crx2rnx: https://terras.gsi.go.jp/ja/crx2rnx.html (although this is also installed with GAMIT/GLOBK)
> + GPSPACE: https://github.com/demiangomez/GPSPACE (forked from https://github.com/lahayef/GPSPACE)



