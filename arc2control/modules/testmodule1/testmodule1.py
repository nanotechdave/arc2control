import math
import time
import numpy as np
import pyqtgraph as pg
from enum import Enum
from pyarc2 import ReadAt, ReadAfter, DataMode
from arc2control.modules.base import BaseModule, BaseOperation
from . import MOD_NAME, MOD_TAG, MOD_DESCRIPTION
from .ret_display_widget import RETDataDisplayWidget
from arc2control import signals
from arc2control.h5utils import OpType
from arc2control.widgets.duration_widget import DurationWidget
from PyQt6 import QtCore, QtWidgets, QtGui
from PyQt6.QtCore import pyqtSignal


_RET_DTYPE = [('read_voltage', '<f4'), ('current', '<f4'), ('tstamp_s', '<u8'), ('tstamp_us', '<u8')]

_MAX_REFRESHES_PER_SECOND = 10
_MIN_INTERVAL_USEC = 1000000//_MAX_REFRESHES_PER_SECOND # note! integer division


class RetentionOperation(BaseOperation):
    newValue = pyqtSignal(np.ndarray)
    operationFinished = QtCore.pyqtSignal()

    def __init__(self, params, parent):
        super().__init__(parent=parent)
        self.params = params
        self.arcconf = self.arc2Config
        self._voltages = []
        self._currents = []
        self.cellData = {}

        (_, readevery, hightime, lowtime, _) = self.params
        # check if we need to ease up on refreshing the display
        self._immediateUpdates = readevery*1000000 > _MIN_INTERVAL_USEC
        # in that case, find out how many points should we
        # accumulate before a refresh
        self._accumulatorCutOff = (1.0/readevery)/_MAX_REFRESHES_PER_SECOND

        # if doing slow refreshes this holds a counter
        # that indicates how many iterations should be
        # pulled during each refresh
        self.cellDataLookBack = {}

    def run(self):
        (readfor, readevery, hightime, lowtime, vread) = self.params
        
        #computing number of iteration, sampletime averaged between high and low
        iterations = math.ceil(readfor/((hightime+lowtime)/2))
        vread_start = 0
        delta=0
        #try to set channels at a determined voltage
        #self.arc.config_channels([(16, 0), (17, 0), (18, 0), (13, 1)], 0)
        
        # allocate data tables and do initial read
        mask = np.array([16, 17, 18], dtype= np.uint64)
        
        #making sure channel 16, 17, 18 are connected to gnd
        self.arc.connect_to_gnd(mask)
        start =time.time()
        currentSample= self.arc.read_slice_masked(13, mask, vread_start)
        
        sampleTime = self.parseTimestamp(time.time())
        for idx, cell in enumerate(self.cells):
            self.cellData[cell] = np.empty(shape=(iterations+1, ), dtype=_RET_DTYPE)
            self.cellDataLookBack[cell] = 0
            self.cellData[cell][0] = (vread_start, currentSample[idx], \
                *sampleTime)
            ispulse = False
            
            

        for step in range(1, iterations+1):
            ispulse=not ispulse
            
            
            if not ispulse:
                vread_cycle=0.01
                delta = time.time() - start
                time.sleep(lowtime - delta)
                
                #   !!! --- !!!
                #I think sleep is too slow, 
                #I wanted to try delay attribute but 
                #the "Instrument does not have this attribute"
                
                #self.arc.add_delay((lowtime - delta)*(10**9))
                
            else:
                vread_cycle = vread
                delta = time.time() - start
                time.sleep(hightime - delta)
               
                #self.arc.add_delay((hightime - delta)*(10**9))
            start =time.time()
            currentSample = self.arc.read_slice_masked(13, mask, vread_cycle)
            
            sampleTime = self.parseTimestamp(time.time())         
            
            for idx, cell in enumerate(self.cells):
                
                #stamp = self.parseTimestamp(start, step*delta)
                self.cellData[cell][step] = (vread_cycle, currentSample[idx], *sampleTime)
                self.conditionalRefresh(cell, step, (vread_cycle, currentSample[idx], *sampleTime))
            
            
        self.operationFinished.emit()

    def parseTimestamp(self, tstamp, offset=0):
        (decimals, seconds) = math.modf(tstamp - offset)
        microseconds = int(decimals*1000000)
        seconds = int(seconds)

        return (seconds, microseconds)

    def readDevice(self, cell, vread):
        (w, b) = (cell.w, cell.b)
        (high, low) = self.mapper.wb2ch[w][b]

        # ensure we are not tied to hard GND
        self.arc.connect_to_gnd(np.array([], dtype=np.uint64))

        current = self.arc.read_one(low, high, vread)
        self.arc.finalise_operation(self.arcconf.idleMode)

        return current

    def conditionalRefresh(self, cell, step, result):

        (_, readevery, hightime, lowtime, _) = self.params
        (w, b) = (cell.w, cell.b)

        (vread, current, seconds, microseconds) = result

        if self._immediateUpdates:
            signals.valueUpdate.emit(w, b, current, vread, 0.0, vread, OpType.READ)
            signals.dataDisplayUpdate.emit(w, b)
        else:
            pointsPersSec = 1.0/readevery
            self.cellDataLookBack[cell] += 1
            accumulated = self.cellDataLookBack[cell]

            if accumulated > self._accumulatorCutOff:
                currents = self.cellData[cell]['current'][step-accumulated:step]
                voltages = self.cellData[cell]['read_voltage'][step-accumulated:step]
                pws = np.array([0.0]).repeat(accumulated)
                optypes = np.array([OpType.READ]).repeat(accumulated)
                signals.valueBulkUpdate.emit(w, b, currents, voltages, pws, \
                    voltages, optypes)
                signals.dataDisplayUpdate.emit(w, b)

                self.cellDataLookBack[cell] = 0


    def retentionData(self):
        return (self.params, self.cellData)


class Retention(BaseModule):

    def __init__(self, arc, arcconf, vread, store, cells, mapper, parent=None):

        BaseModule.__init__(self, arc, arcconf, vread, store, \
            MOD_NAME, MOD_TAG, cells, mapper, parent=parent)
        self._thread = None

        self.setupUi()

        signals.crossbarSelectionChanged.connect(self.crossbarSelectionChanged)

    def setupUi(self):
        layout = QtWidgets.QGridLayout(self)

        self.readEveryDurationWidget = DurationWidget()
        self.readEveryDurationWidget.setObjectName('readEveryDurationWidget')
        self.readEveryDurationWidget.setDurations([\
            ('ms', 1e-3), ('s', 1.0), ('min', 60.0)])
        self.readEveryDurationWidget.setDuration(100, 'ms')
        
        self.hightimeDurationWidget = DurationWidget()
        self.hightimeDurationWidget.setObjectName('hightimeDurationWidget')
        self.hightimeDurationWidget.setDurations([\
              ('us', 1e-6), ('ms', 1e-3), ('s', 1.0), ('min', 60.0)])
        self.hightimeDurationWidget.setDuration(100, 'ms')
        
        self.lowtimeDurationWidget = DurationWidget()
        self.lowtimeDurationWidget.setObjectName('lowtimeDurationWidget')
        self.lowtimeDurationWidget.setDurations([\
            ('us', 1e-6), ('ms', 1e-3), ('s', 1.0), ('min', 60.0)])
        self.lowtimeDurationWidget.setDuration(1, 's')

        self.readForDurationWidget = DurationWidget()
        self.readForDurationWidget.setObjectName('readForDurationWidget')
        self.readForDurationWidget.setDurations([\
            ('s', 1.0), ('min', 60.0), ('hr', 3600.0)])
        self.readForDurationWidget.setDuration(10, 's')

        self.readVoltageSpinBox = QtWidgets.QDoubleSpinBox()
        self.readVoltageSpinBox.setObjectName('readVoltageSpinBox')
        self.readVoltageSpinBox.setSuffix(' V')
        self.readVoltageSpinBox.setMinimum(-10.0)
        self.readVoltageSpinBox.setMaximum(10.0)
        self.readVoltageSpinBox.setSingleStep(0.1)
        self.readVoltageSpinBox.setDecimals(2)
        self.readVoltageSpinBox.setValue(self.readoutVoltage)
        self.readVoltageSpinBox.setEnabled(False)

        self.lockReadoutVoltageCheckBox = QtWidgets.QCheckBox('Use global read-out voltage?')
        self.lockReadoutVoltageCheckBox.setObjectName('lockReadoutVoltageCheckBox')
        self.lockReadoutVoltageCheckBox.setChecked(True)
        self.lockReadoutVoltageCheckBox.toggled.connect(\
            lambda checked: self.readVoltageSpinBox.setEnabled(not checked))

        layout.addWidget(QtWidgets.QLabel("Read every"), 0, 0)
        layout.addWidget(QtWidgets.QLabel("Read for"), 1, 0)
        layout.addWidget(QtWidgets.QLabel("Read at"), 2, 0)
        
        #start of mycode
        layout.addWidget(QtWidgets.QLabel("hightime"), 3, 0)
        layout.addWidget(QtWidgets.QLabel("lowtime"), 4, 0)        
        
        layout.addWidget(self.readEveryDurationWidget, 0, 1)
        layout.addWidget(self.readForDurationWidget, 1, 1)
        layout.addWidget(self.readVoltageSpinBox, 2, 1)
        
        #start of my code
        layout.addWidget(self.hightimeDurationWidget, 3, 1)
        layout.addWidget(self.lowtimeDurationWidget, 4, 1)
        
        
        layout.addWidget(self.lockReadoutVoltageCheckBox, 5, 0, 1, 2)
        layout.addItem(QtWidgets.QSpacerItem(20, 20, \
            QtWidgets.QSizePolicy.Policy.Fixed, \
            QtWidgets.QSizePolicy.Policy.Expanding), 5, 0)
        layout.addItem(QtWidgets.QSpacerItem(20, 20, \
            QtWidgets.QSizePolicy.Policy.Expanding, \
            QtWidgets.QSizePolicy.Policy.Fixed), 5, 2)
        layout.setColumnStretch(0, 0)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(2, 2)
        layout.setContentsMargins(0, 0, 0, 0)

        buttonLayout = QtWidgets.QHBoxLayout()
        self.applyButton = QtWidgets.QPushButton("Apply to Selected")
        self.applyButton.setEnabled((len(self.cells) > 0) and \
            (self.arc is not None))
        self.applyButton.clicked.connect(self.applyButtonClicked)
        buttonLayout.addItem(QtWidgets.QSpacerItem(20, 20, \
            QtWidgets.QSizePolicy.Policy.Expanding))
        buttonLayout.addWidget(self.applyButton)
        buttonLayout.addItem(QtWidgets.QSpacerItem(20, 20, \
            QtWidgets.QSizePolicy.Policy.Expanding))

        layout.addLayout(buttonLayout, 6, 0, 1, 3)

        self.setLayout(layout)

    @property
    def description(self):
        return MOD_DESCRIPTION

    def loadFromJson(self, fname):
        # we override the default loading function to do extra validation
        super().loadFromJson(fname)
        self.readVoltageSpinBox.setEnabled(\
            not self.lockReadoutVoltageCheckBox.isChecked())
        self.applyButton.setEnabled((len(self.cells) > 0) and \
            (self.arc is not None))

    def crossbarSelectionChanged(self, cells):
        self.applyButton.setEnabled((len(self.cells) > 0) and \
            (self.arc is not None))

    def applyButtonClicked(self):
        self._thread = RetentionOperation(self.__retentionParams(), self)
        self._thread.operationFinished.connect(self.__threadFinished)
        self._thread.start()

    def __threadFinished(self):
        self._thread.wait()
        self._thread.setParent(None)
        ((readfor, readevery, hightime, lowtime, vread), data) = self._thread.retentionData()
        self._thread = None

        for (cell, values) in data.items():
            (w, b) = (cell.w, cell.b)
            dset = self.datastore.make_wb_table(w, b, MOD_TAG, \
                values.shape, _RET_DTYPE)
            dset.attrs['vread'] = vread
            for (field, _) in _RET_DTYPE:
                dset[:, field] = values[field]
            self.experimentFinished.emit(w, b, dset.name)

    def __retentionParams(self):
        readfor = self.readForDurationWidget.getDuration()
        readevery = self.readEveryDurationWidget.getDuration()
        hightime = self.hightimeDurationWidget.getDuration()
        lowtime = self.lowtimeDurationWidget.getDuration()
        if self.lockReadoutVoltageCheckBox.isChecked():
            vread = self.readoutVoltage
        else:
            vread = self.readVoltageSpinBox.value()

        return (readfor, readevery, hightime, lowtime, vread)

    @staticmethod
    def display(dataset):
        return RETDataDisplayWidget(dataset)