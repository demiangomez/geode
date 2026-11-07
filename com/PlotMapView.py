#!/usr/bin/env python
"""
Project: Geodetic Database Engine (GeoDE)
Date: 6/12/18 10:28 AM
Author: Demian D. Gomez
"""

import argparse
import os
import numpy as np
from tqdm import tqdm
import simplekml
from io import BytesIO
import base64

# app
from geode.pyETM import CO_SEISMIC_JUMP_DECAY, CO_SEISMIC_DECAY, LABEL
from geode import Utils
from geode.Utils import stationID, add_version_argument
from scipy.interpolate import griddata
from geode import pyETM
from geode import dbConnection


def generate_kmz(kmz, stations):

    tqdm.write(' >> Generating KML (see production directory)...')

    kml = simplekml.Kml()
    folder1 = kml.newfolder(name='stations')

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

        pt = folder1.newpoint(name=stn_id, coords=[(stn['lon'], stn['lat'])])
        pt.stylemap = styles_ok

        pt.description = """
        <strong>Previous earthquakes:</strong><br>
        %s
        <br>
        <table width="880" cellpadding="0" cellspacing="0">
        <tr>
        <td align="center" valign="top">
        <strong>Map view:</strong><br>
        <img src="data:image/png;base64, %s" alt="Observation information" height="800" width="800"/>
        </p>
        <strong>Trajectory model:</strong><br>
        <img src="data:image/png;base64, %s" alt="Observation information" height="750" width="1100"/>
        </p>
        </tr>
        </td>
        </table>
        """ % ('<br>'.join(stn['models']), stn['map'], stn['etm'])

    tqdm.write(' >> Saving kmz...')
    kml.savekmz(kmz)


def plot_map_view(pngfile, etm, lneu, fil, event, co_jump):

    import matplotlib.pyplot as plt

    f, axis = plt.subplots(nrows=1, ncols=1, figsize=(10, 10))  # type: plt.subplots

    fneu = etm.factor * 1000

    f.suptitle(LABEL('station') + ' %s (%s %.2f%%/%.2f%%) lat: %.5f lon: %.5f - Event: %s\n'
                                  '%s\n%s\n'
                                  'NEU wrms [mm]: %5.2f %5.2f %5.2f %s %s' %
               (stationID(etm),
                etm.soln.stack_name.upper(),
                etm.soln.completion,
                etm.soln.span_completion,
                etm.soln.lat,
                etm.soln.lon,
                event.yyyyddd(),
                etm.Linear.print_parameters(np.array([etm.soln.auto_x, etm.soln.auto_y, etm.soln.auto_z]),
                                            etm.soln.lat, etm.soln.lon),
                etm.Periodic.print_parameters(),
                fneu[0],
                fneu[1],
                fneu[2],
                '' if not etm.plot_jumps_removed else LABEL('jumps removed'),
                '' if not etm.plot_polynomial_removed else LABEL('polynomial removed')),
               fontsize=9, family='monospace')

    # aux variable for plotting the circle
    an = np.linspace(0, 2 * np.pi, 100)

    ax = axis

    aux = np.all([etm.soln.t > event.fyear, fil], axis=0)
    s = ax.scatter(lneu[1][aux], lneu[0][aux], c=etm.soln.t[aux], s=2)

    for j in [j for j in etm.Jumps.table]:
        if j.date == event:
            tqdm.write(' -- Plotting coseismic jump for event on ' + str(event))
            jm = np.sqrt(np.square(co_jump[0]) + np.square(co_jump[1]))
            #ax.plot(jm * np.cos(an) * 1000, jm * np.sin(an) * 1000)
            ax.plot(co_jump[1] * 1000, co_jump[0] * 1000, 'xb', markersize=2)
            ax.quiver(co_jump[1] * 1000, co_jump[0] * 1000, angles='xy', scale_units='xy', scale=1)

    #ax.set_aspect('equal', 'box')
    ax.grid(True)
    plt.colorbar(s)
    # save / show plot
    if pngfile is not None:
        plt.savefig(pngfile)

    fileio = BytesIO()
    plt.savefig(fileio, format='png')
    # plt.show()
    fileio.seek(0)  # rewind to beginning of file
    plt.close()
    return base64.b64encode(fileio.getvalue()).decode()


def main():

    parser = argparse.ArgumentParser(description='Archive operations Main Program')

    parser.add_argument('stack', type=str, nargs=1, metavar='{stack name}',
                        help="Name of the GAMIT stack to use for the trajectories")

    parser.add_argument('-stn', '--stations', nargs='+', type=str, metavar='{station list}', default=[],
                        help="Specify the list of networks/stations given in [net].[stnm] format or just [stnm] "
                             "that will be filtered using the selected field specifications. If [stnm] is "
                             "not unique in the database, all stations with that name will be processed."
                             "Alternatively, a file with the station list can be provided.")

    parser.add_argument('-inter', '--interseismic_model', nargs=1, type=str,
                        metavar='{interseismic_model}',
                        help="Interseismic removal is done using {interseismic_grid}.")

    parser.add_argument('-post', '--postseismic', nargs='+', type=str,
                        metavar='{event_date} [event_date_1] [event_date_2] ... [event_grid_1] [event_grid_2] ...',
                        help="Interseismic removal is done using the grid provided in --interseismic_model. The event "
                             "postseimic to be plotted correspond to seismic event given in {event_date}. "
                             "Additionally, provide [event_date_n] and [event_grid_n] if a previous "
                             "postsiesmic processes should be removed from the ETMs. If no [event_date_n] are given, "
                             "then any previous events are ignored (but they could still be present in the ETM fit).")

    parser.add_argument('-co', '--coseismic', nargs=1, type=str,
                        metavar='{coseismic_grid}',
                        help='Coseismic grid to use for stations that were not active by the time of the event. This '
                             'assumes that you are providing the grid of the event specific in -post')

    parser.add_argument('-dir', '--directory', type=str,
                        help="Directory to save the resulting PNG files. If not specified, assumed to be the "
                             "production directory")

    parser.add_argument('-missing', '--plot_missing_solutions', action='store_true', default=False,
                        help='Plot the missing solutions in the ETMs stored in the KMZ file (might take longer '
                             'to produce the file).')

    add_version_argument(parser)

    args = parser.parse_args()
    cnn = dbConnection.Cnn('gnss_data.cfg')

    if args.directory:
        if not os.path.exists(args.directory):
            os.mkdir(args.directory)
    else:
        if not os.path.exists('production'):
            os.mkdir('production')
        args.directory = 'production'

    # stack name to use for the plots
    stack = args.stack[0]

    tqdm.write(' >> Using stack %s' % stack)

    # station list
    stnlist  = Utils.process_stnlist(cnn, args.stations)

    # load the interseismic model
    inter = np.loadtxt(args.interseismic_model[0])
    # the event to be plotted
    event = Utils.process_date_str(args.postseismic[0])
    # previous events to be removed from the ETM
    prev_events = args.postseismic[1:]

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

    # load the coseismic grid
    if args.coseismic:
        cgrid = np.loadtxt(args.coseismic[0])
        # specify which types should be allowed (if using a grid for the coseismic, allow CO_SEISMIC_DECAY
        TYPES = (CO_SEISMIC_DECAY, CO_SEISMIC_JUMP_DECAY)
    else:
        cgrid = None
        TYPES = (CO_SEISMIC_JUMP_DECAY, )

    def getpost():
        # fetm = pyETM.GamitETM(cnn, stn['NetworkCode'], stn['StationCode'], stack_name=stack,
        #                      plot_polynomial_removed=True)
        return {'NetworkCode': etm.NetworkCode,
                'StationCode': etm.StationCode,
                'lat': etm.gamit_soln.lat[0],
                'lon': etm.gamit_soln.lon[0],
                'map': plt,
                'etm': etm.plot(plot_missing=args.plot_missing_solutions, plot_outliers=True, fileio=BytesIO()),
                'models': app_models}

    map_views = []

    for stn in tqdm(stnlist, ncols=160, disable=None):
        stn_id = stationID(stn)
        tqdm.write(' -- Processing station %s' % stn_id)
        try:
            lla = cnn.query_float('SELECT lat,lon FROM stations WHERE "NetworkCode" = \'%s\' AND "StationCode" = \'%s\''
                                  % (stn['NetworkCode'], stn['StationCode']), as_dict=True)[0]

            ve = griddata(inter[:, 0:2], inter[:, 2] / 1000, (lla['lon'], lla['lat']), method='cubic')
            vn = griddata(inter[:, 0:2], inter[:, 3] / 1000, (lla['lon'], lla['lat']), method='cubic')

            postseismic = []
            app_models  = []
            # check if any prev_events where passed
            if prev_events:
                etm = pyETM.GamitETM(cnn, stn['NetworkCode'], stn['StationCode'],
                                     stack_name=stack,
                                     interseismic=[vn, ve, 0.])

                for e, g in zip(pdates, pgrids):
                    # only correct the postseismic if the event is not fully constrained
                    # i.e. if the jump is just CO_SEISMIC_DECAY, then correct, otherwise use ETM
                    # interpolate on the grid
                    # stations are ASSUMED to be near the event being corrected, so if the event is not in the list,
                    # the removal of the postsesimic will be forced

                    def apply_model():
                        pe = griddata(g[:, 0:2], g[:, 2] / 1000, (lla['lon'], lla['lat']), method='cubic')
                        pn = griddata(g[:, 0:2], g[:, 3] / 1000, (lla['lon'], lla['lat']), method='cubic')
                        if np.isnan(pe):
                            pe = 0.
                            pn = 0.
                        postseismic.append({'date': e, 'relaxation': [0.5], 'amplitude': [[pn, pe, 0.]]})
                        tqdm.write('    postseismic removal for %s %6.3f %6.3f %6.3f using model'
                                   % (e.yyyyddd(), float(pn), float(pe), 0.))
                        app_models.append('%s using model %6.3f %6.3f %6.3f for postseismic'
                                          % (e.yyyyddd(), float(pn), float(pe), 0.))

                    prev_event_j = [j for j in etm.Jumps.table if j.date == e]
                    if prev_event_j:
                        # if prev_event_j has something, it can be adjusted or not
                        if prev_event_j[0].p.jump_type == CO_SEISMIC_DECAY:
                            apply_model()
                        else:
                            tqdm.write('    postseismic removal for %s using ETM' % e.yyyyddd())
                            app_models.append('%s using ETM for postseismic' % e.yyyyddd())
                    else:
                        # event was not in the list, force it
                        apply_model()

            # create a list of effective corrections
            epdates = [p['date'] for p in postseismic]

            etm = pyETM.GamitETM(cnn, stn['NetworkCode'], stn['StationCode'],
                                 stack_name   = stack,
                                 interseismic = [vn, ve, 0.],
                                 postseismic  = postseismic,
                                 ignore_db_params=True,
                                 plot_polynomial_removed=True,
                                 plot_remove_jumps=True)

            e = [j for j in etm.Jumps.table if j.p.jump_type in TYPES and j.fit and j.date == event]

            # the station needs to have seen the event as specified in TYPES
            if e:
                # observed
                oj = np.zeros((3, etm.soln.t.shape[0]))
                of = np.zeros((3, etm.soln.t.shape[0]))
                # modeled
                mj = np.zeros((3, etm.soln.ts.shape[0]))
                mf = np.zeros((3, etm.soln.ts.shape[0]))

                for j in [j for j in etm.Jumps.table if j.p.jump_type is not CO_SEISMIC_DECAY and j.fit]:
                    a = j.eval(etm.soln.t)
                    b = j.eval(etm.soln.ts)
                    if j.p.jump_type is CO_SEISMIC_JUMP_DECAY and (j.date == event or j.date in epdates):
                        # if jump is the event in question, just remove the jump, not the decay
                        # tqdm.write(str(j.date))
                        # tqdm.write(str(j.p.params))
                        oj = oj + np.array([(np.dot(a[:, 0], j.p.params[i, 0])) * 1000 for i in range(3)])
                        mj = mj + np.array([(np.dot(b[:, 0], j.p.params[i, 0])) * 1000 for i in range(3)])
                    elif j.p.jump_type is CO_SEISMIC_JUMP_DECAY and not j.date == event:
                        # if jump is NOT the event in question, remove everything, unless the jump is in the list of
                        # externally-corrected decays pdates (see previous if-branch)
                        # tqdm.write(str(j.date))
                        # tqdm.write(str(j.p.params))
                        oj = oj + np.array([(np.dot(a, j.p.params[i])) * 1000 for i in range(3)])
                        mj = mj + np.array([(np.dot(b, j.p.params[i])) * 1000 for i in range(3)])
                    else:
                        # jump has no decay, remove everything
                        oj = oj + np.array([(np.dot(a, j.p.params[i])) * 1000 for i in range(3)])
                        mj = mj + np.array([(np.dot(b, j.p.params[i])) * 1000 for i in range(3)])

                op = np.array([(np.dot(etm.Linear.get_design_ts(etm.soln.t), etm.Linear.p.params[i])) * 1000
                               for i in range(3)])

                mp = np.array([(np.dot(etm.Linear.get_design_ts(etm.soln.ts), etm.Linear.p.params[i])) * 1000
                               for i in range(3)])

                if etm.Periodic.frequency_count > 1:
                    of = np.array([(np.dot(etm.Periodic.get_design_ts(etm.soln.t), etm.Periodic.p.params[i])) * 1000
                                   for i in range(3)])
                    mf = np.array([(np.dot(etm.Periodic.get_design_ts(etm.soln.ts), etm.Periodic.p.params[i])) * 1000
                                   for i in range(3)])

                # data free of interseismic and jump + postseismic removal
                m = np.array([(np.dot(etm.As, etm.C[i])) * 1000 - mj[i] - mp[i] - mf[i] for i in range(3)])

                # decide if we need to remove an initial offset or not
                # if station only saw the decay, then don't remove (part of the signal of interest)
                # if station saw the jump of the event, then remove any previous offsets (these are not important)
                if e[0].p.jump_type == CO_SEISMIC_DECAY:
                    data = etm.l * 1000 - op - oj - of
                else:
                    # remove the first model value to make sure the time series start at zero
                    data = etm.l * 1000 - op - oj - of - np.tile(m[:, 0], (oj.shape[1], 1)).transpose()

                filt = etm.F[0] * etm.F[1] * etm.F[2]

                co_jump = np.zeros(2)

                if cgrid is not None and e[0].p.jump_type == CO_SEISMIC_DECAY:
                    # there is a coseismic grid and the event jump was not observed, use the provided grid
                    co_jump[0] = griddata(cgrid[:, 0:2], cgrid[:, 2] / 1000, (lla['lon'], lla['lat']), method='cubic')
                    co_jump[1] = griddata(cgrid[:, 0:2], cgrid[:, 3] / 1000, (lla['lon'], lla['lat']), method='cubic')
                    app_models.append('%s using modeled coseismic component NEU = (%6.3f %6.3f %6.3f) for event'
                                      % (e[0].date.yyyyddd(), float(co_jump[0]), float(co_jump[1]), 0.))
                else:
                    co_jump[0] = e[0].p.params[0, 0]
                    co_jump[1] = e[0].p.params[1, 0]
                    app_models.append('%s using observed coseismic component NEU = (%6.3f %6.3f %6.3f) for event'
                                      % (e[0].date.yyyyddd(), float(co_jump[0]), float(co_jump[1]), 0.))

                png = os.path.join(args.directory, '%s_mapview.png' % stationID(etm))
                plt = plot_map_view(png, etm, data, filt, event, co_jump)

                map_views.append(getpost())

            else:
                tqdm.write(' -- The requested event is not present for this station')

        except pyETM.pyETMException as e:
            tqdm.write(' -- %s: %s' % (stn_id, str(e)))

    generate_kmz(os.path.join(args.directory, 'mapviews_%s_%03i.kmz' % (event.yyyy(), event.doy)), map_views)


if __name__ == '__main__':
    main()
