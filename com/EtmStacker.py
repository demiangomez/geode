#!/usr/bin/env python

"""
Project: Geodesy Database Engine (GeoDE)
Date: 10/10/17 9:10 AM
Author: Demian D. Gomez

Routines to stack ETMs and jointly estimate parameters.
"""
import sys
import os

# =======================
# FORCE UNBUFFERED OUTPUT
# =======================

# Method 1: Reconfigure (Python 3.7+)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

# Method 2: Environment variable (backup)
os.environ['PYTHONUNBUFFERED'] = '1'
os.environ['OMP_NUM_THREADS'] = '8'
os.environ['MKL_NUM_THREADS'] = '8'
os.environ['OPENBLAS_NUM_THREADS'] = '8'

import argparse
import os.path

import numpy as np
import logging
import cmd
import pickle
import time
from datetime import datetime
import matplotlib.pyplot as plt
from scipy.stats import iqr
import readline

# app
from geode.etm.core.etm_config import EtmConfig
from geode.etm.core.etm_engine import EtmEngine
from geode.etm.core.data_classes import ModelingParameters, SolutionType
from geode.etm.core.s_score import Earthquake
from geode.etm.core.logging_config import setup_etm_logging
from geode.etm.core.etm_stacker import EtmStacker, ConstraintType, EtmStackerConfig
from geode.etm.core.etm_stacker_vce import EtmStackerVCEEnhanced
from geode.etm.core.type_declarations import JumpType
from geode.dbConnection import Cnn
from geode.Utils import add_version_argument, station_list_help
from geode.Utils import stationID, process_stnlist
from geode.pyDate import Date
from geode.etm.data.solution_data import SolutionData

# Map verbosity to logging levels
VERBOSITY_MAP = {
    'quiet': logging.CRITICAL,  # or logging.NOTSET to disable all
    'info': logging.INFO,
    'debug': logging.DEBUG
}


class EtmStackerShell(cmd.Cmd):
    intro = 'EtmStacker shell. Type help or ? to list commands.\n'
    prompt = '(EtmStacker) > '

    def __init__(self, etm_stacker: EtmStacker, cnn: Cnn):
        super().__init__()
        self.etm_stacker = etm_stacker
        self.cnn = cnn
        self.history_file = "etm_stacker_history.log"
        self.history = []
        self.session_start = datetime.now()

        # Restore command history from pickled EtmStacker
        self._restore_history()

        self.velocities = None
        self.postseismic = None

        # Write session header
        with open(self.history_file, 'a') as f:
            f.write(f"\n{'=' * 70}\n")
            f.write(f"Session: {self.session_start.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"CWD: {os.getcwd()}\n")
            f.write(f"{'=' * 70}\n")

    def _restore_history(self):
        """Restore readline history from EtmStacker instance"""
        if hasattr(self.etm_stacker, 'command_history'):
            readline.clear_history()
            for cmd in self.etm_stacker.command_history:
                if cmd.strip():  # Don't add empty commands
                    readline.add_history(cmd.strip())

    def precmd(self, line):
        """Called before each command - log it here"""
        if line.strip():  # Only log non-empty commands

            if not line.strip().startswith('#'):
                # Save to EtmStacker's persistent history
                if not hasattr(self.etm_stacker, 'command_history'):
                    self.etm_stacker.command_history = []
                self.etm_stacker.command_history.append(line)

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = f"[{timestamp}] {line}\n"

            # Add to in-memory history
            self.history.append(log_entry)

            # Write to file immediately
            with open(self.history_file, 'a') as f:
                f.write(log_entry)

        return line  # Return the line unmodified

    def do_history(self, arg):
        """
        Print the command history for this EtmStacker
        """
        for cmd in self.etm_stacker.command_history:
            print(cmd)

    def do_remove_station(self, arg: str):
        self.etm_stacker.remove_station(arg)
        print(f'Removed station {arg}, invoke solve again when ready')

    def do_reweight_station(self, arg: str):
        """
        Update the weight scale of one or more stations.
        syntax: reweight_station <net.stn> [<net.stn> ...] <value>
        """
        try:
            values = arg.split()
            stnlist = process_stnlist(self.cnn, arg.split()[0:-1])
            value = values[-1]
            for stn in stnlist:
                self.etm_stacker.change_station_weight(stationID(stn), float(value))
        except ValueError:
            print("Usage: reweight_station <station_id> <value>")

    def do_save(self, arg):
        """
        Save session to file
        syntax: save [filename|new] (no extension). If no filename provided, use current filename (overwrite).
                use save new to create a new automatic filename
        """
        if not arg:
            # no filename, use current
            arg = self.etm_stacker.filename
        elif arg == 'new':
            # save as, use new filename
            arg = 'etm_stacker_session_' + datetime.now().strftime('%Y%m%d_%H%M%S')

        self.etm_stacker.filename = arg

        with open(f'{arg}.pkl', 'wb') as f:
            pickle.dump(self.etm_stacker, f, protocol=pickle.HIGHEST_PROTOCOL)

        print(f"Saved to {arg}")

    def do_load(self, arg):
        """
        Load previously saved session
        """
        pass

    def do_quit(self, arg):
        """Exit the shell"""
        print("Goodbye!")
        return True

    def do_EOF(self, arg):
        """Exit on Ctrl-D"""
        print()
        return True

    def do_vce(self, arg):
        # Run VCE
        vce = EtmStackerVCEEnhanced(self.etm_stacker, max_iterations=20, tolerance=1)
        results = vce.run_vce_with_observation_scaling()

        # Review results
        vce.print_summary()
        vce.plot_convergence('convergence.png')

    def do_add_event(self, arg):
        """
        Add earthquake to the list of modeled events
        syntax add_event <id> <id> ...
        """
        import math

        for e in arg.split():
            # find the event id
            j = self.cnn.query_float(f"SELECT * FROM earthquakes WHERE id = '{e}'", as_dict=True)

            if len(j) > 0:
                j = j[0]
                strike = [float(j['strike1']), float(j['strike2'])] if not math.isnan(j['strike1']) else []
                dip = [float(j['dip1']), float(j['dip2'])] if not math.isnan(j['strike1']) else []
                rake = [float(j['rake1']), float(j['rake2'])] if not math.isnan(j['strike1']) else []

                event = Earthquake(
                    id=j['id'], lat=j['lat'], lon=j['lon'],
                    date=Date(datetime=j['date']), depth=j['depth'], magnitude=j['mag'],
                    distance=0.0, location=j['location'], strike=strike, dip=dip,
                    rake=rake, jump_type=JumpType.COSEISMIC_JUMP_DECAY)
                self.etm_stacker.add_earthquake(event)
            else:
                print(f'Could not find event id {e}')

    def do_remove_event(self, arg):
        """
        Remove earthquake to the list of modeled events
        syntax remove_event <id> <id> ...
        """
        import math

        for e in arg.split():
            # find the event id
            j = self.cnn.query_float(f"SELECT * FROM earthquakes WHERE id = '{e}'", as_dict=True)

            if len(j) > 0:
                j = j[0]
                strike = [float(j['strike1']), float(j['strike2'])] if not math.isnan(j['strike1']) else []
                dip = [float(j['dip1']), float(j['dip2'])] if not math.isnan(j['strike1']) else []
                rake = [float(j['rake1']), float(j['rake2'])] if not math.isnan(j['strike1']) else []

                event = Earthquake(
                    id=j['id'], lat=j['lat'], lon=j['lon'],
                    date=Date(datetime=j['date']), depth=j['depth'], magnitude=j['mag'],
                    distance=0.0, location=j['location'], strike=strike, dip=dip,
                    rake=rake, jump_type=JumpType.COSEISMIC_JUMP_DECAY)
                self.etm_stacker.remove_earthquake(event)
            else:
                print(f'Could not find event id {e}')

    def do_plot(self, arg='field'):
        """
        Plot results using matplotlib to visualize deformation fields
        syntax plot <field> or <sigmas> to plot the sigmas
        """
        if self.etm_stacker.solved:
            if arg == 'field' or arg == '':
                fig = self.etm_stacker.plot_grid_result()
            elif arg == 'sigmas':
                fig = self.etm_stacker.plot_grid_result(sigmas=True)
            else:
                print('unknown argument')
        else:
            print('EtmStacker is not showing as solved. Execute solve first.')

    def do_plot_etm(self, arg):
        """
        plot and save constrained ETM. If folder is provided, save ETM as PNG. Otherwise, show ETM interactively.
        syntax: plot_etm <network.station> [folder]
        """
        if self.etm_stacker.solved:
            parts = arg.split()
            for i, stn in enumerate(self.etm_stacker.stations):
                if stationID(stn) == parts[0]:
                    if len(parts) > 1:
                        folder = parts[1]
                        print(f'Will save ETM for station {stationID(stn)} in {folder}')
                        if not os.path.exists(folder):
                            os.makedirs(folder)
                    else:
                        folder = None

                    self.etm_stacker.plot_constrained_etm(i, folder)
                    break
            print(self.prompt)
        else:
            print('EtmStacker is not showing as solved. Execute solve first.')

    def do_list(self, arg):
        """
        list all the <events> or <constraints>
        """
        if arg == 'events':
            for event in self.etm_stacker.earthquakes:
                print(event.id)
        elif arg == 'constraints':
            for cont_list in self.etm_stacker.constraint_registry.constraints.values():
                for const in cont_list:
                    print(repr(const))
        elif arg == 'stations':
            for station in self.etm_stacker.stations:
                print(f'{stationID(station)} weight {station.normal_equations.weight_scale}')

    def do_update_constraint(self, arg):
        """
        Update constraint sigmas and re-solve (without field interpolation).
        syntax: update_constraint <interseismic|coseismic|postseismic> [event_id] [relax] <h_sigma_mm> <v_sigma_mm>
        if event_id is not given, all constraints of selected type will be modified. Relax only valid for postseismic.
        Sigmas are in millimetres.
        """
        if not arg:
            print('syntax: update_constraint <interseismic|coseismic|postseismic> [event_id] [relax] '
                  '<h_sigma_mm> <v_sigma_mm>')
            return

        parts = arg.split()

        if parts[0] not in ('interseismic', 'coseismic', 'postseismic'):
            print(f'{parts[0]}: invalid constraint type, use interseismic, coseismic, or postseismic')
            return

        try:
            if len(parts) == 5:
                # type event_id relax h v
                relax   = float(parts[2])
                h_sigma = float(parts[3]) / 1000.
                v_sigma = float(parts[4]) / 1000.
                if parts[0] != 'postseismic':
                    print('Relaxation argument only valid for postseismic constraint')
                    return
                self.etm_stacker.update_weights(
                    constraint_type=parts[0], event_id=parts[1], h_sigma=h_sigma, v_sigma=v_sigma, relax=relax
                )
            elif len(parts) == 4:
                # type event_id h v
                h_sigma = float(parts[2]) / 1000.
                v_sigma = float(parts[3]) / 1000.
                self.etm_stacker.update_weights(
                    constraint_type=parts[0], event_id=parts[1], h_sigma=h_sigma, v_sigma=v_sigma
                )
            elif len(parts) == 3:
                # type h v
                h_sigma = float(parts[1]) / 1000.
                v_sigma = float(parts[2]) / 1000.
                self.etm_stacker.update_weights(
                    constraint_type=parts[0], h_sigma=h_sigma, v_sigma=v_sigma
                )
            else:
                print('syntax: update_constraint <interseismic|coseismic|postseismic> [event_id] [relax] '
                      '<h_sigma_mm> <v_sigma_mm>')
                return
        except ValueError:
            print('syntax: update_constraint <interseismic|coseismic|postseismic> [event_id] [relax] '
                  '<h_sigma_mm> <v_sigma_mm>')
            return

        print('Re-solving...')
        tic = time.time()
        self.velocities, self.postseismic = self.etm_stacker.solve()
        print(f'Solved in {(time.time() - tic):.1f} sec')

    def do_okada_weights(self, arg):
        """
        Update SW-Okada regularization weights for co/postseismic constraints and re-solve.
        syntax: okada_weights <h_weight> <v_weight> [event_id [relax]]
        Examples:
          okada_weights 10 10                      apply to all co/postseismic constraints
          okada_weights 50 50 us20005iis            apply to one event (co + postseismic)
          okada_weights 20 20 us20005iis 0.050      apply to one postseismic relaxation only
        The solver runs automatically after updating (without field interpolation).
        Use predict afterwards if you want the interpolated grid fields refreshed.
        """
        if not arg:
            print('syntax: okada_weights <h_weight> <v_weight> [event_id [relax]]')
            return

        parts = arg.split()
        try:
            if len(parts) == 2:
                h_weight, v_weight = float(parts[0]), float(parts[1])
                event_id, relax = None, None
            elif len(parts) == 3:
                h_weight, v_weight = float(parts[0]), float(parts[1])
                event_id, relax = parts[2], None
            elif len(parts) == 4:
                h_weight, v_weight = float(parts[0]), float(parts[1])
                event_id, relax = parts[2], float(parts[3])
            else:
                print('syntax: okada_weights <h_weight> <v_weight> [event_id [relax]]')
                return
        except ValueError:
            print('syntax: okada_weights <h_weight> <v_weight> [event_id [relax]]')
            return

        n = self.etm_stacker.update_okada_weights(h_weight, v_weight,
                                                   event_id=event_id, relax=relax)
        if n == 0:
            return

        print(f'Updated {n} constraint(s). Re-solving...')
        tic = time.time()
        self.velocities, self.postseismic = self.etm_stacker.solve()
        toc = time.time()
        print(f'Solved in {(toc - tic):.1f} sec')

    def do_print(self, arg=''):
        """
        print the <rms>|<vel> |<config>
              <rms>   : summary for the RMS of applied constraints 
              <vel>   : a priori vs constrained velocities
              <config>: summary of the stacker configuration 
        """""

        if arg not in ('rms', 'vel', 'config'):
            print(f'Unknown argument {arg}')
            return

        if arg in ('rms', 'vel') and not self.etm_stacker.solved:
            print('System is not solved. Invoke solve first!')
            return

        if self.etm_stacker.solved and arg == 'rms':
            wrms = self.etm_stacker.constraints_rms()
            print('List ordered by descending constraint rms')
            for item in wrms:
                if item[0].constraint_type == ConstraintType.INTERSEISMIC:
                    unit = '[mm/yr]'
                else:
                    unit = '[mm]'

                print(f'{item[0].short_description():60s}' +
                      f' {item[1]:4} equations -> '
                      f'rms: {item[2] * 1000.: 8.3f} ' + unit + ' '
                      f'a priori: {item[0].h_sigma * 1000.:.3f} {item[0].v_sigma * 1000.:.3f}')

                #for eq in item[3]:
                #    res = ' '.join([f'{v * 1000:8.3f}' for v in eq[1]])
                #    print(f'  -> {eq[0]} {res} ' + unit)

        elif arg == 'config':
            self.etm_stacker.print_config()

        elif arg == 'vel' and self.velocities is not None:
            header = list(self.velocities[0].keys())
            print(f'{header[0]:8s} {header[1]:15s} {header[2]:15s} {header[3]:27s} '
                  f'{header[4]:27s} {header[5]:15s}')

            for stn in self.velocities:
                name = stn['station']
                vc = stn['constrained']
                vp = stn['a_priori']
                lon = stn['lon']
                lat = stn['lat']
                inter = stn['is_interseismic']
                print(f'{name} {lon:15.8f} {lat:15.8f} {vp[0] * 1000.:9.2f} '
                      f'{vp[1] * 1000.:9.2f} {vp[2] * 1000.:9.2f} {vc[0] * 1000.:9.2f} '
                      f'{vc[1] * 1000.:9.2f} {vc[2] * 1000.:9.2f} {inter}')

    def do_test(self, arg):
        """test the model with a station"""
        if arg:
            stnlist = process_stnlist(self.cnn, arg.split())
            stn_t, stn_e, stn_n, stn_u = [], [], [], []

            for stn in stnlist:
                net, stnm = stn['NetworkCode'], stn['StationCode']

                try:
                    config = EtmConfig(net, stnm, cnn=self.cnn)
                    config.solution.solution_type = SolutionType.PPP
                    etm = EtmEngine(config, self.cnn, silent=True)
                    etm.run_adjustment(try_loading_db=False, try_save_to_db=False)

                    # config = EtmConfig(net, stnm, cnn=self.cnn)
                    # config.solution.solution_type = SolutionType.PPP
                    # config.plotting_config.interactive = True
                    # config = self.etm_stacker._apply_config(config, self.cnn)

                    #solution_data = SolutionData.create_instance(config)
                    #solution_data.load_data(cnn=self.cnn)
                    etm.fit.outlier_flags[etm.solution_data.time_vector < 1995.] = False

                    neu = np.array(etm.solution_data.transform_to_local())
                    enu = neu[[1, 0, 2]]
                    enu = enu[:, etm.fit.outlier_flags]
                    enu_ = neu[[1, 0, 2]]
                    enu_ = enu_[:, etm.fit.outlier_flags]

                    for field in self.etm_stacker.fields:
                        # print(f'Fetching {field.description}')
                        if (field.base_type != ConstraintType.INTERSEISMIC and
                            field.onset_date.fyear > etm.solution_data.time_vector.max()):
                            # time series stopped, do not evaluate any more fields
                            break

                        m = field.eval(config.metadata.lon[0], config.metadata.lat[0],
                                       etm.solution_data.time_vector[etm.fit.outlier_flags])
                        # print(f'applying {field.description} {m}')
                        for i in range(3):
                            enu_[i] -= m[i]

                    # done applying models, remove mean
                    for i in range(3):
                        enu_[i] -= np.mean(enu_[i])
                        enu[i] -= np.mean(enu[i])

                    stn_t.extend(etm.solution_data.time_vector[etm.fit.outlier_flags].tolist())
                    stn_e.extend(enu_[0].tolist())
                    stn_n.extend(enu_[1].tolist())
                    stn_u.extend(enu_[2].tolist())

                    prgn = (enu.max(axis=1) - enu.min(axis=1)) * 1000.
                    argn = (enu_.max(axis=1) - enu_.min(axis=1)) * 1000.

                    print(f'{stationID(stn)} value range PRIOR/AFTER [mm] '
                          f'E: {prgn[0]:8.2f}/{argn[0]:8.2f} '
                          f'N: {prgn[1]:8.2f}/{argn[1]:8.2f} '
                          f'U: {prgn[2]:8.2f}/{argn[2]:8.2f}')
                except Exception as e:
                        print(f'skipped {stationID(stn)}: {str(e)}')

            if arg.split()[0] == 'ts':
                self.plot_time_series(np.array(stn_t), np.array(stn_e) * 1000.,
                                      np.array(stn_n) * 1000.,
                                      np.array(stn_u) * 1000.)
            else:
                self.plot_darts(np.array(stn_t), np.array(stn_e) * 1000.,
                                np.array(stn_n) * 1000.,
                                np.array(stn_u) * 1000.)
        else:
            print('usage: test <net.stnm> <net.stnm> ...')

    def do_export(self, arg):
        """export interpolated field to text file
        available fields: interseismic"""

        if arg == 'interseismic':
            for field in self.etm_stacker.fields:
                if field.base_type == ConstraintType.INTERSEISMIC:
                    lon, lat = field.get_interpolation_grid_geographic()
                    np.savetxt('export_interseismic.txt',
                               np.array([lon, lat,
                                         field.enu_field[0, :] * 1000,
                                         field.enu_field[1, :] * 1000,
                                         field.enu_field[2, :] * 1000]).transpose(),
                               fmt=['%11.6f', '%10.6f', '%7.3f', '%7.3f', '%7.3f'],
                               header="interseismic velocities in mm/yr"
                                      "\ncreated on %s using %s"
                                      "\nlon lat e n u"
                                      % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), self.etm_stacker.filename))
                    break

    def do_overlay(self, arg):
        """overlay model and data from a station"""
        if arg:
            stnlist = process_stnlist(self.cnn, arg.split())

            for stn in stnlist:
                net, stnm = stn['NetworkCode'], stn['StationCode']

                config = EtmConfig(net, stnm, cnn=self.cnn)
                config.solution.solution_type = SolutionType.PPP
                etm = EtmEngine(config, self.cnn, silent=True)
                etm.run_adjustment(try_loading_db=False, try_save_to_db=False)
                #config = EtmConfig(net, stnm, cnn=self.cnn)
                #config.solution.solution_type = SolutionType.PPP
                #config.plotting_config.interactive = True
                #config = self.etm_stacker._apply_config(config, self.cnn)
                #solution_data = SolutionData.create_instance(config)
                #solution_data.load_data(cnn=self.cnn)

                etm.fit.outlier_flags[etm.solution_data.time_vector < 1995.] = False

                neu = np.array(etm.solution_data.transform_to_local())
                enu = neu[[1, 0, 2]]
                enu = enu[:, etm.fit.outlier_flags]
                enu_ = neu[[1, 0, 2]]
                enu_ = enu_[:, etm.fit.outlier_flags]

                model = np.zeros((3, etm.solution_data.time_vector_cont.size))

                for field in self.etm_stacker.fields:
                    # print(f'Fetching {field.description}')
                    if (field.base_type != ConstraintType.INTERSEISMIC and
                            field.onset_date.fyear > etm.solution_data.time_vector.max()):
                        # time series stopped, do not evaluate any more fields
                        break

                    print(f'doing {field.base_type} {field.event}')

                    model += field.eval(config.metadata.lon[0], config.metadata.lat[0],
                                        etm.solution_data.time_vector_cont)
                    # this is to find the mean between the model and the data
                    m = field.eval(config.metadata.lon[0], config.metadata.lat[0],
                                   etm.solution_data.time_vector[etm.fit.outlier_flags])
                    # print(f'applying {field.description} {m}')
                    for i in range(3):
                        enu_[i] -= m[i]

                # add the mean between model and data
                for i in range(3):
                    model[i] += np.mean(enu_[i])

                self.plot_overlay(
                    etm.solution_data.time_vector[etm.fit.outlier_flags],
                    enu[0] * 1000., enu[1] * 1000., enu[2] * 1000.,
                    etm.solution_data.time_vector_cont,
                    model[0] * 1000., model[1] * 1000., model[2] * 1000.
                )

        else:
            print('usage: test <net.stnm> <net.stnm> ...')


    @staticmethod
    def plot_darts(t_, e_, n_, u_):

        plt.ion()
        fig, axes = plt.subplots(nrows=2, ncols=2, figsize=(16, 10))

        ax = axes[0, 0]

        residuals = np.sqrt(e_ ** 2 + n_ ** 2)

        mask = np.abs(residuals) <= iqr(residuals) * 5

        print(f'Filtered {len(mask) - np.sum(mask)} observations as outliers out of {len(mask)} ')

        # Generate circle points
        theta = np.linspace(0, 2 * np.pi, 100)
        x = 10 * np.cos(theta)
        y = 10 * np.sin(theta)

        ax.plot(x, y, 'r-', linewidth=2)
        ax.plot(2*x, 2*y, 'r--', linewidth=2)
        ax.plot(4 * x, 4 * y, 'r:', linewidth=2)
        scatter = ax.scatter(e_[mask], n_[mask], c=t_[mask], s=12,
                             alpha=0.8,  # Transparency,
                             cmap='viridis', edgecolors='none')
        cbar = plt.colorbar(scatter, ax=ax, label='Time (years)')
        cbar.ax.tick_params(labelsize=8)  # Adjust tick label size

        ax.grid(True)
        ax.set_xlabel(f"East [mm]")
        ax.set_ylabel(f"North [mm]")
        ax.set_title(f"Residuals East-North")
        ax.axis('equal')

        xlim, ylim = ax.get_xlim(), ax.get_ylim()

        ax = axes[0, 1] # north
        ax.hist(n_[mask], bins=100, alpha=0.75, density=True,
                color=(0, 150 / 255, 235 / 255), orientation='horizontal')

        ax.set_xlabel('Frequency')
        ax.set_ylabel(f"North Residuals [mm]")
        ax.set_ylim(ylim)
        ax.grid(True)

        ax = axes[1, 0] # east
        ax.hist(e_[mask], bins=100, alpha=0.75, density=True,
                color=(0, 150 / 255, 235 / 255), orientation='vertical')
        ax.set_xlabel('Frequency')
        ax.set_ylabel(f"East Residuals [mm]")
        ax.grid(True)
        ax.set_xlim(xlim)

        ax = axes[1, 1] # up
        residuals = np.abs(u_)
        mask = np.abs(residuals) <= iqr(residuals) * 5

        ax.hist(u_[mask], bins=100, alpha=0.75, density=True,
                color=(0, 150 / 255, 235 / 255), orientation='vertical')
        ax.set_xlabel('Frequency')
        ax.set_ylabel(f"Up Residuals [mm]")
        ax.grid(True)

        plt.show()

    @staticmethod
    def plot_time_series(t_, e_, n_, u_):

        plt.ion()
        fig, axes = plt.subplots(nrows=3, ncols=1, figsize=(16, 10))

        ax = axes[0]

        residuals = np.sqrt(e_ ** 2 + n_ ** 2)

        mask = np.abs(residuals) <= iqr(residuals) * 5

        print(f'Filtered {len(mask) - np.sum(mask)} observations as outliers out of {len(mask)} ')

        ax.plot(t_[mask], e_[mask], 'o', color=(0, 150 / 255, 235 / 255), markersize=2)

        ax.grid(True)
        ax.set_xlabel(f"Years [yr]")
        ax.set_ylabel(f"East [mm]")

        ax = axes[1] # north
        ax.plot(t_[mask], n_[mask], 'o', color=(0, 150 / 255, 235 / 255), markersize=2)

        ax.grid(True)
        ax.set_xlabel(f"Years [yr]")
        ax.set_ylabel(f"North [mm]")

        ax = axes[2] # up
        ax.plot(t_[mask], u_[mask], 'o', color=(0, 150 / 255, 235 / 255), markersize=2)

        ax.grid(True)
        ax.set_xlabel(f"Years [yr]")
        ax.set_ylabel(f"Up [mm]")

        plt.show()

    @staticmethod
    def plot_overlay(t_s, e_s, n_s, u_s, t_m, e_m, n_m, u_m):

        plt.ion()
        fig, axes = plt.subplots(nrows=3, ncols=1, figsize=(16, 10))

        ax = axes[0]

        ax.plot(t_s, e_s, 'o', color=(0, 150 / 255, 235 / 255), markersize=2)
        ax.plot(t_m, e_m, color='red')

        ax.grid(True)
        ax.set_xlabel(f"Years [yr]")
        ax.set_ylabel(f"East [mm]")

        ax = axes[1]  # north
        ax.plot(t_s, n_s, 'o', color=(0, 150 / 255, 235 / 255), markersize=2)
        ax.plot(t_m, n_m, color='red')

        ax.grid(True)
        ax.set_xlabel(f"Years [yr]")
        ax.set_ylabel(f"North [mm]")

        ax = axes[2]  # up
        ax.plot(t_s, u_s, 'o', color=(0, 150 / 255, 235 / 255), markersize=2)
        ax.plot(t_m, u_m, color='red')

        ax.grid(True)
        ax.set_xlabel(f"Years [yr]")
        ax.set_ylabel(f"Up [mm]")

        plt.show()

    def do_predict(self, arg):
        """recompute the inter, co, and postseismic interpolated fields"""
        self.etm_stacker.interpolate_fields_to_grid()

    def do_solve(self, arg):
        """
        Trigger EtmStacker solver: collect constraints and obtain parameters
        Call solve false to avoid interpolating the fields (useful for just looking at variance component
        syntax: solve [false]
        """
        tic = time.time()
        if arg.strip() == '':
            self.velocities, self.postseismic = self.etm_stacker.solve()
        else:
            self.velocities, self.postseismic = self.etm_stacker.solve(interpolate_fields=False)
        toc = time.time()
        print(f'Solved in {(toc - tic):.1f} sec')

            #for stn in postseismic:
            #    name = stn['station']
            #    event_id = stn['event_id']
            #    relax = stn['relax']
            #    vc = stn['constrained']
            #    vp = stn['a_priori']
            #    lon = stn['lon']
            #    lat = stn['lat']
            #
            #    print(f'{name} {lon:15.8f} {lat:15.8f} {event_id:32s}'
            #            f'{relax:6.3f} {vc[0] * 1000.:10.2f} {vc[1] * 1000.:10.2f} {vc[2] * 1000.:10.2f} '
            #            f'{vp[0] * 1000.:10.2f} {vp[1] * 1000.:10.2f} {vp[2] * 1000.:10.2f}')

def main():

    def_config = EtmStackerConfig()

    parser = argparse.ArgumentParser(description='Routines to stack ETMs and jointly estimate parameters',
                                     formatter_class=argparse.RawTextHelpFormatter)

    parser.add_argument('stnlist', type=str, nargs='*',
                        help=station_list_help())

    parser.add_argument('-is', '--interseismic_sigmas', type=float,
                        default=[def_config.interseismic_h_sigma * 1000., def_config.interseismic_v_sigma * 1000.],
                        metavar='mm/yr', nargs=2,
                        help=f"Interseismic horizontal and vertical sigmas (given in mm/yr) to use for the "
                             f"interseismic constraints. Default h={def_config.interseismic_h_sigma * 1000.} "
                             f"mm/yr and v={def_config.interseismic_v_sigma * 1000.} mm/yr.")

    parser.add_argument('-cs', '--coseismic_sigmas', type=float,
                        default=[def_config.coseismic_h_sigma * 1000., def_config.coseismic_v_sigma * 1000.],
                        metavar='mm', nargs=2,
                        help=f"Coseismic horizontal and vertical sigmas (given in mm) to use for the "
                             f"coseismic constraints. Default values are h={def_config.coseismic_h_sigma * 1000.} "
                             f"mm and v={def_config.coseismic_v_sigma * 1000.} mm.")

    parser.add_argument('-ps', '--postseismic_sigmas', type=float,
                        default=[def_config.postseismic_h_sigma * 1000., def_config.postseismic_v_sigma * 1000.],
                        metavar='mm', nargs=2,
                        help=f"Postseismic horizontal and vertical sigmas (given in mm) to use for the "
                             f"postseismic constraints. Default values are h={def_config.postseismic_h_sigma * 1000.} "
                             f"mm and v={def_config.postseismic_v_sigma * 1000.} mm.")

    parser.add_argument('-sw', '--station_weight', type=float, default=1.0, metavar='unitless',
                        help="Station scale weight to form the normal equations. Default value is 1.0")

    parser.add_argument('-vm', '--vertical_method', type=str, default=def_config.vertical_method,
                        metavar='method', choices=['diskload', 'spline2d', 'rectload'],
                        help="Vertical interpolation method. Choose from diskload, spline2d, and rectload. Default "
                             f"is {def_config.vertical_method}")

    parser.add_argument('-vr', '--vertical_load_radius', type=float,
                        default=def_config.vertical_load_radius, metavar='km',
                        help=f"Vertical loading grid (for methods diskload and rectload). "
                             f"Default value is {def_config.vertical_load_radius} km")

    parser.add_argument('-t', '--tension', type=float, default=def_config.tension, metavar='unitless',
                        help=f"Tension parameter for method spline2d. "
                             f"Default value is {def_config.tension}")

    parser.add_argument('-s_score', '--s_score_mag_limit', type=float, default=6.0, metavar='magnitude',
                        help="Limit the s-score search to earthquakes with magnitude >= {magnitude}. Default is 6.0")

    parser.add_argument('-load', '--load_from_file', type=str, default=None, metavar='filename',
                        help="Load ETM stacker session from pickle file")

    parser.add_argument('-load_json', '--load_json', type=str, default=None, metavar='path',
                        help="Path to folder containing the json files for the ETMs (rather than using the db)")

    parser.add_argument('-save_json', '--save_json', type=str, default=None, metavar='path',
                        help="Path to folder to save ETM json files (this makes the reprocess faster)")

    parser.add_argument('-force', '--force_earthquakes', nargs='+', default=[], metavar='event_id',
                        help="Add cherry-picked seismic earthquake (that fall outside of s_score_mag_limit) to the "
                             "list of jump functions to fit (using the USGS event id). Event needs to have an "
                             "s-score > 0 to be considered, even if it has been cherry-picked")

    parser.add_argument('-relax', '--default_relax', type=float, nargs='+',
                        default=ModelingParameters().relaxation,
                        help="Relaxation value(s) to use during the fit. Default as defined by the station in "
                             "the database or in the ETM module (0.05 and 1 years)")

    parser.add_argument('-etm_verbosity', '--etm_verbosity',
                        choices=['quiet', 'info', 'debug'], default='quiet',
                        help="Determine how detailed the execution messages should be. "
                             "Default is 'info'")

    add_version_argument(parser)

    args = parser.parse_args()

    cnn = Cnn('gnss_data.cfg', write_cfg_file=True)

    setup_etm_logging(level=VERBOSITY_MAP[args.etm_verbosity])

    if args.stnlist:
        stnlist = process_stnlist(cnn, args.stnlist)
    else:
        stnlist = []

    etm_stacker = None
    # user loading from a previous session pickle
    if not args.load_from_file:

        config = EtmStackerConfig(
            earthquake_magnitude_limit=args.s_score_mag_limit,
            relaxation=np.array(args.default_relax),
            earthquakes_cherry_picked=args.force_earthquakes,
            interseismic_h_sigma=args.interseismic_sigmas[0] / 1000.,
            interseismic_v_sigma=args.interseismic_sigmas[1] / 1000.,
            coseismic_h_sigma=args.coseismic_sigmas[0] / 1000.,
            coseismic_v_sigma=args.coseismic_sigmas[1] / 1000.,
            postseismic_h_sigma=args.postseismic_sigmas[0] / 1000.,
            postseismic_v_sigma=args.postseismic_sigmas[1] / 1000.,
            station_weight_scale=args.station_weight,
            vertical_method=args.vertical_method,
            vertical_load_radius=args.vertical_load_radius,
            tension=args.tension
        )

        etm_stacker = EtmStacker(config)

        etm_stacker.prepare_earthquake_list(cnn, stnlist)

        for stn in stnlist:
            # processing station
            etm_stacker.add_station(cnn, stn['NetworkCode'], stn['StationCode'],
                                    json_folder=args.load_json,
                                    save_json_folder=args.save_json)

        etm_stacker.build_system()

        datestr = datetime.now().strftime('%Y%m%d_%H%M%S')
        etm_stacker.filename = f'etm_stacker_session_{datestr}'
        #with open(f'etm_stacker_session_{datestr}.pkl', 'wb') as f:
        #    pickle.dump(etm_stacker, f, protocol=pickle.HIGHEST_PROTOCOL)

        etm_stacker.solve()
    else:
        print(f' -- Loading saved session {args.load_from_file}')
        with open(args.load_from_file, 'rb') as f:
            etm_stacker = pickle.load(f)

        etm_stacker.filename = os.path.basename(args.load_from_file).removesuffix('.pkl')

    if etm_stacker.solved:
        print(' -- System shows up as solved')
    else:
        print(' -- System needs to be solved first')

    # Interactive user mode
    EtmStackerShell(etm_stacker, cnn).cmdloop()

if __name__ == '__main__':
    main()
