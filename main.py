from dronekit import connect, Command, LocationGlobal,VehicleMode
from pymavlink import mavutil
import time, sys, argparse, math
import threading
import os
import cv2
import numpy as np
import importlib.util

"""
Not needed for now

from statistics import mean
import cv2
import numpy as np
import picamera
"""

#################################### Connection String and vehicle connection
"""
# For Jmavsim
connection_string       = '127.0.0.1:14540'

print ("Connecting")
vehicle = connect(connection_string, wait_ready=True)
"""
connection_string       = "/dev/ttyAMA0"

print ("Connecting")
vehicle = connect(connection_string,baud=57600, wait_ready=True)


##################################### Objects
class LandSiteObject:
    def __init__(self,xCoordinate,yCoordinate,flagName,letter):
        self.xCoordinate = xCoordinate
        self.yCoordinate = yCoordinate
        self.flagName = flagName
        self.letter = letter

class FlagObject:
    def __init__(self,landSiteLetter,landOrder,flagName):
        self.landSiteLetter = landSiteLetter
        self.landOrder = landOrder
        self.flagName = flagName

class VideoStream:
    """Camera object that controls video streaming from the Picamera"""
    def __init__(self,resolution=(640,480),framerate=30):
        # Initialize the PiCamera and the camera image stream
        self.stream = cv2.VideoCapture(0)
        ret = self.stream.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        ret = self.stream.set(3,resolution[0])
        ret = self.stream.set(4,resolution[1])
            
        # Read first frame from the stream
        (self.grabbed, self.frame) = self.stream.read()

	# Variable to control when the camera is stopped
        self.stopped = False

    def start(self):
	# Start the thread that reads frames from the video stream
        threading.Thread(target=self.update,args=()).start()
        return self

    def update(self):
        # Keep looping indefinitely until the thread is stopped
        while True:
            # If the camera is stopped, stop the thread
            if self.stopped:
                # Close camera resources
                self.stream.release()
                return

            # Otherwise, grab the next frame from the stream
            (self.grabbed, self.frame) = self.stream.read()

    def read(self):
	# Return the most recent frame
        return self.frame

    def stop(self):
	# Indicate that the camera and thread should be stopped
        self.stopped = True

##################################### SETUP
stm = FlagObject('N',1,"stm")
metu = FlagObject('N',2,"metu")
ort = FlagObject('N',3,"ort")
landingfield = FlagObject('N',4,"landingfield")

A = LandSiteObject(3.25,3.25,"N",'A')
B = LandSiteObject(3.25,-3.25,"N",'B')
C = LandSiteObject(-3.25,-3.25,"N",'C')
D = LandSiteObject(-3.25,3.25,"N",'D')

land_sites = [A,B,C,D]
flag_objects = [stm,metu,ort,landingfield]


x,y,z =0,0,0
detected_flag_name = "empty for start"

number_of_detection = 0

number_of_being_sure = 3 #how many detections in a row to be sure

vision_altitude = 6 #meter

distance_tolerance = 0.10 # meter

threshold_for_tf = 0.6 # %60

time_to_takeoff_again = 3 #second

drive_with_meter = 0b111111111000
drive_with_speed = 0b111111000111

drive_type = drive_with_meter #initial

###################################### FUNCTIONS

def setpoint_buffer():
    global x,y,z,drive_type
    while True:
        msg=vehicle.message_factory.set_position_target_local_ned_encode(
            0,
            0,0,
            mavutil.mavlink.MAV_FRAME_LOCAL_NED,
            drive_type,
            x,y,z,
            x,y,z,
            0,0,0,
            0,0)
        vehicle.send_mavlink(msg)
        vehicle.flush()
        time.sleep(0.3)

# Define and parse input arguments
parser = argparse.ArgumentParser()
parser.add_argument('--modeldir', help='Folder the .tflite file is located in',
                    required=True)
parser.add_argument('--graph', help='Name of the .tflite file, if different than detect.tflite',
                    default='detect.tflite')
parser.add_argument('--labels', help='Name of the labelmap file, if different than labelmap.txt',
                    default='labelmap.txt')
parser.add_argument('--threshold', help='Minimum confidence threshold for displaying detected objects',
                    default=threshold_for_tf)
parser.add_argument('--resolution', help='Desired webcam resolution in WxH. If the webcam does not support the resolution entered, errors may occur.',
                    default='640x480')
parser.add_argument('--edgetpu', help='Use Coral Edge TPU Accelerator to speed up detection',
                    action='store_true')

args = parser.parse_args()

MODEL_NAME = args.modeldir
GRAPH_NAME = args.graph
LABELMAP_NAME = args.labels
min_conf_threshold = float(args.threshold)
resW, resH = args.resolution.split('x')
imW, imH = int(resW), int(resH)
use_TPU = args.edgetpu

# Import TensorFlow libraries
# If tensorflow is not installed, import interpreter from tflite_runtime, else import from regular tensorflow
# If using Coral Edge TPU, import the load_delegate library
pkg = importlib.util.find_spec('tensorflow')
if pkg is None:
    from tflite_runtime.interpreter import Interpreter
    if use_TPU:
        from tflite_runtime.interpreter import load_delegate
else:
    from tensorflow.lite.python.interpreter import Interpreter
    if use_TPU:
        from tensorflow.lite.python.interpreter import load_delegate

# If using Edge TPU, assign filename for Edge TPU model
if use_TPU:
    # If user has specified the name of the .tflite file, use that name, otherwise use default 'edgetpu.tflite'
    if (GRAPH_NAME == 'detect.tflite'):
        GRAPH_NAME = 'edgetpu.tflite'       

# Get path to current working directory
CWD_PATH = os.getcwd()

# Path to .tflite file, which contains the model that is used for object detection
PATH_TO_CKPT = os.path.join(CWD_PATH,MODEL_NAME,GRAPH_NAME)

# Path to label map file
PATH_TO_LABELS = os.path.join(CWD_PATH,MODEL_NAME,LABELMAP_NAME)

# Load the label map
with open(PATH_TO_LABELS, 'r') as f:
    labels = [line.strip() for line in f.readlines()]

# Have to do a weird fix for label map if using the COCO "starter model" from
# https://www.tensorflow.org/lite/models/object_detection/overview
# First label is '???', which has to be removed.
if labels[0] == '???':
    del(labels[0])

# Load the Tensorflow Lite model.
# If using Edge TPU, use special load_delegate argument
if use_TPU:
    interpreter = Interpreter(model_path=PATH_TO_CKPT,
                              experimental_delegates=[load_delegate('libedgetpu.so.1.0')])
    print(PATH_TO_CKPT)
else:
    interpreter = Interpreter(model_path=PATH_TO_CKPT)

interpreter.allocate_tensors()

# Get model details
input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()
height = input_details[0]['shape'][1]
width = input_details[0]['shape'][2]

floating_model = (input_details[0]['dtype'] == np.float32)

input_mean = 127.5
input_std = 127.5

# Initialize frame rate calculation
frame_rate_calc = 1
freq = cv2.getTickFrequency()

# Initialize video stream
videostream = VideoStream(resolution=(imW,imH),framerate=30).start()
#time.sleep(1)

#for frame1 in camera.capture_continuous(rawCapture, format="bgr",use_video_port=True):

def tf_buffer():
    global videostream,freq,frame_rate_calc,input_std,input_mean,floating_model,width,height,output_details,input_details,interpreter,use_TPU,labels,PATH_TO_LABELS,PATH_TO_CKPT,CWD_PATH,GRAPH_NAME,pkg,MODEL_NAME,LABELMAP_NAME,min_conf_threshold,resW,resH,imW,imH,args,detected_flag_name,number_of_detection
    while True:
        # Start timer (for calculating frame rate)
        #t1 = cv2.getTickCount()

        # Grab frame from video stream
        frame1 = videostream.read()

        # Acquire frame and resize to expected shape [1xHxWx3]
        frame = frame1.copy()
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frame_resized = cv2.resize(frame_rgb, (width, height))
        input_data = np.expand_dims(frame_resized, axis=0)

        # Normalize pixel values if using a floating model (i.e. if model is non-quantized)
        if floating_model:
            input_data = (np.float32(input_data) - input_mean) / input_std

        # Perform the actual detection by running the model with the image as input
        interpreter.set_tensor(input_details[0]['index'],input_data)
        interpreter.invoke()

        # Retrieve detection results
        boxes = interpreter.get_tensor(output_details[0]['index'])[0] # Bounding box coordinates of detected objects
        classes = interpreter.get_tensor(output_details[1]['index'])[0] # Class index of detected objects
        scores = interpreter.get_tensor(output_details[2]['index'])[0] # Confidence of detected objects
        #num = interpreter.get_tensor(output_details[3]['index'])[0]  # Total number of detected objects (inaccurate and not needed)

        # Loop over all detections and draw detection box if confidence is above minimum threshold 0.5 default
        for i in range(len(scores)):
            if ((scores[i] > min_conf_threshold) and (scores[i] <= 1.0)):

                # Get bounding box coordinates and draw box
                # Interpreter can return coordinates that are outside of image dimensions, need to force them to be within image using max() and min()
                ymin = int(max(1,(boxes[i][0] * imH)))
                xmin = int(max(1,(boxes[i][1] * imW)))
                ymax = int(min(imH,(boxes[i][2] * imH)))
                xmax = int(min(imW,(boxes[i][3] * imW)))
            
                cv2.rectangle(frame, (xmin,ymin), (xmax,ymax), (10, 255, 0), 2)

                # Draw label
                object_name = labels[int(classes[i])] # Look up object name from "labels" array using class index
                """
                label = '%s: %d%%' % (object_name, int(scores[i]*100)) # Example: 'person: 72%'
                labelSize, baseLine = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2) # Get font size
                label_ymin = max(ymin, labelSize[1] + 10) # Make sure not to draw label too close to top of window
                cv2.rectangle(frame, (xmin, label_ymin-labelSize[1]-10), (xmin+labelSize[0], label_ymin+baseLine-10), (255, 255, 255), cv2.FILLED) # Draw white box to put label text in
                cv2.putText(frame, label, (xmin, label_ymin-7), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2) # Draw label text
                """
                detected_flag_name = object_name
                number_of_detection += 1
        # Draw framerate in corner of frame
        #cv2.putText(frame,'FPS: {0:.2f}'.format(frame_rate_calc),(30,50),cv2.FONT_HERSHEY_SIMPLEX,1,(255,255,0),2,cv2.LINE_AA)

        # All the results have been drawn on the frame, so it's time to display it.
        #cv2.imshow('Object detector', frame)

        # Calculate framerate
        #t2 = cv2.getTickCount()
        #time1 = (t2-t1)/freq
        #frame_rate_calc= 1/time1

        # Press 'q' to quit
        if cv2.waitKey(1) == ord('q'):
            break    

def land():
    print("Land !")
    while vehicle.mode != "LAND":
        vehicle._master.set_mode_px4('LAND',None,None)
        print ("Trying land")
        time.sleep(0.3)
    time.sleep(2)
    print ("Landed!")

def tryArming():
    while True:
        vehicle._master.mav.command_long_send(
        1, # autopilot system id
        1, # autopilot component id
        400, # command id, ARM/DISARM
        0, # confirmation
        1, # arm!
        0,0,0,0,0,0 # unused parameters for this command
        )
        print("Trying arming")
        time.sleep(0.3)
        if (vehicle.armed == True):
            break
    print ("Armed :",vehicle.armed)


def startThread():
    t = threading.Thread(target=setpoint_buffer)

    t.daemon = True

    t.start()

def startTfThread():
    tf = threading.Thread(target=tf_buffer)

    tf.daemon = True

    tf.start()

def startOffboardMode():
    while vehicle.mode != "OFFBOARD":
        vehicle._master.set_mode_px4('OFFBOARD',None,None)
        print ("Trying offboard")
        time.sleep(0.3)

def print_status():
    # Display basic vehicle state
    print (" Type: %s" % vehicle._vehicle_type)
    print (" Armed: %s" % vehicle.armed)
    print (" System status: %s" % vehicle.system_status.state)
    print (" GPS: %s" % vehicle.gps_0)
    print (" Alt: %s" % vehicle.location.global_relative_frame.alt)

def readyToTakeoff():
    print_status()
    tryArming()
    startOffboardMode()
    print_status()

    return True

def tryDisArming():
    #disarm
    while True:
        vehicle._master.mav.command_long_send(
        1, # autopilot system id
        1, # autopilot component id
        400, # command id, ARM/DISARM
        0, # confirmation
        0, # arm!
        0,0,0,0,0,0 # unused parameters for this command
        )
        print("Trying Disarming")
        time.sleep(0.3)
        if (vehicle.armed == False):
            break
    print ("Armed :",vehicle.armed)

    return True

def shutDownTheMotors():
    #stop motors afer landing
    print_status()
    tryDisArming()
    print_status()
    return True

def goToLocation(xTarget,yTarget,altTarget):
    global startYaw,x,y,z,distance_tolerance
    #goto desired locaiton
    
    x,y = bodyToNedFrame(xTarget,yTarget,startYaw)
    z = -altTarget

    while not atTheTargetYet(xTarget,yTarget,altTarget):
        time.sleep(0.1)

    return True

def defineTheFlag(landSiteLetter):
    #define the flag and write name of it to the landSite
    global detected_flag_name,number_of_detection,number_of_being_sure

    sure_counter = 0

    temp_flag_name = detected_flag_name
    temp_number = number_of_detection

    while True:
        if number_of_detection > temp_number:
            if detected_flag_name == temp_flag_name:
                sure_counter +=1
                temp_number = number_of_detection
                if sure_counter >= number_of_being_sure:
                    break
            else:
                sure_counter = 0
                temp_flag_name = detected_flag_name
                temp_number = number_of_detection
        time.sleep(0.1)



    #after being sure of detection:
    for land in land_sites:
        if(land.letter == landSiteLetter):
            land.flagName = temp_flag_name
    
    for flag in flag_objects:
        if(flag.flagName == temp_flag_name):
            flag.landSiteLetter = landSiteLetter
    
    return True 

def landWithVision(flagName):
    #land with vision to the flag

    #after landing
    shutDownTheMotors()
    return True

def rtl():
    goToLocation(0,0,-vision_altitude)
    landWithVision("turkishflag")
    #return to launch
    return True

def bodyToNedFrame(xBody,yBody,yawBody):
    xNed =  (xBody * math.cos(yawBody) ) - ( yBody * math.sin(yawBody) )
    yNed =  (xBody * math.sin(yawBody) ) + ( yBody * math.cos(yawBody) )
    return xNed,yNed

def atTheTargetYet(xTarget,yTarget,zTarget):
    global startYaw,distance_tolerance
    #goto desired locaiton
    
    xTarget,yTarget = bodyToNedFrame(xTarget,yTarget,startYaw)
    zTarget = -zTarget

    north = vehicle.location.local_frame.north
    east = vehicle.location.local_frame.east
    down = vehicle.location.local_frame.down
    if (abs(xTarget-north) < distance_tolerance):
        if(abs(yTarget-east) < distance_tolerance):
            if (abs(zTarget-down) < distance_tolerance):
                print("Target point reached")#add x y z
                return True
    print("Not at target yet")
    return False

##################################### START
home_position_set = False
#Create a message listener for home position fix
@vehicle.on_message('HOME_POSITION')
def listener(self, name, home_position):
    global home_position_set
    home_position_set = True


while not home_position_set:
    print ("Waiting for home position...")
    time.sleep(1)


startThread()

startTfThread()

readyToTakeoff()

startYaw = vehicle.attitude.yaw

goToLocation(0,0,vision_altitude) # take off

i = 1
###################################### LOOP

while True:
    print("Land Order = ",i)

    if (i == 5):#So we should RTL
        rtl()
        break

    for flag in flag_objects:
        if (flag.landOrder == i):
            if(flag.landSiteLetter == 'N'):
                for land in land_sites:
                    if (land.flagName == 'N'):
                        goToLocation(land.xCoordinate,land.yCoordinate,vision_altitude)
                        defineTheFlag(land.letter)
            else:
                for land in land_sites:
                    if (flag.landSiteLetter == land.letter):
                        goToLocation(land.xCoordinate,land.yCoordinate,vision_altitude)
                        landWithVision(flag.flagName)
                        time.sleep(time_to_takeoff_again)
                        readyToTakeoff()
                        
                        i += 1