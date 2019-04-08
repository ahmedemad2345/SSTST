
from queue import Queue
import flask
import threading
import predict_input as pred
import time
import tensorflow as tf
from object_detection.utils import label_map_util
from keras.models import load_model
import pickle
import os
from datetime import datetime
from datetime import timedelta


# variable definition
MODEL_NAME = 'hand_region_graph'
PATH_TO_FROZEN_GRAPH = 'object_detection/' + MODEL_NAME + '/frozen_inference_graph.pb'
PATH_TO_LABELS = os.path.join('object_detection/training', 'object-detection.pbtxt')
model_ = "output/Sn_sign_language_model.model"
label_bin = "output/Sn_sign_language_lb.pickle"


# initialize flask application
app = flask.Flask(__name__)

## Creating pools
obj_detection_res = Queue(5)                # object detector resources (graph, index)
predictor_res = Queue(5)                    # predictor resources (graph, model, lb)
obj_detLock = threading.Lock()
predLock = threading.Lock()

# worker threads
threads = []

# new tasks
tasks = Queue(10)
taskLock = threading.Lock()

# processed tasks output [ key: client_id+'$'+image_name, value: (res_time, pred, accuracy) ]
processed_data = {}
pro_data_lock = threading.Lock()


# load object detector and predictor resources
def loadGraphModels():
    for i in range(1, 6, 1):

        # load the detection graph and category index for object detector
        print("[INFO] loading neural network for hand detector {} ...".format(i))

        obj_detection_graph = tf.Graph()
        with obj_detection_graph.as_default():
            od_graph_def = tf.GraphDef()
            with tf.gfile.GFile(PATH_TO_FROZEN_GRAPH, 'rb') as fid:
                serialized_graph = fid.read()
                od_graph_def.ParseFromString(serialized_graph)
                tf.import_graph_def(od_graph_def, name='')

        obj_category_index = label_map_util.create_category_index_from_labelmap(PATH_TO_LABELS, use_display_name=True)  # label map
        obj_detLock.acquire()
        obj_detection_res.put( (obj_detection_graph, obj_category_index) )
        obj_detLock.release()

        # load the graph, model and lb for predictor
        print("[INFO] loading neural network for predictor {} ...".format(i))
        model = load_model(model_)
        lb = pickle.loads(open(label_bin, "rb").read())
        graph = tf.get_default_graph()

        predLock.acquire()
        predictor_res.put( (graph, model, lb) )
        predLock.release()


# create worker threads
def create_threads():
    for tid in range(1, 11, 1):  ## 1,11,1
        thread = pred.PredictThread(tid, 'thread_{}'.format(tid), tasks, taskLock, obj_detection_res, obj_detLock, predictor_res, predLock, processed_data, pro_data_lock)
        thread.start()
        threads.append(thread)
        print("[INFO] Thread {} created and added to the pool...".format(tid))


# thread to clear output dictionary (older outputs should be removed)
class OutputManager(threading.Thread):
    def __init__(self, threadID, name):
        threading.Thread.__init__(self)
        self.threadID  =threadID
        self.name = name

    def run(self):
        print("Starting " + self.name + " cleaner thread")
        manage_output()
        print("Exiting " + self.name + " cleaner thread")


# manage output dictionary (predictions older than 10min will be removed)
def manage_output():
    tocheck  = False
    sleepTime = 30 * 60             # default sleep time = 30 mins
    while True:                 # check at 30 min intervals
        if(tocheck):
            if(len(processed_data) > 0):
                print("[INFO] Running output cleaner thread ... ")
                pro_data_lock.acquire()
                keys = list(processed_data.keys())
                for key in keys:
                    processed_time = processed_data.get(key)[0]
                    current_time = datetime.now().strftime("%H:%M:%S")
                    time_int = datetime.strptime(current_time, "%H:%M:%S") - datetime.strptime(processed_time, "%H:%M:%S")
                    if time_int.days < 0:
                        time_int = timedelta(days=0, seconds=time_int.seconds, microseconds=time_int.microseconds)
                    
                    if time_int.total_seconds() > 10*60:        # remove predictions older than 10 mins
                        del processed_data[key]

                pro_data_lock.release()
                print("[INFO] Pausing output cleaner thread ... ")
                tocheck = False
            else:
                tocheck = False
                sleepTime = 60 * 60         # if no predictions sleep for 1 h
        else:
            time.sleep(sleepTime)         # sleep for 10 mins
            sleepTime = 30 * 60         # set again to default sleep time (30 mins)
            tocheck = True

# create cleaner thread
def create_cleaner():
    cl_thread = OutputManager(1, 'cleaner_01')
    print("[INFO] Cleaner thread created ...")
    cl_thread.start()


# route for prediction requests with images
@app.route('/predict', methods=["POST"])
def serve():
    if flask.request.method == "POST":
        if flask.request.files.get('image'):
            tasks.put( (flask.request.files["client_id"].read().decode('utf-8'), flask.request.files["image"].read(), flask.request.files["image_name"].read().decode('utf-8')) )
            
            return flask.jsonify(("received"))


# route for checking prediction results
@app.route('/check', methods=["POST"])
def ifDone():
    if flask.request.method == "POST":
        cli_id = flask.request.files["client_id"].read().decode('utf-8')
        img_name = flask.request.files["image_name"].read().decode('utf-8')

        pro_data_lock.acquire()
        ret = processed_data.get(cli_id + '$' + img_name, 'wait')
        if not ret == 'wait':
            del processed_data[cli_id + '$' + img_name]
        pro_data_lock.release()
        return flask.jsonify(ret)



if __name__ == "__main__":
    print("[INFO] Server started...\n[INFO] Wait until thread creation finish (10 threads in total) ...")
    loadGraphModels()
    create_threads()
    create_cleaner()
    print("[INFO] Loading complete...\n[INFO] Server is running...")
    app.run(host='0.0.0.0')