import math
import time, datetime
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


_RET_DTYPE = [('read_voltage', '<f4'), ('current', '<f4'), ('tstamp_s', '<u8'), ('tstamp_us', '<u8')]


class RetentionOperation(BaseOperation):

    def __init__(self, params, parent):
        super().__init__(parent=parent)
        self.params = params
        self.arcconf = self.arc2Config
        self._voltages = []
        self._currents = []
        self.cellData = {}

    def run(self):
        (readfor, readevery, vread) = self.params

        iterations = math.ceil(readfor/readevery)

        # allocate data tables and do initial read
        for cell in self.cells:
            self.cellData[cell] = np.empty(shape=(iterations+1, ), dtype=_RET_DTYPE)
            self.cellData[cell][0] = self._readDevice(cell, vread)

        for step in range(1, iterations+1):
            time.sleep(readevery)
            for cell in self.cells:
                self.cellData[cell][step] = self._readDevice(cell, vread)

        self.finished.emit()

    def _readDevice(self, cell, vread):
        (w, b) = (cell.w, cell.b)
        (high, low) = self.mapper.wb2ch[w][b]

        current = self.arc.read_one(low, high, vread)
        self.arc.finalise_operation(self.arcconf.idleMode)
        (decimals, seconds) = math.modf(datetime.datetime.now().timestamp())
        microseconds = int(decimals*1e6)
        seconds = int(seconds)

        signals.valueUpdate.emit(w, b, current, vread, 0.0, vread, OpType.READ)
        signals.dataDisplayUpdate.emit(w, b)
        return (vread, current, seconds, microseconds)

    def retentionData(self):
        return self.cellData


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
        self.readEveryDurationWidget.setDuration(1, 's')

        self.readForDurationWidget = DurationWidget()
        self.readForDurationWidget.setObjectName('readForDurationWidget')
        self.readForDurationWidget.setDurations([\
            ('s', 1.0), ('min', 60.0), ('hr', 3600.0)])
        self.readForDurationWidget.setDuration(1, 'min')

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
        layout.addWidget(self.readEveryDurationWidget, 0, 1)
        layout.addWidget(self.readForDurationWidget, 1, 1)
        layout.addWidget(self.readVoltageSpinBox, 2, 1)
        layout.addWidget(self.lockReadoutVoltageCheckBox, 3, 0, 1, 2)
        layout.addItem(QtWidgets.QSpacerItem(20, 20, \
            QtWidgets.QSizePolicy.Policy.Fixed, \
            QtWidgets.QSizePolicy.Policy.Expanding), 4, 0)
        layout.addItem(QtWidgets.QSpacerItem(20, 20, \
            QtWidgets.QSizePolicy.Policy.Expanding, \
            QtWidgets.QSizePolicy.Policy.Fixed), 4, 2)
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

        layout.addLayout(buttonLayout, 5, 0, 1, 3)

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
        self._thread = RetentionOperation(self._retentionParams(), self)
        self._thread.finished.connect(self._threadFinished)
        self._thread.start()

    def _threadFinished(self):
        self._thread.wait()
        self._thread.setParent(None)
        data = self._thread.retentionData()
        self._thread = None

        for (cell, values) in data.items():
            (w, b) = (cell.w, cell.b)
            dset = self.datastore.make_wb_table(w, b, MOD_TAG, \
                values.shape, _RET_DTYPE)
            for (field, _) in _RET_DTYPE:
                dset[:, field] = values[field]
            self.experimentFinished.emit(w, b, dset.name)

    def _retentionParams(self):
        readfor = self.readForDurationWidget.getDuration()
        readevery = self.readEveryDurationWidget.getDuration()
        if self.lockReadoutVoltageCheckBox.isChecked():
            vread = self.readoutVoltage
        else:
            vread = self.readVoltageSpinBox.value()

        return (readfor, readevery, vread)

    @staticmethod
    def display(dataset):
        return RETDataDisplayWidget(dataset)
