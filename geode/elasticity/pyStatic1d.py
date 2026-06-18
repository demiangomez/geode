"""
Project: Parallel.Archive
Date: 03/11/2024
Author: Demian D. Gomez
"""

import os
import shutil
import uuid

from geode import dbConnection
from geode import pyRunWithRetry


class Static1d(object):

    def __init__(self, cnn, max_expansion=5000, greens_function_file=None, min_depth=0, max_depth=10):

        self.id_run = str(uuid.uuid4())

        if type(cnn) is not dbConnection.Cnn:
            # need to connect using file, no connection provided
            cnn = dbConnection.Cnn(cnn)

        if greens_function_file and os.path.exists(greens_function_file):
            # copy the greens function file, if provided
            shutil.copyfile(greens_function_file, f'production/{self.id_run}/stat0.out')
        else:
            with open(f'production/{self.id_run}/stat0A.in', 'w') as f:
                f.write(f'1 {max_expansion}\n')
                f.write(f'{min_depth:5.2f} {max_depth:5.2f}\n')
                f.write(f'0\n')

            pyRunWithRetry.RunCommand('./stat0A', 5, cat_file=f'production/{self.id_run}/stat0A.in')


