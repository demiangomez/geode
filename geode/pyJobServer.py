"""
Project: Geodetic Database Engine (GeoDE)
Date: 9/13/17 6:30 PM
Author: Demian D. Gomez

This module handles the cluster nodes and checks all the necessary dependencies
before sending jobs to each node
"""

import time
import _thread
import queue
import traceback
import os
import threading

# deps
from tqdm import tqdm
import dispy
import dispy.httpd


def test_node(check_gamit_tables=None, check_archive=True, check_executables=True, check_atx=True, software_sync=()):
    # test node: function that makes sure that all required packages and tools are present in the nodes
    import traceback
    import platform
    import os
    import sys

    def check_tab_file(tabfile, date):
        if os.path.isfile(tabfile):
            # file exists, check contents
            with open(tabfile, 'rt', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()

            tabdate = pyDate.Date(mjd=lines[-1].split()[0])
            if tabdate < date:
                return ' -- %s: Last entry in %s is %s but processing %s' \
                       % (platform.node(), tabfile, tabdate.yyyyddd(), date.yyyyddd())
            return []
        else:
            return ' -- %s: Could not find file %s' % (platform.node(), tabfile)

    # BEFORE ANYTHING! check the python version
    version = sys.version_info
    # if version.major > 2 or version.minor < 7 or (version.micro < 12 and version.minor <= 7):
    #     return ' -- %s: Incorrect Python version: %i.%i.%i. Recommended version >= 2.7.12' \
    #            % (platform.node(), version.major, version.minor, version.micro)
    if version.major < 3:
        return ' -- %s: Incorrect Python version: %i.%i.%i. Recommended version >= 3.0.0' \
               % (platform.node(), version.major, version.minor, version.micro)

    # start importing the modules needed
    try:
        print(' >> Testing python imports')
        import shutil
        import datetime
        import time
        import uuid
        import traceback
        # deps
        import numpy
        import dirsync
        # app
        from geode import dbConnection
        from geode import pyProducts
        from geode import pyOptions
        from geode import pyRunWithRetry
        from geode import pyDate

    except:
        return ' -- %s: Problem found while importing modules:\n%s' % (platform.node(), traceback.format_exc())

    try:
        if len(software_sync) > 0:
            print(' >> Syncing directories')
            # synchronize directories listed in the src and dst arguments
            from dirsync import sync

            for source_dest in software_sync:
                if isinstance(source_dest, str) and ',' in source_dest:
                    s = source_dest.split(',')[0].strip()
                    d = source_dest.split(',')[1].strip()

                    print('    -- Synchronizing %s -> %s' % (s, d))

                    updated = sync(s, d, 'sync', purge=True, create=True)

                    for f in updated:
                        print('    -- Updated %s' % f)

    except:
        return ' -- %s: Problem found while synchronizing software:\n%s ' % (platform.node(), traceback.format_exc())

    # continue with a test SQL connection
    # make sure that the gnss_data.cfg is present
    try:
        print(' >> Testing database connection')
        cnn = dbConnection.Cnn('gnss_data.cfg')

        q = cnn.query('SELECT count(*) FROM networks')
    except:
        return ' -- %s: Problem found while connecting to postgres:\n%s ' % (platform.node(), traceback.format_exc())

    # make sure we can create the production folder
    try:
        print(' >> Testing access to production dir')
        test_dir = os.path.join('production/node_test')
        if not os.path.exists(test_dir):
            os.makedirs(test_dir)
    except:
        return ' -- %s: Could not create production folder:\n%s ' % (platform.node(), traceback.format_exc())

    # test
    try:
        print(' >> Testing gnss_data.cfg file access')
        Config = pyOptions.ReadOptions('gnss_data.cfg')
    except:
        return ' -- %s: Problem while reading config file and/or testing archive access:\n%s' \
               % (platform.node(), traceback.format_exc())

    if check_archive:
        print(' >> Testing access to archive %s' % Config.archive_path)
        # check that all paths exist and can be reached
        if not os.path.exists(Config.archive_path):
            return ' -- %s: Could not reach archive path %s' % (platform.node(), Config.archive_path)

        print(' >> Testing access to repository %s' % Config.repository)
        if not os.path.exists(Config.repository):
            return ' -- %s: Could not reach repository path %s' % (platform.node(), Config.repository)

        # pick a test date to replace any possible parameters in the config file
        date = pyDate.Date(year=2010, doy=1)
        try:
            print(' >> Testing access to broadcast orbits %s' % Config.brdc_path)
            brdc = pyProducts.GetBrdcOrbits(Config.brdc_path, date, test_dir)
        except:
            return ' -- %s: Problem while testing the broadcast ephemeris archive (%s) access:\n%s' \
                   % (platform.node(), Config.brdc_path, traceback.format_exc())

        try:
            print(' >> Testing access to precise orbits %s' % Config.sp3_path)
            sp3 = pyProducts.GetSp3Orbits(Config.sp3_path, date, Config.sp3types, test_dir)
        except:
            return ' -- %s: Problem while testing the sp3 orbits archive (%s) access:\n%s' \
                   % (platform.node(), Config.sp3_path, traceback.format_exc())

    if check_executables:
        # check that all executables and GAMIT bins are in the path
        for prg in ('crz2rnx', 'crx2rnx', 'rnx2crx', 'rnx2crz', 'gfzrnx_lx', 'svpos', 'tform',
                    'sh_rx2apr', 'doy', 'sed', 'compress'):
            print(' >> Testing %s' % prg)
            stdout, _ = pyRunWithRetry.RunCommand('which ' + prg, 10).run_shell()
            if not stdout.strip():
                return ' -- %s: Could not find path to %s' % (platform.node(), prg)

        # check grdtab and ppp from the config file
        for opt in ('grdtab', 'otlgrid', 'ppp_exe'):
            path = Config.options[opt]
            print(' >> Testing access to %s' % path)
            if not os.path.isfile(path):
                return ' -- %s: Could not find %s in %s' % (platform.node(), opt, path)

        ppp_path = Config.options['ppp_path']
        for f in ('gpsppp.stc', 'gpsppp.svb_gps_yrly', 'gpsppp.flt', 'gpsppp.stc', 'gpsppp.met'):
            print(' >> Testing access to %s' % f)
            if not os.path.isfile(os.path.join(ppp_path, f)):
                return ' -- %s: Could not find %s in %s' % (platform.node(), f, ppp_path)

    if check_atx:
        for frame in Config.options['frames']:
            print(' >> Testing access to %s %s' % (frame, frame['atx']))
            if not os.path.isfile(frame['atx']):
                return ' -- %s: Could not find atx in %s' % (platform.node(), frame['atx'])

    if check_gamit_tables is not None:
        # check the gamit tables if not none

        date = check_gamit_tables[0]
        eop  = check_gamit_tables[1]

        gg     = os.path.expanduser('~/gg')
        tables = os.path.expanduser('~/gg/tables')

        if not os.path.isdir(gg):
            return ' -- %s: Could not GAMIT installation dir (gg)' % (platform.node())

        elif not os.path.isdir(tables):
            return ' -- %s: Could not GAMIT tables dir (gg)' % (platform.node())

        # DDG: deprecated -> GAMIT now uses a single nbody file (binary)
        # for t_name in ('luntab.' + date.yyyy() + '.J2000',
        #               'soltab.' + date.yyyy() + '.J2000',
        #               'ut1.' + eop,
        #               # leapseconds
        #               # vmf1
        #               'pole.' + eop
        #               ):
        #    result = check_tab_file(os.path.join(tables, t_name), date)
        #    if result:
        #        return result

        # fes_cmc consistency

    return ' -- %s: Test passed!' % platform.node()


def setup(modules):
    """
    function to import modules in the nodes
    :return: 0
    """
    from tqdm import tqdm

    tqdm.write(' >> Initializing node...')
    for module in modules:
        # create a global object containing our module
        # DDG: to support imports like geode.pyDate, ignoring the geode and only accessing pyDate
        # DDG: new behavior: if module.submodule is called, import the submodule as a global
        if '.' in module:
            module_obj = __import__(module, fromlist=[None])
            globals()[module.split('.')[-1]] = module_obj
            tqdm.write(' >> Importing module %s as %s' % (module, module.split('.')[-1]))
        else:
            module_obj = __import__(module)
            globals()[module] = module_obj
            tqdm.write(' >> Importing module %s' % module)

    return 0


class JobServer:

    def check_cluster(self, status, node, job):

        if status == dispy.DispyNode.Initialized:
            with self._node_init_lock:
                print(' -- Checking node %s (%i CPUs)...' % (node.name, node.avail_cpus))

                try:
                    self.cluster.send_file('gnss_data.cfg', node)
                except Exception as e:
                    print(' -- %s: Could not send gnss_data.cfg: %s' % (node.name, e))
                    return

                job = self.cluster.submit_node(node, self.check_gamit_tables, self.check_archive, self.check_atx,
                                               self.software_sync)

                if job is None:
                    print(' -- %s: submit_node returned None (node may be unavailable)' % node.name)
                    return

                # poll with timeout instead of cluster.wait() to avoid hanging on a dead node
                deadline = time.time() + 60
                while job.status not in (dispy.DispyJob.Finished,
                                         dispy.DispyJob.Cancelled,
                                         dispy.DispyJob.Abandoned):
                    if time.time() > deadline:
                        print(' -- %s: test job timed out, skipping node' % node.name)
                        return
                    time.sleep(0.5)

                self.result.append(job.result)
                self.nodes.append(node)

    def __init__(self, Config,
                 check_gamit_tables=None,
                 check_archive=True,
                 check_executables=True,
                 check_atx=True,
                 run_parallel=True, software_sync=()):
        """
        initialize the jobserver
        :param Config: pyOptions.ReadOptions instance
        :param check_gamit_tables: check or not the tables in GAMIT
        :param check_archive: check if can access archive files and folder structure
        :param run_parallel: override the configuration in gnss_data.cfg
        :param software_sync: list of strings with remote and local paths of software to be synchronized
        """
        self.check_gamit_tables = check_gamit_tables
        self.check_archive      = check_archive
        self.check_executables  = check_executables
        self.check_atx          = check_atx
        self.software_sync      = software_sync

        self.nodes           = []
        self.result          = []
        self.jobs            = []
        self._node_init_lock = threading.Lock()
        self.run_parallel = Config.run_parallel and run_parallel
        self.delay        = Config.cluster_delay
        self.verbose      = False
        self.close        = False
        # variable with ip address for multi-homed systems
        self.ip_address   = Config.options['ip_address']

        # vars to store the http_server and the progress bar (if needed)
        self.progress_bar = None
        self.http_server  = None
        self.callback     = None
        self.function     = None
        self.modules      = []

        self.on_nodes_changed = None
        self.job_runner_inbox = queue.PriorityQueue()
        self.node_cleanup = None

        tqdm.write(" ==== Starting JobServer(dispy) ====")

        # check that the run_parallel option is activated
        if self.run_parallel:
            node_list = Config.options['node_list']
            if node_list is None or not node_list.strip():
                # no explicit list, find all
                servers = ['*']
            else:
                # use the provided explicit list of nodes
                servers = [n for n in Config.options['node_list'].split(',') if n]

            # initialize the cluster
            # if explicitly declared, then we might have a multi-homed computer system
            self.cluster = dispy.JobCluster(test_node,
                                            servers,
                                            recover_file   = 'pg.dat',
                                            pulse_interval = 10,
                                            ping_interval  = 10,
                                            cluster_status = self.check_cluster,
                                            host           = self.ip_address
                                                             if type(self.ip_address) is list or self.ip_address is None
                                                             else [self.ip_address])

            # discover the available nodes
            self.cluster.discover_nodes(servers)

            # wait for all nodes
            tqdm.write(" >> Waiting %d seconds to discover all nodes... " % self.delay)
            time.sleep(self.delay)

            # if no nodes were found, stop
            if not len(self.nodes):
                print(' >> No nodes could be found. Check ip_address in gnss_data.cfg if cluster has more than one ' \
                      'Ethernet card and check the node_list to make sure you have the correct IP addresses.')
                # terminate execution if problems were found
                self.cluster.close()
                os._exit(1)

            for r in self.result:
                if r is None or 'Test passed!' not in r:
                    print(r if r is not None else ' -- Node returned no result (job crashed on the remote node)')
                    print(' >> Errors were encountered during initialization. Check messages.')
                    # terminate execution if problems were found
                    self.cluster.close()
                    os._exit(1)

            self.cluster.close()
        else:
            print(' >> Parallel processing deactivated by user')
            r = test_node(check_gamit_tables=check_gamit_tables, check_archive=check_archive,
                          check_executables=check_executables, check_atx=check_atx)
            if r is None or 'Test passed!' not in r:
                print(r if r is not None else ' -- test_node returned no result')
                print(' >> Errors were encountered during initialization. Check messages.')
                os._exit(1)

    def create_cluster(self, function, deps=(), callback=None, progress_bar=None, verbose=False, modules=(),
                       on_nodes_changed=None,
                       node_setup=None,
                       node_cleanup=None
                       ):

        self.jobs     = []
        self.callback = callback
        self.function = function
        self.verbose  = verbose
        self.close    = True
        self.on_nodes_changed = on_nodes_changed
        self.node_cleanup     = node_cleanup

        # check that the modules exist and that there are no typos passed
        try:
            setup(modules)
        except Exception as e:
            tqdm.write('There were problems during module imports in create_cluster: ' + str(e))
            os._exit(1)

        if not self.run_parallel:
            if node_setup:
                node_setup()
            _thread.start_new_thread(self._job_runner_thread, ())
        else:
            # DDG: NodeAllocate is used to pass the arguments to setup during node initialization
            self.cluster = dispy.JobCluster(function,
                                            [dispy.NodeAllocate(node.ip_addr, setup_args=() if node_setup else (modules,))
                                             for node in self.nodes],
                                            list(deps),
                                            callback,
                                            self.cluster_status,
                                            pulse_interval=10,
                                            ping_interval =10,
                                            # Note, exceptions in setup seems to be swallowed up and
                                            # never shown.
                                            setup          = node_setup or setup,
                                            cleanup        = node_cleanup or True,
                                            loglevel       = dispy.logger.CRITICAL,
                                            # if communication is lost to a node, his jobs will be
                                            # automatically rescheduled to another one.. (but
                                            # jobs must be reentrant! Because if rescheduled and the
                                            # disconnected node is really alive (temporal netsplit) the
                                            # job will run multiple times and maybe in parallel)
                                            reentrant      = True,
                                            host           = self.ip_address)

            self.http_server = dispy.httpd.DispyHTTPServer(self.cluster, poll_sec=2)

            # wait for all nodes to be created
            tqdm.write(" >> Waiting %d seconds to initialize all nodes... " % self.delay)
            time.sleep(self.delay)

        self.progress_bar = progress_bar

    def submit(self, *args):
        """
        function to submit jobs to dispy. If run_parallel == False, the jobs are executed
        :param args:
        :return:
        """
        if self.run_parallel:
            self.jobs.append(self.cluster.submit(*args))
        # if no-parallel was invoked, execute the procedure manually and synchronously
        elif not self.callback:
            self.function(*args)
        else:
            job = dispy.DispyJob(None, args, ())
            try:
                job.result = self.function(*args)
                if self.progress_bar is not None:
                    self.progress_bar.update()
            except Exception as e:
                job.exception = e
            self.callback(job)

    def submit_async(self, *args):
        # If run_parallel == True, works the same as the submit() method
        # If run_parallel == False, then the job will be run asynchronously in a job_runner
        # thread.
        # TODO: The submit() method must be deprecated and replaced with this method after we
        # make sure no code depends on submit() synchronous behavior. So every job submitted
        # will run asynchronously no matter run_parallel value and no different running semantics
        # will be used.
        if self.run_parallel:
            job = self.cluster.submit(*args)
            self.jobs.append(job)
        else:
            # dispy will a sign a job.id automatically
            job = dispy.DispyJob(None, args, ())
            self.job_runner_inbox.put((1, job, args))
        return job

    def _job_runner_thread(self):
        while True:
            (prio, job, args) = self.job_runner_inbox.get()

            if 'CLOSE' == job:
                return
            
            try:
                try:
                    job.result = self.function(*args)
                    if self.progress_bar is not None:
                        self.progress_bar.update()
                except:
                    job.exception = traceback.format_exc()
                self.callback(job)
            except:
                tqdm.write('WARNING: Exception running job callback: ' + traceback.format_exc())

    def wait(self):
        """
        wrapped function to wait for cluster execution
        :return: none
        """
        if self.run_parallel:
            tqdm.write(' -- Waiting for jobs to finish (no less than %d seconds)...' % self.delay)
            try:
                self.cluster.wait()
                # let the process trigger cluster_status before letting the calling proc close the progress bar
                time.sleep(self.delay)
            except KeyboardInterrupt:
                for job in self.jobs:
                    if job.status in (dispy.DispyJob.Running,
                                      dispy.DispyJob.Created):
                        self.cluster.cancel(job)
                self.cluster.shutdown()

    def close_cluster(self):
        if self.run_parallel and self.close:
            tqdm.write('')
            self.http_server.shutdown()
            self.cleanup()

    def cluster_status(self, status, node, job):
        """ Called by dispy on cluster events """
        # see https://dispy.org/examples.html#process-status-notifications
        J = dispy.DispyJob
        N = dispy.DispyNode
        
        # update the status in the http_server
        self.http_server.cluster_status(self.http_server._clusters[self.cluster.name], status, node, job)

        s = status
        if not job:
            # Node status change
            if N.Initialized == s:
                tqdm.write(' -- Node %s initialized with %i CPUs' % (node.name, node.avail_cpus))
                # test node to make sure everything works
                self.cluster.send_file('gnss_data.cfg', node)
                self.nodes.append(node)
                if self.on_nodes_changed:
                    self.on_nodes_changed(self.nodes)

            elif N.Closed == s:
                self.nodes.remove(node)
                if self.on_nodes_changed:
                    self.on_nodes_changed(self.nodes)
        else:
            # Job status change
            if J.Finished == s:
                if self.verbose:
                    tqdm.write(' -- Job %i finished successfully' % job.id)

            elif J.Abandoned == s:
                # If a node becomes offline and the cluster was created with reentrant=False,
                # their jobs will be reported as Abandoned. If reentrant=True, they will be
                # automatically redirected to other nodes by dispy.
                # always print abandoned jobs
                tqdm.write(' -- Job %04i (%s) was reported as abandoned at node %s -> resubmitting'
                           % (job.id, str(job.args), node.name))

            elif J.Created == s:
                if self.verbose:
                    tqdm.write(' -- Job %i has been created' % job.id)

            elif J.Terminated == s:
                tqdm.write(' -- Job %04i has been terminated with the following exception: ' % job.id)
                tqdm.write(str(job.exception))

            elif J.Cancelled == s:
                tqdm.write(' -- Job %04i has been cancelled with the following exception: ' % job.id)
                tqdm.write(str(job.exception))

            #
            if s in (J.Finished, J.Terminated) and self.progress_bar is not None:
                self.progress_bar.update()
                
            if s in (J.Finished, J.Abandoned, J.Terminated, J.Cancelled):
                try:
                    self.jobs.remove(job)
                except:
                    pass

    def cleanup(self):
        if not self.run_parallel:
            if self.node_cleanup:
                self.node_cleanup()
            self.job_runner_inbox.put((0, 'CLOSE', None))
        elif self.close:
            self.cluster.print_status()
            self.cluster.close()
            self.close = False

    def __del__(self):
        self.cleanup()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def __enter__(self):
        return self
