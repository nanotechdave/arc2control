from PyQt6 import QtWidgets
from arc2control.modules.base import BaseModule
from . import MOD_NAME, MOD_TAG, MOD_DESCRIPTION

import time
from arc2control.modules.base import BaseOperation
from PyQt6.QtCore import pyqtSignal

import math
import numpy as np
import pyqtgraph as pg
from enum import Enum
from pyarc2 import ReadAt, ReadAfter, DataMode
from arc2control import signals
from arc2control.h5utils import OpType
from arc2control.widgets.duration_widget import DurationWidget

from PyQt6 import QtCore, QtWidgets, QtGui




class TestModuleOperation(BaseOperation):
    newValue = pyqtSignal(np.ndarray)
    operationFinished = QtCore.pyqtSignal()
    

    def __init__(self, parent):
        super().__init__(parent=parent)
        
        self.arcconf = self.arc2Config
        self._voltages = []
        self._currents = []
        self.cellData = {}
        # signal emitted when a new value is received
       

    def run(self):

        # pickup the first selected cell
        try:
            cell = list(self.cells)[0]
            vread = 0.5#self.readoutVoltage()
            (high, low) = self.mapper.wb2ch[cell.w][cell.b]
        except IndexError:
            # or exit if nothing is selected
            self.operationFinished.emit()
            return

        for _ in range(10):
            # do a current measurement
            ## try to pulse and read simultaneously
            #current = self.arc.pulseread_slice(16, 1, 1, 0)
            
            #read_slice allows to read the whole wordline simultaneously, given the channel 
            #and the read voltage -> read_slice(self, chan, vread)
            #THE CHANNEL IS THE BOARD CHANNEL, PAY ATTENTION TO MAPPING -> in this case channel 13 is Bitline 1
            current= self.arc.read_slice(13,1)
            # communicate the new value to the UI
            self.newValue.emit(current)
            print(current)
            self.arc.finalise_operation(self.arc2Config.idleMode)
            # artificial wait time
            time.sleep(1)

        self.operationFinished.emit()

class TestModule(BaseModule):

    def __init__(self, arc, arcconf, vread, store, cells, mapper, parent=None):
        # calling superclass constructor with all the arguments
        BaseModule.__init__(self, arc, arcconf, vread, store, MOD_NAME, \
            MOD_TAG, cells, mapper, parent=parent)

        # build the UI
        self.setupUi()

        # make the button do something
        self.runButton.clicked.connect(self.onRunClicked)

    def setupUi(self):
        self.setObjectName('TestModuleWidget')
        self.gridLayout = QtWidgets.QGridLayout(self)
        self.gridLayout.setContentsMargins(0, 0, 0, 0)
        spacer00 = QtWidgets.QSpacerItem(0, 20, \
             QtWidgets.QSizePolicy.Policy.Expanding, \
             QtWidgets.QSizePolicy.Policy.Minimum)
        self.gridLayout.addItem(spacer00, 2, 1, 1, 1)
        spacer01 = QtWidgets.QSpacerItem(20, 0, \
             QtWidgets.QSizePolicy.Policy.Minimum, \
             QtWidgets.QSizePolicy.Policy.Expanding)
        self.gridLayout.addItem(spacer01, 7, 0, 1, 1)

        self.horizontalLayout = QtWidgets.QHBoxLayout()
        spacer02 = QtWidgets.QSpacerItem(40, 20, \
             QtWidgets.QSizePolicy.Policy.Expanding, \
             QtWidgets.QSizePolicy.Policy.Minimum)
        self.horizontalLayout.addItem(spacer02)

        self.runButton = QtWidgets.QPushButton(self)
        self.runButton.setObjectName("runButton")
        self.horizontalLayout.addWidget(self.runButton)

        spacer03 = QtWidgets.QSpacerItem(40, 20, \
             QtWidgets.QSizePolicy.Policy.Expanding, \
             QtWidgets.QSizePolicy.Policy.Minimum)
        self.horizontalLayout.addItem(spacer03)

        self.gridLayout.addLayout(self.horizontalLayout, 8, 0, 1, 2)
        self.label = QtWidgets.QLabel(self)
        self.label.setText("Example label")
        self.gridLayout.addWidget(self.label, 0, 0, 1, 1)

    def onRunClicked(self):
    
        # callback called whenever a new value is produced
        def onNewValue(value):
            print('I = %g A' % value)
    
        # callback called when the thread exits
        def onFinished():
            self.operation.wait()
            self.operation = None
            print('Process finished')
    
        try:
            if self.operation is not None:
                # an operation is currently running; abort
                return
        except AttributeError:
            self.operation = None
    
        self.operation = TestModuleOperation(self)
        self.operation.newValue.connect(onNewValue)
        self.operation.operationFinished.connect(onFinished)
        self.operation.start()
