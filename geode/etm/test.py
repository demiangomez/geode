import logging
import numpy as np

from geode.pyDate import Date
from geode.metadata import station_info
from geode.Utils import load_json, process_date_str
from geode.etm.core.type_declarations import CovarianceFunction, SolutionType, EtmSolutionType
from geode.etm.core.etm_engine import EtmEngine
from geode.etm.core.etm_config import EtmConfig
from geode.etm.etm_functions.polynomial import PolynomialFunction
from geode.etm.etm_functions.periodic import PeriodicFunction
from geode.etm.etm_functions.jumps import JumpFunction, JumpType
from geode.dbConnection import Cnn
from geode.etm.core.logging_config import setup_etm_logging
from geode.etm.least_squares.least_squares import AdjustmentModels

setup_etm_logging(level=logging.DEBUG)

cnn = Cnn('/home/demian/pg_osu/gnss_data.cfg')

def get_prefit_models(config, args):

    user_prefit_models = []

    if args:
        function_map_str = {'poly': 'polynomial', 'per': 'periodic', 'jump': 'jump'}
        function_map = {'poly': PolynomialFunction, 'per': PeriodicFunction, 'jump': JumpFunction}
        # begin by loading the json
        functions = load_json(args[0])['functions']
        # read current function
        current_funct = args[1]
        # set i at the correct location
        i = 2
        while i < len(args):
            # find the json info for this function
            print(' -- Adding ' + function_map_str[current_funct] + ' as detrending')
            if current_funct == 'jump':
                # if it is a jump assimilate the date also
                mjd = process_date_str(args[i]).mjd
                # find the correct jump
                jf = [funct for funct in functions if funct['object'] == function_map_str[current_funct]
                      and funct['jump_date']['mjd'] == mjd]
                # create instance of function
                funct = function_map[current_funct](config,
                                                    time_vector=np.array([0]),
                                                    date=Date(**jf[0]['jump_date']),
                                                    jump_type=JumpType.UNDETERMINED,
                                                    fit=True)
                i += 1
            else:
                jf = [funct for funct in functions if funct['object'] == function_map_str[current_funct]]
                # create instance of function
                funct = function_map[current_funct](config)

            if not len(jf):
                raise ValueError('Constraint function ' + function_map_str[current_funct] +
                                 ' with matching arguments could not be found')

            funct.load_from_json(jf[0])
            # append to constraints
            user_prefit_models.append(funct)
            i += 1

        for funct in user_prefit_models:
            print(funct.p.params)

    return user_prefit_models

#stn = pyStationInfo.StationInfo(cnn=None, NetworkCode='arg', StationCode='igm1')
#hh = stn.parse_station_info('/home/demian/pg_osu/steps.ngl')

config = EtmConfig(json_file='/home/demian/pg_osu/vel-ar_double/arg.3aro_ppp.json')
etm = EtmEngine(config)
etm.run_adjustment(try_loading_db=False, try_save_to_db=False)
config.plotting_config.filename = '/home/demian/pg_osu/'
etm.plot()

config = EtmConfig('arg', '3aro', cnn=cnn)
config.solution.solution_type = SolutionType.PPP
# config.solution.stack_name = 'igs14'

config.modeling.least_squares_strategy.adjustment_model = AdjustmentModels.ROBUST_LEAST_SQUARES
config.modeling.least_squares_strategy.covariance_function = CovarianceFunction.ARMA
# config.modeling.relaxation = np.array([1.0])
config.modeling.fit_auto_detected_jumps = False
config.modeling.fit_auto_detected_jumps_method = 'dbscan'
# only fit the data within this interval
#config.modeling.data_model_window = [(1995.0, 2005.9999), (2009.606849, 2025.5)]
#poly_model = PolynomialFunction(config)
#periodic_model = PeriodicFunction(config)
#illapel = JumpFunction(config, time_vector=np.array([0]), date=Date(year=2015, doy=259),
#                       jump_type=JumpType.COSEISMIC_JUMP_DECAY, fit=False)

#poly_model.p.params = [np.array([np.nan, 0.115]), np.array([np.nan, np.nan]), np.array([np.nan, 0.0])]
#poly_model.p.sigmas = [np.array([np.nan, 0.0001]), np.array([np.nan, 0.0001]), np.array([np.nan, 0.00001])]

#periodic_model.p.params = [np.array([0.0, 0.0, 0.0, 0.0]),
#                           np.array([0.0, 0.0, 0.012, 0.001]),
#                           np.array([0.0, 0.0, 0.0, 0.0])]

#illapel.p.params = [np.array([0.0, np.nan, np.nan]), np.array([0.0, np.nan, np.nan]), np.array([0.0, np.nan, np.nan])]
#illapel.p.sigmas = [np.array([0.00001, np.nan, np.nan]), np.array([0.00001, np.nan, np.nan]), np.array([0.00001, np.nan, np.nan])]

#config.modeling.prefit_models = get_prefit_models(config, ['/home/demian/pg_osu/arg.mzac_ppp.json', 'jump', '2010_058'])
# config.modeling.least_squares_strategy.constraints.extend([poly_model, illapel])

# options for plotting
config.plotting_config.filename = '/home/demian/pg_osu/'
config.plotting_config.plot_show_outliers = False
config.plotting_config.save_kwargs={'dpi': 150}
config.plotting_config.plot_remove_polynomial = True
config.plotting_config.plot_remove_periodic = False
config.plotting_config.plot_remove_stochastic_signal = True
config.plotting_config.plot_remove_stochastic_noise = False
config.plotting_config.plot_residuals_mode = False
config.plotting_config.plot_remove_jumps = False
#config.plotting_config.plot_time_window = [2015.0, 2019.0]

config.validate_config()

etm = EtmEngine(config, cnn=cnn)
etm.run_adjustment(cnn=cnn, try_save_to_db=True, try_loading_db=True)
a = etm.get_position([Date(fyear=2004.0), Date(fyear=2004.1)], EtmSolutionType.OBSERVATION)
etm.save_etm(filename='/home/demian/pg_osu/',
             dump_observations=True,
             dump_design_matrix=True,
             dump_raw_results=True,
             dump_functions=True,
             dump_model=True)
etm.plot()
etm.plot_hist()

print('done')