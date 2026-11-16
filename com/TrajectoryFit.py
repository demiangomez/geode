#!/usr/bin/env python
"""
Project: Parallel.Archive
Date: 02/16/2017
Author: Demian D. Gomez
"""

import argparse
from io import BytesIO
import base64
import os
import json
import matplotlib.pyplot as plt
import simplekml
from scipy.interpolate import griddata

# deps
from tqdm import tqdm
import numpy as np
import matplotlib

if not os.environ.get('DISPLAY', None):
    matplotlib.use('Agg')

# app
from geode import dbConnection
from geode import Utils
from geode import pyETM
from geode import pyDate
from geode.Utils import stationID, file_write, add_version_argument
from geode.pyETM import CO_SEISMIC_JUMP_DECAY, CO_SEISMIC_DECAY


def plot_station_param(NetworkCode, StationCode, parameter_name, unit, pn, pe):

    fig = plt.figure(figsize=(5, 5))

    fig.suptitle('Station %s for %s.%s' % (parameter_name, NetworkCode, StationCode))
    plt.plot(0, 0, 'ok')
    plt.xlim([-30, 30])
    plt.ylim([-30, 30])
    plt.quiver(0, 0, np.multiply(pe, 1000), np.multiply(pn, 1000), scale=1, scale_units='x', zorder=3)
    plt.grid(True)
    plt.xlabel('[%s]' % unit)
    plt.ylabel('[%s]' % unit)

    figfile = BytesIO()
    fig.savefig(figfile, format='png')
    # plt.show()
    figfile.seek(0)  # rewind to beginning of file

    figdata_png = base64.b64encode(figfile.getvalue()).decode()

    plt.close()

    return figdata_png


def generate_kmz(kmz, stations, discarded, deformation_type='interseismic', units='mm/yr'):

    tqdm.write(' >> Generating KML (see production directory)...')

    kml = simplekml.Kml()
    folder1 = kml.newfolder(name=deformation_type)
    folder2 = kml.newfolder(name='discarded')

    # define styles
    styles_ok  = simplekml.StyleMap()
    styles_nok = simplekml.StyleMap()
    for (s, icon_color, label_scale) in ((styles_ok.normalstyle,     'ff00ff00', 0), 
                                         (styles_ok.highlightstyle,  'ff00ff00', 3),
                                         (styles_nok.normalstyle,    'ff0000ff', 0),
                                         (styles_nok.highlightstyle, 'ff0000ff', 3)):
        s.iconstyle.icon.href = 'http://maps.google.com/mapfiles/kml/shapes/placemark_square.png'
        s.iconstyle.color     = icon_color
        s.labelstyle.scale    = label_scale

    for stn in tqdm(stations, ncols=160, disable=None, desc=' -- Included station list'):
        stn_id = stationID(stn)
        plot   = plot_station_param(stn['NetworkCode'], stn['StationCode'],
                                    deformation_type, units, stn['n'], stn['e'])

        pt = folder1.newpoint(name=stn_id, coords=[(stn['lon'], stn['lat'])])
        pt.stylemap = styles_ok

        pt.description = """<strong>NE (%s): %5.2f %5.2f [%s]</strong><br><br>
        <table width="880" cellpadding="0" cellspacing="0">
        <tr>
        <td align="center" valign="top">
        <strong>Parameters:</strong><br>
        <img src="data:image/png;base64, %s" alt="Observation information" height="300" width="300"/><br>
        <strong>Trajectory model:</strong><br>
        <img src="data:image/png;base64, %s" alt="Observation information" height="750" width="1100"/>
        </p>
        </tr>
        </td>
        </table>
        """ % (deformation_type, stn['n']*1000, stn['e']*1000, units, plot, stn['etm'])

        ls = folder1.newlinestring(name=stn_id)

        ls.coords = [(stn['lon'], stn['lat']),
                     (stn['lon'] + stn['e'] * 10,
                      stn['lat'] + stn['n'] * 10 * np.cos(stn['lat']*np.pi/180))]
        ls.style.linestyle.width = 3
        ls.style.linestyle.color = 'ff0000ff'

    for stn in tqdm(discarded, ncols=160, disable=None, desc=' -- Excluded station list'):
        stn_id = stationID(stn)
        plot   = plot_station_param(stn['NetworkCode'], stn['StationCode'], 
                                    deformation_type, units, stn['n'], stn['e'])

        pt = folder2.newpoint(name=stn_id, coords=[(stn['lon'], stn['lat'])])
        pt.stylemap = styles_nok

        pt.description = """<strong>NE (%s): %5.2f %5.2f [%s]</strong><br><br>
        <table width="880" cellpadding="0" cellspacing="0">
        <tr>
        <td align="center" valign="top">
        <strong>Parameters:</strong><br>
        <img src="data:image/png;base64, %s" alt="Observation information" height="300" width="300"/><br>
        <strong>Trajectory model:</strong><br>
        <img src="data:image/png;base64, %s" alt="Observation information" height="750" width="1100"/>
        </p>
        </tr>
        </td>
        </table>
        """ % (deformation_type, stn['n']*1000, stn['e']*1000, units, plot, stn['etm'])

        ls = folder2.newlinestring(name=stn_id)

        ls.coords = [(stn['lon'], stn['lat']),
                     (stn['lon'] + stn['e'] * 10,
                      stn['lat'] + stn['n'] * 10 * np.cos(stn['lat']*np.pi/180))]
        ls.style.linestyle.width = 3
        ls.style.linestyle.color = 'ff0000ff'

    if not os.path.exists('production'):
        os.makedirs('production')

    tqdm.write(' >> Saving kmz...')
    kml.savekmz(kmz)


def process_interseismic(cnn, stnlist, force_stnlist, stack, sigma_cutoff, vel_cutoff, lat_lim, filename, kmz):
    # start by checking that the stations in the list have a linear start (no post-seismic)
    # and more than 2 years of data until the first earthquake or non-linear behavior

    tqdm.write(' >> Analyzing suitability of station list to participate in interseismic model...')
    tqdm.write(' -- latitude cutoff: south %.2f, north %.2f' % (lat_lim[0], lat_lim[1]))
    tqdm.write(' -- velocity cutoff: %.2f mm/yr; output filename: %s' % (vel_cutoff, filename))

    use_station = []
    discarded   = []
    velocities  = []
    # min_lon     =  9999
    # max_lon     = -9999
    # min_lat     =  9999
    # max_lat     = -9999

    for stn in tqdm(stnlist, ncols=160, disable=None):
        try:
            stn_id = stationID(stn)

            etm = pyETM.GamitETM(cnn, stn['NetworkCode'], stn['StationCode'], stack_name=stack)

            use = True
            if stn in force_stnlist:
                tqdm.write(' -- %s was forced to be included in the list' % stn_id)
            else:
                # only check everything is station not included in the force list
                # check that station is within latitude range
                if etm.gamit_soln.lat[0] < lat_lim[0] or \
                   etm.gamit_soln.lat[0] > lat_lim[1]:
                    tqdm.write(' -- %s excluded because it is outside of the latitude limit' % stn_id)
                    use = False

                # check that station has at least 2 years of data
                if etm.gamit_soln.date[-1].fyear - etm.gamit_soln.date[0].fyear < 2 and use:
                    tqdm.write(' -- %s rejected because it has less than two years of observations %s -> %s'
                               % (stn_id,
                                  etm.gamit_soln.date[0].yyyyddd(),
                                  etm.gamit_soln.date[-1].yyyyddd()))
                    use = False

                # other checks
                if etm.A is not None:
                    if len(etm.Jumps.table) > 0 and use:
                        j = next((j
                                  for j in etm.Jumps.table
                                  if j.p.jump_type == pyETM.CO_SEISMIC_JUMP_DECAY and j.fit and \
                                  j.magnitude >= 7 and j.date.fyear < etm.gamit_soln.date[0].fyear + 1.5
                                  ), None)
                        if j:
                            tqdm.write(' -- %s has a Mw %.1f in %s and data starts in %s'
                                       % (stn_id,
                                          j.magnitude, j.date.yyyyddd(), etm.gamit_soln.date[0].yyyyddd()))
                            use = False
                            
                        else:
                            has_eq_jumps = any(True
                                               for j in etm.Jumps.table
                                               if j.p.jump_type == pyETM.CO_SEISMIC_DECAY and j.fit)
                            if has_eq_jumps:
                                tqdm.write(' -- %s has one or more earthquakes before data started in %s'
                                           % (stn_id, etm.gamit_soln.date[0].yyyyddd()))
                                use = False

                    if (etm.factor[0] * 1000 > sigma_cutoff or etm.factor[1] * 1000 > sigma_cutoff) and use:
                        tqdm.write(' -- %s rejected due to large wrms %5.2f %5.2f %5.2f'
                                   % (stn_id,
                                      etm.factor[0] * 1000, etm.factor[1] * 1000, etm.factor[2] * 1000))
                        use = False

                    norm = np.sqrt(np.sum(np.square(etm.Linear.p.params[0:2, 1]*1000)))
                    if norm > vel_cutoff and use:
                        tqdm.write(' -- %s rejected due to large NEU velocity: %5.2f %5.2f %5.2f NE norm %5.2f'
                                   % (stn_id,
                                      etm.Linear.p.params[0, 1] * 1000,
                                      etm.Linear.p.params[1, 1] * 1000,
                                      etm.Linear.p.params[2, 1] * 1000,
                                      norm))
                        use = False
                elif use:
                    tqdm.write(' -- %s too few solutions to calculate ETM' % stn_id)
                    use = False

            def getvel():
                return {'NetworkCode' : etm.NetworkCode,
                        'StationCode' : etm.StationCode,
                        'lat'         : etm.gamit_soln.lat[0],
                        'lon'         : etm.gamit_soln.lon[0],
                        'n'           : etm.Linear.p.params[0, 1],
                        'e'           : etm.Linear.p.params[1, 1],
                        'etm'         : etm.plot(plot_missing  = False, plot_outliers = True, fileio=BytesIO())
                        }

            if use:
                tqdm.write(' -- %s added NEU wrms: %5.2f %5.2f %5.2f NEU vel: %5.2f %5.2f %5.2f'
                           % (stn_id,
                              etm.factor[0]*1000, etm.factor[1]*1000, etm.factor[2]*1000,
                              etm.Linear.p.params[0, 1]*1000,
                              etm.Linear.p.params[1, 1]*1000,
                              etm.Linear.p.params[2, 1]*1000))
                use_station.append(stn)
                v = getvel()
                velocities.append(v)

                #min_lon = min(v['lon'], min_lon)
                #max_lon = max(v['lon'], max_lon)
                #min_lat = min(v['lat'], min_lat)
                #max_lat = max(v['lat'], max_lat)

            elif etm.A is not None:
                discarded.append(getvel())

        except pyETM.pyETMException as e:
            tqdm.write(' -- %s: %s' % (stn_id, str(e)))

    tqdm.write(' >> Total number of stations for linear model: %i' % len(use_station))

    outvar = np.array([(v['NetworkCode'] + '.' + v['StationCode'], v['lon'], v['lat'], v['e'], v['n'])
                       for v in velocities], dtype=[('stn', 'U8'), ('lon', 'float64'), ('lat', 'float64'),
                                                    ('e', 'float64'), ('n', 'float64')])
    np.savetxt(filename, outvar, fmt=("%s",  "%13.8f",  "%12.8f", "%12.8f", "%12.8f"))
    if kmz:
        generate_kmz(kmz, velocities, discarded, 'interseismic', 'mm/yr')


def process_postseismic(cnn, stnlist, force_stnlist, stack, interseimic_filename, event, filename, kmz,
                        prev_events=None, sigma_cutoff=0, lat_lim=0):
    tqdm.write(' >> Analyzing suitability of station list to participate in postseismic model...')
    tqdm.write(' -- output filename: %s' % filename)

    use_station = []
    discarded   = []

    # load the interseismic model
    model = np.loadtxt(interseimic_filename)

    # model[:, 0] -= 360
    params = []

    pgrids = []
    pdates = []

    # check if any prev_events where passed
    if prev_events:
        if np.mod(len(prev_events), 2) != 0:
            raise ValueError('Invalid number of arguments for previous events: needs to be multiple of 2.')
        n = int(len(prev_events) / 2)
        for e, g in zip(prev_events[0:n], prev_events[n:]):
            pgrids.append(np.loadtxt(g))
            pdates.append(Utils.process_date_str(e))

    def getpost():
        return {'NetworkCode': etm.NetworkCode,
                'StationCode': etm.StationCode,
                'lat': etm.gamit_soln.lat[0],
                'lon': etm.gamit_soln.lon[0],
                'n': eq.p.params[0, 0] if eq.p.jump_type is CO_SEISMIC_DECAY
                else eq.p.params[0, 1],
                'e': eq.p.params[1, 0] if eq.p.jump_type is CO_SEISMIC_DECAY
                else eq.p.params[1, 1],
                'etm': etm.plot(plot_missing=False, plot_outliers=True, fileio=BytesIO())}

    for stn in tqdm(stnlist, ncols=160, disable=None):
        stn_id = stationID(stn)
        tqdm.write(' -- Processing station %s' % stn_id)
        try:
            lla = cnn.query_float('SELECT lat,lon FROM stations WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\''
                                  % (stn['NetworkCode'], stn['StationCode']), as_dict=True)[0]

            ve = griddata(model[:, 0:2], model[:, 2] / 1000, (lla['lon'], lla['lat']), method='cubic')
            vn = griddata(model[:, 0:2], model[:, 3] / 1000, (lla['lon'], lla['lat']), method='cubic')

            postseismic = []

            # check if any prev_events where passed
            if prev_events:
                for e, g in zip(pdates, pgrids):
                    # interpolate on the grid
                    pe = griddata(g[:, 0:2], g[:, 2] / 1000, (lla['lon'], lla['lat']), method='cubic')
                    pn = griddata(g[:, 0:2], g[:, 3] / 1000, (lla['lon'], lla['lat']), method='cubic')
                    if np.isnan(pe):
                        pe = 0.
                        pn = 0.
                    postseismic.append({'date': e,
                                        'relaxation': [0.5],
                                        'amplitude': [[pn, pe, 0.]]})
                    tqdm.write('    postseismic removal for %s %6.3f %6.3f %6.3f'
                               % (e.yyyyddd(), float(pn), float(pe), 0.))
            etm = pyETM.GamitETM(cnn, stn['NetworkCode'], stn['StationCode'],
                                 stack_name   = stack,
                                 interseismic = [vn, ve, 0.],
                                 postseismic  = postseismic,
                                 ignore_db_params=True,
                                 plot_polynomial_removed=True,
                                 plot_remove_jumps=True)

            previous_eq = None
            for eq in [e for e in etm.Jumps.table if e.p.jump_type in (CO_SEISMIC_DECAY, CO_SEISMIC_JUMP_DECAY)
                       and e.fit and etm.A is not None]:
                # check if any earthquakes exist before the event of interest that are not included in
                # pdates, which are the events corrected using the postsiesmic removal
                # also check that the postseismic fit the event is less than 2.5 (which means the previous event is
                # poorly constrained)
                if eq.date < event and event.fyear - eq.min_date < 2.5 and eq.date not in pdates:
                    previous_eq = eq.date

                if eq.date == event:
                    tqdm.write('    co-seismic decay detected for event %s (years: %.3f; data points: %i)'
                               % (str(eq.p.jump_date), eq.constrain_years, eq.constrain_data_points))
                    if (eq.constrain_years >= 2.5 and eq.constrain_data_points >= eq.constrain_years * 5
                            and not previous_eq) or stn in force_stnlist:
                        params.append(getpost())
                        tqdm.write('    co-seismic decay added to the list for interpolation')
                    else:
                        if eq.constrain_years < 2.5:
                            tqdm.write('    co-seismic decay not added (less than 2.5 years after event)')
                        elif eq.constrain_data_points < eq.constrain_years * 5:
                            tqdm.write('    co-seismic decay not added (too few data points)')
                        elif previous_eq:
                            tqdm.write('    co-seismic decay not added (postseismic of event on %s not corrected)'
                                       % previous_eq)
                        discarded.append(getpost())
                    break

        except pyETM.pyETMException as e:
            tqdm.write(' -- %s: %s' % (stn_id, str(e)))

    outvar = np.array([(v['NetworkCode'] + '.' + v['StationCode'], v['lon'], v['lat'], v['e'], v['n'])
                       for v in params], dtype=[('stn', 'U8'), ('lon', 'float64'), ('lat', 'float64'),
                                                ('e', 'float64'), ('n', 'float64')])

    np.savetxt(filename, outvar, fmt=("%s",  "%13.8f",  "%12.8f", "%12.8f", "%12.8f"))
    if kmz:
        generate_kmz(kmz, params, discarded, 'postseismic', 'mm')


def main():

    parser = argparse.ArgumentParser(description='Archive operations Main Program')

    parser.add_argument('stack', type=str, nargs=1, metavar='{stack name}',
                        help="Name of the GAMIT stack to use for the trajectories")

    parser.add_argument('-stn', '--stations', nargs='+', type=str, metavar='{station list}', default=[],
                        help="Specify the list of networks/stations given in [net].[stnm] format or just [stnm] "
                             "that will be filtered using the selected field specifications. If [stnm] is "
                             "not unique in the database, all stations with that name will be processed."
                             "Alternatively, a file with the station list can be provided.")

    parser.add_argument('-force_stn', '--force_stations', nargs='+', type=str, metavar='{station list}', default=[],
                        help="Force stations to be included in the selected field. "
                             "Specify the list of networks/stations given in [net].[stnm] format or just [stnm]. "
                             "If [stnm] is not unique in the database, all stations with that name will be processed."
                             "Alternatively, a file with the station list can be provided.")

    parser.add_argument('-lat_lim', '--latitude_limits', nargs=2, type=float, metavar='{min_lat max_lat}',
                        default=[-90, 90],
                        help="Latitude limits (decimal degrees, stations discarded outside of this limit) provided as "
                             "south, north limit. Default is -90 90")

    parser.add_argument('-sigma', '--sigma_cutoff', nargs=1, type=float, metavar='{mm}', default=[2.5],
                        help="Reject stations based on the ETM's wrms (in mm). This filter is not applied for the "
                             "forced station list.")

    parser.add_argument('-vel', '--velocity_cutoff', nargs=1, type=float, metavar='{mm/yr}', default=[50],
                        help="ETM velocity cutoff value to reject stations for velocity interpolation "
                             "(norm of NE in mm/yr).")

    parser.add_argument('-interseismic', '--interseismic_process', nargs='*', type=str,
                        metavar='[velocity_cutoff] [output_filename] [kmz_filename]',
                        help="Process stations for interseismic velocity field computation. Reject stations with "
                             "interseismic velocity > {velocity_cutoff} (default 50 mm/yr). Filename to output the "
                             "selected stations (default filename interseismic.txt). Optionally, specify a kmz "
                             "filename to output the selected and rejected stations with their velocity components and "
                             "ETMs embedded in the kmz (default no kmz).")

    parser.add_argument('-postseismic', '--postseismic_process', nargs='+', type=str,
                        metavar='{interseismic_grid} {event_date} {output_filename} {kmz_filename} [event_date_1] '
                                '[event_date_2] ... [event_grid_1] [event_grid_2] ...',
                        help="Process stations for postseismic field computation. Interseismic removal is done using "
                             "{interseismic_grid}. The event parameters to be extracted from the ETMs correspond to "
                             "seismic event given in {event_date}. Resulting parameters will be written to "
                             "{output_filename}. Additionally, provide [event_date_n] and [event_grid_n] if a previous "
                             "postsiesmic processes should be removed from the ETMs. If no [event_date_n] are given, "
                             "then any previous events are ignored (but they could still be present in the ETM fit).")

    add_version_argument(parser)

    args = parser.parse_args()

    cnn = dbConnection.Cnn("gnss_data.cfg")

    # station list
    stnlist       = Utils.process_stnlist(cnn, args.stations)
    force_stnlist = Utils.process_stnlist(cnn, args.force_stations, summary_title='Forced station list:')

    # get the station list
    if args.interseismic_process is not None:
        # defaults
        l = len(args.interseismic_process)
        args.interseismic_process += ['50.', 'interseismic.txt', None][min(l, 3):]

        process_interseismic(cnn, stnlist, force_stnlist,
                             args.stack[0],
                             args.sigma_cutoff[0],
                             float(args.interseismic_process[0]),
                             args.latitude_limits,
                             args.interseismic_process[1],
                             args.interseismic_process[2])

    if args.postseismic_process is not None:
        process_postseismic(cnn, stnlist, force_stnlist,
                            args.stack[0],
                            args.postseismic_process[0],
                            Utils.process_date_str(args.postseismic_process[1]),
                            args.postseismic_process[2], args.postseismic_process[3],
                            args.postseismic_process[4:], 0, 0)


if __name__ == '__main__':
    main()
