# -*- coding: utf-8 -*-
"""
Created on Mon Oct 10 16:45:25 2022

@author: Arc2
"""

MOD_NAME = 'TestModule1'
MOD_TAG = 'TM1'
MOD_DESCRIPTION = 'Read devices over time'
BUILT_IN = False

from .testmodule1 import Retention
ENTRY_POINT = Retention
