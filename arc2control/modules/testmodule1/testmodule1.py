import math
import time
import numpy as np
import pyqtgraph as pg
import h5py
from enum import Enum
from pyarc2 import ReadAt, ReadAfter, DataMode
from arc2control.modules.base import BaseModule, BaseOperation
from . import MOD_NAME, MOD_TAG, MOD_DESCRIPTION
from .ret_display_widget import RETDataDisplayWidget
from arc2control import signals
from arc2control.h5utils import OpType
from arc2control.widgets.duration_widget import DurationWidget
from decimal import Decimal
from PyQt6 import QtCore, QtWidgets, QtGui
from PyQt6.QtCore import pyqtSignal


_RET_DTYPE = [('channel','<u8'),('bias', '<f8'), ('current', '<f4'), ('resistance', '<f4'), ('tstamp_us', '<f8')]

_MAX_REFRESHES_PER_SECOND = 100
_MIN_INTERVAL_USEC = 1000000//_MAX_REFRESHES_PER_SECOND # note! integer division


class RetentionOperation(BaseOperation):
    
    #Define useful signals
    newValue = pyqtSignal(np.ndarray)
    operationFinished = QtCore.pyqtSignal()

    #Constructor
    def __init__(self, params, parent):
        super().__init__(parent=parent)
        
        #params are passed via GUI, like pulsewidth and vread
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
        
        delta=0
        
        """
        mask: list of channels selected on GUI crosspoints (not repeated)
        biasMask: list of channels to be biased
        biasedMask: list of tuplets [(channel, bias)]
        """
        mask = []
        highs=[]
        biasMask=[13]
        biasedMask=[]
        
        
        
        #Set value to start with
        v_start = -5
        
        vBiasLow = -1
        vBiasHigh = -5
        
        
        #construction of mask
        for cell in self.cells:
            (w, b) = (cell.w, cell.b)
            (high, low) = self.mapper.wb2ch[w][b]
            
            if high not in mask:
                mask.append(high)
                highs.append(high)
               
            if low not in mask:
                mask.append(low)   
        mask.sort()
        highs.sort()
        
        
        #construction of biased mask
        for channel in mask:
            if channel in biasMask:
                biasedMask.append((channel,v_start))
            else:
                biasedMask.append((channel, 0))
                
        
        
        #set channels to the desired voltage
        self.arc.connect_to_gnd([])
        self.arc.open_channels(list(range(64)))
        self.arc.execute()
        
        self.arc.config_channels(biasedMask, base=None)
        self.arc.execute()
        self.arc.wait()
        
        voltages=self.arc.vread_channels(mask, False)
        print("these are the voltages")
        print(voltages)
       
        
        #start the timer, in order to compensate and keep track of operation time
        start =time.time()
        start_prog =time.time()
        sampleTime = time.time()
        resistance=[0,0,0,0,0,0,0,0,0,0,0,0]
        
        
        #perform open current measurement on mask channels
        currentSample= self.arc.read_slice_open(mask, False)
        for idx, channel in enumerate(mask):
            resistance[idx]=(voltages[0]-voltages[idx])/currentSample[channel]
        
        
        
        print(resistance)
        
        
        #save reading results in cellData[idx]
        for idx, channel in enumerate(mask):   
            self.cellData[idx] = np.empty(shape=(iterations+1, ), dtype=_RET_DTYPE)
            self.cellDataLookBack[idx] = 0
            self.cellData[idx][0] = (mask[idx], voltages[idx], currentSample[channel], resistance[idx], sampleTime-start_prog)
            ispulse = False
            
        #------------   HERE STARTS THE ACTUAL CYCLE    ----------------------------------
        
        #cycle between high and low bias
        for step in range(1, iterations+1):
            #alternate between high and low bias
            ispulse=not ispulse
                       
            if not ispulse:                
                #update of biased mask
                biasedMask=[]
                for channel in mask:
                    if channel in biasMask:
                        biasedMask.append((channel,vBiasLow))
                    else:
                        biasedMask.append((channel, -0.0))
                
                time.sleep(lowtime)
               
                
            else:
                #update of biased mask
                biasedMask=[]
                for channel in mask:
                    if channel in biasMask:
                        biasedMask.append((channel,vBiasHigh))
                    else:
                        biasedMask.append((channel, 0.0))
                
                time.sleep(hightime)
               
            #start timer for next cycle  
            start =time.time()
            sampleTime = time.time() 
            
            #set channels
           
            self.arc.connect_to_gnd([])
            self.arc.open_channels(list(range(64)))
            self.arc.execute()
            
            #DEBUG CHECK
            print(biasedMask)
                
            self.arc.config_channels(biasedMask, base=None)
            self.arc.execute()
            
            voltages=[]
            voltages=self.arc.vread_channels(mask, False)
            
            print("these are the voltages")
            print(voltages)
            
             
            #INSERTING SLEEP TO CHECK THE WAVEFORM ON OSCILLOSCOPE, unselected channels are still floating
            time.sleep(5)
            
            currentSample = self.arc.read_slice_open(mask, False)
            
            #INSERTING SLEEP TO CHECK THE WAVEFORM ON OSCILLOSCOPE, unselected channels are now forced at 0V
            time.sleep(3)
            
            
          
            
            for idx, channel in enumerate(mask):
                resistance[idx]=(2*voltages[0]-2*voltages[idx])/currentSample[channel]
           
            print(resistance)
                 
            
            for idx, channel in enumerate(mask):
                
                self.cellData[idx][step] = (mask[idx],voltages[idx], currentSample[channel],resistance[idx], sampleTime-start_prog)
                
                        
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
        self.customDatastore = h5py.File('C:/Users/mcfab/Desktop/customdata.h5','w')
        self.customGroup =self.customDatastore.create_group('group1')
        

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
        
        for (channel, values) in data.items():
            #(w, b) = (cell.w, cell.b)
            directory="C:/Users/mcfab/Desktop/Measurements/"
            filename=str(values[0][0])
            with open(r""+directory+filename+".txt","w") as file:
                file.write(np.array2string(values))
            file.close()
                
                
            print(channel)
            print(values)
            """
            customDset=self.customGroup.create_dataset(name='TestModule1_'+str(channel), shape= np.shape(values), dtype= _RET_DTYPE, chunks=True)
            k=list(customDset.attrs.keys())
            z=list(customDset.attrs.values())
            print(k,z)
            customDset.attrs['vread'] = vread
            self.display(customDset)
           
            self.experimentFinished.emit(channel, channel, customDset.name)
            """
        self.customDatastore.close()

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
