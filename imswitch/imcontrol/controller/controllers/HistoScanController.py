import json
import os

import imswitch
from imswitch.imcommon.model import initLogger, ostools
import numpy as np
import time
import tifffile
import threading
from datetime import datetime
import cv2
import numpy as np
from skimage.io import imsave
from scipy.ndimage import gaussian_filter
from collections import deque
from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QImage, QPixmap

import datetime 
from itertools import product
try:
    from ashlar import fileseries, thumbnail, reg
    IS_ASHLAR = True
except:
    print("Ashlar not installed")
    IS_ASHLAR = False
import numpy as np

from imswitch.imcommon.model import dirtools, initLogger, APIExport
from ..basecontrollers import ImConWidgetController
from imswitch.imcommon.framework import Signal, Thread, Worker, Mutex, Timer
import time

from ..basecontrollers import LiveUpdatedController


# import NanoImagingPack as nip

class HistoScanController(LiveUpdatedController):
    """Linked to HistoScanWidget."""

    sigImageReceived = Signal()
    sigUpdatePartialImage = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._logger = initLogger(self)
        # read default values from previously loaded config file
        offsetX = self._master.HistoScanManager.offsetX
        offsetY = self._master.HistoScanManager.offsetY
        self.tSettle = 0.05
    
        self.histoscanTask = None
        self.histoscanStack = np.ones((1,1,1))

        # read offset between cam and microscope from config file in µm
        self.offsetCamMicroscopeX = -2500 #  self._master.HistoScanManager.offsetCamMicroscopeX
        self.offsetCamMicroscopeY = 2500 #  self._master.HistoScanManager.offsetCamMicroscopeY
        
        # select detectors
        allDetectorNames = self._master.detectorsManager.getAllDeviceNames()
        self.microscopeDetector = self._master.detectorsManager[allDetectorNames[0]] # FIXME: This is hardcoded, need to be changed through the GUI
        if len(allDetectorNames)>1:
            self.webCamDetector = self._master.detectorsManager[allDetectorNames[1]] # FIXME: HARDCODED NEED TO BE CHANGED
            self.pixelSizeWebcam = self.webCamDetector.pixelSizeUm[-1]
        else:
            self.webCamDetector = None

        self.ishistoscanRunning = False
       
        # select lasers and add to gui
        allLaserNames = self._master.lasersManager.getAllDeviceNames()
        if "LED" in allLaserNames:
            self.led = self._master.lasersManager["LED"]
        else:
            self.led = None

        # grab ledmatrix if available
        if len(self._master.LEDMatrixsManager.getAllDeviceNames())>0:
            self.ledMatrix = self._master.LEDMatrixsManager[self._master.LEDMatrixsManager.getAllDeviceNames()[0]]
        else:
            self.ledMatrix = None 
        
        # connect signals
        self.sigImageReceived.connect(self.displayImage)
        self.sigUpdatePartialImage.connect(self.updatePartialImage)
        self._commChannel.sigUpdateMotorPosition.connect(self.updateAllPositionGUI)
        
        self.partialImageCoordinates = (0,0,0,0)
        self.partialHistoscanStack = np.ones((1,1,3))
        self.acceleration = 600000
        
        # camera-based scanning coordinates   (select from napari layer)      
        self.mCamScanCoordinates = None
        
        # select stage
        self.stages = self._master.positionersManager[self._master.positionersManager.getAllDeviceNames()[0]]
        
        
        # get flatfield manager
        if hasattr(self._master, "FlatfieldManager"):
            self.flatfieldManager = self._master.FlatfieldManager
        else: 
            self.flatfieldManager = None
        

        if not imswitch.IS_HEADLESS:
            '''
            Set up the GUI
            '''
            self._widget.setOffset(offsetX, offsetY)
            ## update optimal scan parameters for tile-based scan
            try:
                overlap = 0.75
                mFrameSize = (2000,3000) #self.microscopeDetector.getLatestFrame().shape
                bestScanSizeX = mFrameSize[1]*self.microscopeDetector.pixelSizeUm[-1]*overlap
                bestScanSizeY = mFrameSize[0]*self.microscopeDetector.pixelSizeUm[-1]*overlap     
                self._widget.setTilebasedScanParameters((bestScanSizeX, bestScanSizeY))
            except Exception as e:
                self._logger.error(e)
                
            self._widget.setAvailableIlluSources(allLaserNames)
            self._widget.startButton.clicked.connect(self.starthistoscan)
            self._widget.stopButton.clicked.connect(self.stophistoscan)
            self._widget.startButton2.clicked.connect(self.starthistoscanTilebased)
            self._widget.stopButton2.clicked.connect(self.stophistoscanTilebased)
            self._widget.sigSliderIlluValueChanged.connect(self.valueIlluChanged)        
            self._widget.sigSliderIlluValueChanged.connect(self.valueIlluChanged)
            self._widget.sigGoToPosition.connect(self.goToPosition)
            self._widget.sigCurrentOffset.connect(self.calibrateOffset)        
            self._widget.setDefaultSavePath(self._master.HistoScanManager.defaultConfigPath)
            
            # Image View
            self._widget.resetScanCoordinatesButton.clicked.connect(self.resetScanCoordinates)
            self._widget.getCameraScanCoordinatesButton.clicked.connect(self.getCameraScanCoordinates)
            self._widget.startButton3.clicked.connect(self.starthistoscanCamerabased)
            self._widget.stopButton3.clicked.connect(self.stophistoscanCamerabased)
            
            # on tab click, add the image to the napari viewer
            self._widget.tabWidget.currentChanged.connect(self.onTabChanged)
            
            # webcam-related parts 
            self.isWebcamRunning = False
            self._widget.imageLabel.doubleClicked.connect(self.onDoubleClickWebcam)
            self._widget.imageLabel.dragPosition.connect(self.onDragPositionWebcam)

            # illu settings
            self._widget.buttonTurnOnLED.clicked.connect(self.turnOnLED)
            self._widget.buttonTurnOffLED.clicked.connect(self.turnOffLED)
            self._widget.buttonTurnOnLEDArray.clicked.connect(self.turnOnLEDArray)
            self._widget.buttonTurnOffLEDArray.clicked.connect(self.turnOffLEDArray)
        
    def turnOnLED(self):
        if self.led is not None:
            self.led.setEnabled(1)
            self.led.setValue(255)
    
    def turnOffLED(self):
        if self.led is not None:
            self.led.setEnabled(0)

    def turnOnLEDArray(self):
        if self.ledMatrix is not None:    
            self.ledMatrix.setLEDIntensity(intensity=(255,255,255))
            self.ledMatrix.setAll((1,1,1))

    def turnOffLEDArray(self):
        if self.ledMatrix is not None:
            self.ledMatrix.setAll(0)

    def onTabChanged(self, index):
        '''
        Callback, when we click on the tab, we want to add the image to the napari viewer
        '''
        if index == 2:
            # add layer to napari
            self._widget.initShapeLayerNapari()
            self.microscopeDetector.startAcquisition()
            # run image scraper if not started already 
            if not self.isWebcamRunning:
                self.timer = QTimer(self)
                self.timer.timeout.connect(self.updateFrameWebcam)
                self.timer.start(100)
                self.isWebcamRunning = True
    
    def updateFrameWebcam(self):
        '''
        Update the webcam image in the dedicated widget periodically to get an overview
        '''
        frame = self.webCamDetector.getLatestFrame() # X,Y,C, uint8 numpy array
        if frame is None: 
            return
        if len(frame.shape)==2:
            frame = np.repeat(frame[:,:,np.newaxis], 3, axis=2)
        if frame is not None:
            height, width, channel = frame.shape
            bytesPerLine = 3 * width
            image = QImage(np.uint8(frame.copy()), width, height, bytesPerLine, QImage.Format_RGB888)
            pixmap = QPixmap.fromImage(image)
            self._widget.imageLabel.setOriginalPixmap(pixmap)
                        
    def resetScanCoordinates(self):
        '''
        reset the shape coordinates in napari
        '''
        # reset shape layer
        self._widget.resetShapeLayerNapari()
        # reset pre-scan image if available
        name = "Histo Prescan"
        self._widget.removeImageNapari(name)

    def onDoubleClickWebcam(self):
        '''
        Callback: when we double click on the webcam image, we want to move the stage to that position
        '''
        # if we double click on the webcam view, we want to move to that position on the plate
        mPositionClicked = self._widget.imageLabel.doubleClickPos.y(), self._widget.imageLabel.doubleClickPos.x()
        # convert to physical coordinates
        #mDimsWebcamFrame = self.webCamDetector.getLatestFrame().shape
        mDimsWebcamFrame = (self._widget.imageLabel.getCurrentImageSize().height(),self._widget.imageLabel.getCurrentImageSize().width())
        mRelativePosToMoveX = -(-mPositionClicked[0]+mDimsWebcamFrame[0]//2)*self.pixelSizeWebcam
        mRelativePosToMoveY = (-mPositionClicked[1]+mDimsWebcamFrame[1]//2)*self.pixelSizeWebcam
        currentPos = self.stages.getPosition()
        mAbsolutePosToMoveX = currentPos["X"]+mRelativePosToMoveX+self.offsetCamMicroscopeX
        mAbsolutePosToMoveY = currentPos["Y"]+mRelativePosToMoveY+self.offsetCamMicroscopeY
        self.goToPosition(mAbsolutePosToMoveX,mAbsolutePosToMoveY)
        
    def onDragPositionWebcam(self, start, end):
        '''
        Callback: when we drag the mouse on the webcam image, we want to move the stage to that position
        '''
        print(f"Dragged from {start} to {end}")
        if start is None or self._widget.imageLabel.currentRect is None:
            return
        # use the coordinates for the stage scan 
        # 1. retreive the coordinates on the canvas
        minPosX = np.min([start.x(), end.x()])
        maxPosX = np.max([start.x(), end.x()])
        minPosY = np.min([start.y(), end.y()])
        maxPosY = np.max([start.y(), end.y()])
        
        # 2. compute scan positions
        currentPos = self.stages.getPosition()
        mDimsWebcamFrame = (self._widget.imageLabel.getCurrentImageSize().height(),self._widget.imageLabel.getCurrentImageSize().width())
        minPosXReal = currentPos["X"]-(-minPosX+mDimsWebcamFrame[0]//2)*self.pixelSizeWebcam + self.offsetCamMicroscopeX
        maxPosXReal = currentPos["X"]-(-maxPosX+mDimsWebcamFrame[0]//2)*self.pixelSizeWebcam + self.offsetCamMicroscopeX
        minPosYReal = currentPos["Y"]+(mDimsWebcamFrame[1]//2-maxPosY)*self.pixelSizeWebcam + self.offsetCamMicroscopeY
        maxPosYReal = currentPos["Y"]+(mDimsWebcamFrame[1]//2-minPosY)*self.pixelSizeWebcam + self.offsetCamMicroscopeY

        # 3. get microscope camera parameters
        mFrame = self.microscopeDetector.getLatestFrame()
        pixelSizeMicroscopeDetector = self.microscopeDetector.pixelSizeUm[-1]
        NpixX, NpixY = mFrame.shape[1], mFrame.shape[0]
        
        # starting the snake scan
        # Calculate the size of the area each image covers
        img_width = NpixX * pixelSizeMicroscopeDetector
        img_height = NpixY * pixelSizeMicroscopeDetector
        
        # compute snake scan coordinates
        mOverlap = 0.75
        self.mCamScanCoordinates = self.generate_snake_scan_coordinates(minPosXReal, minPosYReal, maxPosXReal, maxPosYReal, img_width, img_height, mOverlap)
        nTilesX = int((maxPosXReal-minPosXReal)/(img_width*mOverlap))
        nTilesY = int((maxPosYReal-minPosYReal)/(img_height*mOverlap))
        self._widget.setCameraScanParameters(nTilesX, nTilesY, minPosX, maxPosX, minPosY, maxPosY)
                
        return self.mCamScanCoordinates

    
    def getCameraScanCoordinates(self):
        ''' retreive the coordinates of the shape layer in napari and compute the 
        min/max positions for X/Y to provide the snake-scan coordinates
        
        As of now: No error handling:
        A rect. shape in a shape Layer will provide e.g.:
        array([[ 299.5774541 , -157.22546457],
       [ 299.5774541 ,  160.6666534 ],
       [ 692.26771747,  160.6666534 ],
       [ 692.26771747, -157.22546457]])
        '''
        mCoordinates = self._widget.getCoordinatesShapeLayerNapari()[0]
        maxPosX = np.max(mCoordinates[:,0])
        minPosX = np.min(mCoordinates[:,0])
        maxPosY = np.max(mCoordinates[:,1])
        minPosY = np.min(mCoordinates[:,1])
        
        # get number of pixels in X/Y
        mFrame = self.microscopeDetector.getLatestFrame()
        NpixX, NpixY = mFrame.shape[1], mFrame.shape[0]
        
        # set frame as reference in napari 
        isRGB = mFrame.shape[-1]==3 # most likely True!
        name = "Histo Prescan"
        pixelsize = self.microscopeDetector.pixelSizeUm[-1]
        self._widget.setImageNapari(mFrame, colormap="gray", isRGB=isRGB, name=name, pixelsize=(pixelsize,pixelsize), translation=(0,0))

        # starting the snake scan
        # Calculate the size of the area each image covers
        img_width = NpixX * self.microscopeDetector.pixelSizeUm[-1]
        img_height = NpixY * self.microscopeDetector.pixelSizeUm[-1]
        
        # compute snake scan coordinates
        mOverlap = 0.75
        self.mCamScanCoordinates = self.generate_snake_scan_coordinates(minPosX, minPosY, maxPosX, maxPosY, img_width, img_height, mOverlap)
        nTilesX = int((maxPosX-minPosX)/(img_width*mOverlap))
        nTilesY = int((maxPosY-minPosY)/(img_height*mOverlap))
        self._widget.setCameraScanParameters(nTilesX, nTilesY, minPosX, maxPosX, minPosY, maxPosY)
        
        
    def starthistoscanCamerabased(self):
        '''
        start a camera scan
        '''      
        if self.mCamScanCoordinates is None:
            return  
        self.turnOffLEDArray()
        self.turnOnLED()
        # update GUI elements
        self._widget.startButton3.setEnabled(False)
        self._widget.stopButton3.setEnabled(True)
        self._widget.startButton3.setText("Running")
        self._widget.stopButton3.setText("Stop")
        self._widget.stopButton3.setStyleSheet("background-color: red")
        self._widget.startButton3.setStyleSheet("background-color: green")
        illuSource = self._widget.getIlluminationSource()
        initialPosition = self.stages.getPosition()        
        minPosX = np.min(self.mCamScanCoordinates, axis=0)[0]
        maxPosX = np.max(self.mCamScanCoordinates, axis=0)[0]
        minPosY = np.min(self.mCamScanCoordinates, axis=0)[1]
        maxPosY = np.max(self.mCamScanCoordinates, axis=0)[1]
        
        nTimes = 1
        tPeriod = 0
        
        self.performScanningRecording(minPosX=minPosX, minPosY=minPosY, maxPosX=maxPosX, maxPosY=maxPosY, positionList=self.mCamScanCoordinates, nTimes=nTimes, tPeriod=tPeriod, illuSource=illuSource)
                
    
    def stophistoscanCamerabased(self):
        '''
        stop a camera scan
        '''
        self.turnOnLEDArray()
        self.turnOffLED()

        self.ishistoscanRunning = False
        self._widget.startButton3.setEnabled(True)
        self._widget.stopButton3.setEnabled(False)
        self._widget.startButton3.setText("Start")
        self._widget.stopButton3.setText("Stopped")
        self._widget.stopButton3.setStyleSheet("background-color: green")
        self._widget.startButton3.setStyleSheet("background-color: red")
        self._logger.debug("histoscan scanning stopped.")
    
    def updateAllPositionGUI(self):
        allPositions = self.stages.position
        self._widget.updateBoxPosition(allPositions["X"], allPositions["Y"])

    def goToPosition(self, posX, posY):
        # {"task":"/motor_act",     "motor":     {         "steppers": [             { "stepperid": 1, "position": -1000, "speed": 30000, "isabs": 0, "isaccel":1, "isen":0, "accel":500000}     ]}}
        currentPosition = self.stages.getPosition()
        self.stages.move(value=(posX,posY), axis="XY", is_absolute=True, is_blocking=False, acceleration=(self.acceleration,self.acceleration))
        self._commChannel.sigUpdateMotorPosition.emit()
        newPosition = self.stages.getPosition()
        if currentPosition["X"]==newPosition["X"] and currentPosition["Y"]==newPosition["Y"]:
            self._logger.error("Could not move to position - check if coordinates are within the allowed range or if the stage is homed properly.")
            
    def displayImage(self):
        # a bit weird, but we cannot update outside the main thread
        name = self.histoScanStackName
        # subsample stack 
        isRGB = self.histoscanStack.shape[-1]==3
        self._widget.setImageNapari(self.histoscanStack, colormap="gray", isRGB=isRGB, name=name, pixelsize=(1,1), translation=(0,0))

    def updatePartialImage(self):
        # a bit weird, but we cannot update outside the main thread
        name = self.histoScanStackName
        # subsample stack 
        isRGB = self.histoscanStack.shape[-1]==3
        # coordinates: (x,y,w,h)
        self._widget.updatePartialImageNapari(im=np.uint16(self.partialHistoscanStack ), 
                                              coords=self.partialImageCoordinates,
                                              name=name)

    def valueIlluChanged(self):
        illuSource = self._widget.getIlluminationSource()
        illuValue = self._widget.illuminationSlider.value()
        self._master.lasersManager
        if not self._master.lasersManager[illuSource].enabled:
            self._master.lasersManager[illuSource].setEnabled(1)
        
        illuValue = illuValue/100*self._master.lasersManager[illuSource].valueRangeMax
        self._master.lasersManager[illuSource].setValue(illuValue)

    def calibrateOffset(self):
        # move to a known position and click in the 
        # 1. retreive the coordinates on the canvas
        clickedCoordinates = self._widget.ScanSelectViewWidget.clickedCoordinates
        # 2. measure the stage coordinates that relate to the clicked coordintes 
        self.stageinitialPosition = self.stages.getPosition()
        # true position:
        initX = self.stageinitialPosition["X"]
        initY = self.stageinitialPosition["Y"]
        initZ = self.stageinitialPosition["Z"]

        # compute the differences
        offsetX =  initX - clickedCoordinates[0]
        offsetY =  initY - clickedCoordinates[1]
        self._logger.debug("Offset coordinates in X/Y"+str(offsetX)+" / "+str(offsetY))

        # now we need to calculate the offset here
        self._master.HistoScanManager.writeConfig({"offsetX":offsetX, "offsetY":offsetY})
        self._widget.ScanSelectViewWidget.setOffset(offsetX,offsetY)
        
    def starthistoscan(self):
        minPosX = self._widget.getMinPositionX()
        maxPosX = self._widget.getMaxPositionX()
        minPosY = self._widget.getMinPositionY()
        maxPosY = self._widget.getMaxPositionY()   
        nTimes = self._widget.getNTimesScan()
        tPeriod = self._widget.getTPeriodScan()     
        self._widget.startButton.setEnabled(False)
        self._widget.stopButton.setEnabled(True)
        self._widget.startButton.setText("Running")
        self._widget.stopButton.setText("Stop") 
        self._widget.stopButton.setStyleSheet("background-color: red")
        self._widget.startButton.setStyleSheet("background-color: green")
        overlap = 0.75
        illuSource = self._widget.getIlluminationSource()

        self.performScanningRecording(minPosX, maxPosX, minPosY, maxPosY, overlap, nTimes, tPeriod, illuSource)

    def starthistoscanTilebased(self):
        numberTilesX, numberTilesY = self._widget.getNumberTiles()
        stepSizeX, stepSizeY = self._widget.getStepSize()
        nTimes = self._widget.getNTimesScan()
        tPeriod = self._widget.getTPeriodScan()
        self._widget.startButton2.setEnabled(False)
        self._widget.stopButton2.setEnabled(True)
        self._widget.startButton2.setText("Running")
        self._widget.stopButton2.setText("Stop")
        self._widget.stopButton2.setStyleSheet("background-color: red")
        self._widget.startButton2.setStyleSheet("background-color: green")
        illuSource = self._widget.getIlluminationSource()
        initialPosition = self.stages.getPosition()
        initPosX = initialPosition["X"]
        initPosY = initialPosition["Y"]
        self.startHistoScanTileBasedByParameters(numberTilesX, numberTilesY, stepSizeX, stepSizeY, nTimes, tPeriod, illuSource, initPosX, initPosY)

        
    @APIExport(runOnUIThread=True)
    def stopHistoScan(self):
        self.ishistoscanRunning = False
        if imswitch.IS_HEADLESS:
            self._widget.startButton.setEnabled(True)
            self._widget.stopButton.setEnabled(False)
            self._widget.startButton.setText("Start")
            self._widget.stopButton.setText("Stopped")
            self._widget.stopButton.setStyleSheet("background-color: green")
            self._widget.startButton.setStyleSheet("background-color: red")
            self._logger.debug("histoscan scanning stopped.")

    @APIExport(runOnUIThread=True)
    def startHistoScanTileBasedByParameters(self, numberTilesX:int=2, numberTilesY:int=2, stepSizeX:int=100, stepSizeY:int=100, nTimes:int=1, tPeriod:int=1, illuSource:str=None, initPosX:int=0, initPosY:int=0):
        def computePositionList(numberTilesX, numberTilesY, stepSizeX, stepSizeY, initPosX, initPosY):
            positionList = []
            for i in range(numberTilesX):
                if i % 2 == 0:  # X-Position ist gerade
                    rangeY = range(numberTilesY)
                else:  # X-Position ist ungerade
                    rangeY = range(numberTilesY - 1, -1, -1)
                for j in rangeY:
                    positionList.append((i*stepSizeX+initPosX-numberTilesX//2*stepSizeX, j*stepSizeY+initPosY-numberTilesY//2*stepSizeY))
            return positionList
        if illuSource is None:
            illuSource = self._master.lasersManager.getAllDeviceNames()[0]
        positionList = computePositionList(numberTilesX, numberTilesY, stepSizeX, stepSizeY, initPosX, initPosY)
        minPosX = np.min(positionList, axis=0)[0]
        maxPosX = np.max(positionList, axis=0)[0]
        minPosY = np.min(positionList, axis=0)[1]
        maxPosY = np.max(positionList, axis=0)[1]
        
        self.performScanningRecording(minPosX=minPosX, minPosY=minPosY, maxPosX=maxPosX, maxPosY=maxPosY, positionList=positionList, nTimes=nTimes, tPeriod=tPeriod, illuSource=illuSource)
        
    def stophistoscanTilebased(self):
        self.ishistoscanRunning = False
        self._widget.startButton2.setEnabled(True)
        self._widget.stopButton2.setEnabled(False)
        self._widget.startButton2.setText("Start")
        self._widget.stopButton2.setText("Stopped")
        self._widget.stopButton2.setStyleSheet("background-color: green")
        self._widget.startButton2.setStyleSheet("background-color: red")
        self._logger.debug("histoscan scanning stopped.")

    def performScanningRecording(self, minPosX=None, maxPosX=None, minPosY=None, maxPosY=None, overlap=None, nTimes=1, tPeriod=0, illuSource=None, positionList=None):
        if not self.ishistoscanRunning:
            self.ishistoscanRunning = True
            if self.histoscanTask is not None:
                self.histoscanTask.join()
                del self.histoscanTask
            self.histoscanTask = threading.Thread(target=self.histoscanThread, args=(minPosX, maxPosX, minPosY, maxPosY, overlap, nTimes, tPeriod, illuSource, positionList))
            self.histoscanTask.start()
        
    def generate_snake_scan_coordinates(self, posXmin, posYmin, posXmax, posYmax, img_width, img_height, overlap):
        # Calculate the number of steps in x and y directions
        steps_x = int((posXmax - posXmin) / (img_width*overlap))
        steps_y = int((posYmax - posYmin) / (img_height*overlap))
        
        coordinates = []

        # Loop over the positions in a snake pattern
        for y in range(steps_y):
            if y % 2 == 0:  # Even rows: left to right
                for x in range(steps_x):
                    coordinates.append((posXmin + x * img_width *overlap, posYmin + y * img_height *overlap))
            else:  # Odd rows: right to left
                for x in range(steps_x - 1, -1, -1):  # Starting from the last position, moving backwards
                    coordinates.append((posXmin + x * img_width *overlap, posYmin + y * img_height *overlap))
        
        return coordinates

        
    def histoscanThread(self, minPosX, maxPosX, minPosY, maxPosY, overlap=0.75, nTimes=1, tPeriod=0, illuSource=None, positionList=None):
        self._logger.debug("histoscan thread started.")
        
        initialPosition = self.stages.getPosition()
        initPosX = initialPosition["X"]
        initPosY = initialPosition["Y"]
        if not self.microscopeDetector._running: self.microscopeDetector.startAcquisition()
        
        # now start acquiring images and move the stage in Background
        mFrame = self.microscopeDetector.getLatestFrame()
        NpixX, NpixY = mFrame.shape[1], mFrame.shape[0]
        
        # starting the snake scan
        # Calculate the size of the area each image covers
        img_width = NpixX * self.microscopeDetector.pixelSizeUm[-1]
        img_height = NpixY * self.microscopeDetector.pixelSizeUm[-1]

        # precompute the position list in advance 
        if positionList is None:
            positionList = self.generate_snake_scan_coordinates(minPosX, minPosY, maxPosX, maxPosY, img_width, img_height, overlap)

        maxPosPixY = int((maxPosY-minPosY)/self.microscopeDetector.pixelSizeUm[-1])
        maxPosPixX = int((maxPosX-minPosX)/self.microscopeDetector.pixelSizeUm[-1])
        
        # are we RGB or monochrome?
        if len(mFrame.shape)==2:
            nChannels = 1
        else:
            nChannels = mFrame.shape[-1]
            
        # perform timelapse imaging
        for i in range(nTimes):
            tz = datetime.timezone.utc
            ft = "%Y-%m-%dT%H_%M_%S"
            t = datetime.datetime.now(tz=tz).strftime(ft)
            file_name = "test_"+t
            extension = ".ome.tif"
            folder = self._widget.getDefaulSavePath()

            t0 = time.time()
            
            # create a new image stitcher          
            if self.flatfieldManager is not None:
                flatfieldImage = self.flatfieldManager.getFlatfieldImage()
            else:
                flatfieldImage = None
            stitcher = ImageStitcher(self, min_coords=(0,0), max_coords=(maxPosPixX, maxPosPixY), folder=folder, nChannels=nChannels, file_name=file_name, extension=extension, flatfieldImage=flatfieldImage)
            
            # move to the first position
            self.stages.move(value=positionList[0], axis="XY", is_absolute=True, is_blocking=True, acceleration=(self.acceleration,self.acceleration))
            # move to all coordinates and take an image   
            if illuSource is not None: 
                self._master.lasersManager[illuSource].setEnabled(1)
                self._master.lasersManager[illuSource].setValue(255)
                time.sleep(.5)
            
            # we try an alternative way to move the stage and take images:
            # We move the stage in the background from min to max X and take
            # images in the foreground everytime the stage is in the region where there is a frame due
            if 0:
                self.stages.move(value=(minPosX, minPosY), axis="XY", is_absolute=True, is_blocking=True)
            
                # now we need to move to max X and take images in the foreground everytime the stage is in the region where there is a frame due
                self.stages.move(value=maxPosX, axis="X", is_absolute=True, is_blocking=False)
                stepSizeX = positionList[1][0]-positionList[0][0]
                lastStagePositionX = self.stages.getPosition()["X"]
                running=1
                while running:
                    currentPosX = self.stages.getPosition()["X"]
                    print(currentPosX)
                    if currentPosX-lastStagePositionX > stepSizeX:
                        print("Taking image")
                        mFrame = self.microscopeDetector.getLatestFrame()  
                        import tifffile as tif
                        tif.imsave("test.tif", mFrame, append=True)
                        
                        lastStagePositionX = currentPosX
                        

            for iPos in positionList:
                try:
                    if not self.ishistoscanRunning:
                        break
                    self.stages.move(value=iPos, axis="XY", is_absolute=True, is_blocking=True, acceleration=(self.acceleration,self.acceleration))
                    time.sleep(self.tSettle)
                    mFrame = self.microscopeDetector.getLatestFrame()  

                    def addImage(mFrame, positionList):
                        metadata = {'Pixels': {
                            'PhysicalSizeX': self.microscopeDetector.pixelSizeUm[-1],
                            'PhysicalSizeXUnit': 'µm',
                            'PhysicalSizeY': self.microscopeDetector.pixelSizeUm[-1],
                            'PhysicalSizeYUnit': 'µm'},

                            'Plane': {
                                'PositionX': positionList[0],
                                'PositionY': positionList[1]
                        }, }
                        self._commChannel.sigUpdateMotorPosition.emit()
                        posY_pix_value = (float(positionList[1])-minPosY)/self.microscopeDetector.pixelSizeUm[-1]
                        posX_pix_value = (float(positionList[0])-minPosX)/self.microscopeDetector.pixelSizeUm[-1]
                        iPosPix = (posX_pix_value, posY_pix_value)
                        #stitcher._place_on_canvas(np.copy(mFrame), np.copy(iPosPix))
                        stitcher.add_image(np.copy(mFrame), np.copy(iPosPix), metadata.copy())
                    threading.Thread(target=addImage, args=(mFrame,iPos)).start()

                except Exception as e:
                    self._logger.error(e)
            if illuSource is not None:
                self._master.lasersManager[illuSource].setEnabled(0)

            # wait until we go for the next timelapse
            while 1:
                if time.time()-t0 > tPeriod:
                    break
                if not self.ishistoscanRunning:
                    return
                time.sleep(1)
        # return to initial position
        self.stages.move(value=(initPosX,initPosY), axis="XY", is_absolute=True, is_blocking=False, acceleration=(self.acceleration,self.acceleration))
        self._commChannel.sigUpdateMotorPosition.emit()
        
        # move back to initial position
        self.stophistoscan()

        # get stitched result
        def getStitchedResult():
            largeImage = stitcher.get_stitched_image()
            tifffile.imsave("stitchedImage.tif", largeImage, append=False) 
            # display result 
            self.setImageForDisplay(largeImage, "histoscanStitch")
        threading.Thread(target=getStitchedResult).start()

    def valueIlluChanged(self):
        illuSource = self._widget.getIlluminationSource()
        illuValue = self._widget.illuminationSlider.value()
        self._master.lasersManager
        if not self._master.lasersManager[illuSource].enabled:
            self._master.lasersManager[illuSource].setEnabled(1)
        
        illuValue = illuValue/100*self._master.lasersManager[illuSource].valueRangeMax
        self._master.lasersManager[illuSource].setValue(illuValue)

    def setImageForDisplay(self, image, name):
        self.histoScanStackName = name
        self.histoscanStack = image
        self.sigImageReceived.emit()
        
    def setPartialImageForDisplay(self, image, coordinates, name):
        # coordinates: (x,y,w,h)
        self.partialImageCoordinates = coordinates
        self.partialHistoscanStack = image
        self.histoscanStack = image
        self.sigUpdatePartialImage.emit()

    def stophistoscan(self):
        # update GUI elements
        self.ishistoscanRunning = False
        self._widget.startButton.setEnabled(True)
        self._widget.stopButton.setEnabled(False)
        self._widget.startButton.setText("Start")
        self._widget.stopButton.setText("Stopped")
        self._widget.stopButton.setStyleSheet("background-color: green")
        self._widget.startButton.setStyleSheet("background-color: red")
        self._logger.debug("histoscan scanning stopped.")
        
        # other tabs
        self.stophistoscanTilebased()
        self.stophistoscanCamerabased()
        
    
        




class ImageStitcher:

    def __init__(self, parent, min_coords, max_coords,  folder, file_name, extension, subsample_factor=.25, nChannels = 3, flatfieldImage=None):
        # Initial min and max coordinates 
        self._parent = parent
        self.subsample_factor = subsample_factor
        self.min_coords = np.int32(np.array(min_coords)*self.subsample_factor)
        self.max_coords = np.int32(np.array(max_coords)*self.subsample_factor)
        
        # determine write location
        self.file_name = file_name
        self.file_path = os.sep.join([folder, file_name + extension])
        
        # Create a blank canvas for the final image and a canvas to track blending weights
        self.nY = self.max_coords[1] - self.min_coords[1]
        self.nX = self.max_coords[0] - self.min_coords[0]
        self.stitched_image = np.zeros((self.nY, self.nX, nChannels), dtype=np.float32)
        self.stitched_image_shape= self.stitched_image.shape
        
        # get the background image
        if flatfieldImage is not None:
            self.flatfieldImage = cv2.resize(np.copy(flatfieldImage), None, fx=self.subsample_factor, fy=self.subsample_factor, interpolation=cv2.INTER_NEAREST)  
        else:
            self.flatfieldImage = np.ones((self.nY, self.nX, nChannels), dtype=np.float32)
        
        # Queue to hold incoming images
        self.queue = deque()

        # Thread lock for thread safety
        self.lock = threading.Lock()

        # Start a background thread for processing the queue
        self.processing_thread = threading.Thread(target=self._process_queue)
        self.isRunning = True
        self.processing_thread.start()

    def add_image(self, img, coords, metadata):
        with self.lock:
            self.queue.append((img, coords, metadata))

    def _process_queue(self):
        with tifffile.TiffWriter(self.file_path, bigtiff=True, append=True) as tif:
            while self.isRunning:
                with self.lock:
                    if not self.queue:
                        time.sleep(.1) # unload CPU
                        continue
                    img, coords, metadata = self.queue.popleft()
                    self._place_on_canvas(img, coords)

                    # write image to disk
                    tif.write(data=img, metadata=metadata)
            

    def _place_on_canvas(self, img, coords):
        # these are pixelcoordinates (e.g. center of the imageslice)
        offset_x = int(coords[0]*self.subsample_factor - self.min_coords[0])
        offset_y = int(self.max_coords[1]-coords[1]*self.subsample_factor)
        #self._parent._logger.debug("Coordinates: "+str((offset_x,offset_y)))

        # Calculate a feathering mask based on image intensity
        img = cv2.resize(np.copy(img), None, fx=self.subsample_factor, fy=self.subsample_factor, interpolation=cv2.INTER_NEAREST) 
        img = np.flip(np.flip(img,1),0)
        scalingFactor = .5
        try: img = np.float32(img)/np.float32(self.flatfieldImage) # we scale flatfieldImage 0...1
        except: pass #self._parent._logger.error("Could not divide by flatfieldImage")
        if len(img.shape)==3:
           img = np.uint8(img) # napari only accepts uint8 for RGB
        try: 
            stitchDim = self.stitched_image[offset_y-img.shape[0]:offset_y, offset_x:offset_x+img.shape[1]].shape
            stitchImage = img[0:stitchDim[0], 0:stitchDim[1]]
            if len(stitchImage.shape)==2:
                stitchImage = np.expand_dims(stitchImage, axis=-1)
            self.stitched_image[offset_y-img.shape[0]:offset_y, offset_x:offset_x+img.shape[1]] = stitchImage
        
            # try to display in napari if ready
            #self._parent.setPartialImageForDisplay(stitchImage, (offset_x, offset_y, img.shape[1], img.shape[0]), "Stitched Image")
        except Exception as e:
            self.__logger.error(e)

    def get_stitched_image(self):
        with self.lock:
            # Normalize by the weight image to get the final result
            stitched = self.stitched_image.copy()
            if len(stitched.shape)>2:
                stitched = stitched/np.max(stitched)
                stitched = np.uint8(stitched*255)
            self.isRunning = False
            return stitched 

    def save_stitched_image(self, filename):
        stitched = self.get_stitched_image()
        imsave(filename, stitched)
    
    

class MovementController:
    def __init__(self, stages):
        self.stages = stages
        self.target_reached = False
        self.target_position = None
        self.axis = None


    def move_to_position(self, minPos, axis, speed, is_absolute):
        self.target_position = minPos
        self.speed = speed
        self.is_absolute = is_absolute
        self.axis = axis
        thread = threading.Thread(target=self._move)
        thread.start()

    def _move(self):
        self.target_reached = False
        self.stages.move(value=self.target_position, axis=self.axis, speed=self.speed, is_absolute=self.is_absolute, is_blocking=True)
        self.target_reached = True

    def is_target_reached(self):
        return self.target_reached



