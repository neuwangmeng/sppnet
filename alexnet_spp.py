import numpy as np
import os
import sys
import tarfile
from six.moves.urllib.request import urlretrieve
from six.moves import cPickle as pickle
from PIL import Image
import math
import random
import re
import scipy.io
import PIL
from numpy import *
from pylab import *
from PIL import Image
from collections import defaultdict
import tensorflow as tf
import matplotlib.pyplot as plt

# Load data
DROPOUT = 0.5
LEARNING_RATE  = 0.001
VALIDATION_SIZE = 0
TRAINING_ITERATIONS = 50000
WEIGHT_DECAY = 0.0005

net_data = load("bvlc_alexnet.npy").item()

out_pool_size = [8, 6, 4]
hidden_dim = 0
for item in out_pool_size:
    hidden_dim = hidden_dim + item*item
    
data_folder = './102flowers'
labels = scipy.io.loadmat('imagelabels.mat')
setid = scipy.io.loadmat('setid.mat')

labels = labels['labels'][0] - 1
trnid = np.array(setid['tstid'][0]) - 1
tstid = np.array(setid['trnid'][0]) - 1
valid = np.array(setid['valid'][0]) - 1

num_classes = 102
data_dir = list()
for img in os.listdir(data_folder):
    data_dir.append(os.path.join(data_folder, img))

data_dir.sort()

# --------------------------------------------------------------------------
# Ultils
def print_activations(t):
    print(t.op.name, ' ', t.get_shape().as_list())

def dense_to_one_hot(labels_dense, num_classes):
    num_labels = labels_dense.shape[0]
    index_offset = np.arange(num_labels) * num_classes
    labels_one_hot = np.zeros((num_labels, num_classes))
    labels_one_hot.flat[index_offset + labels_dense.ravel()] = 1
    return labels_one_hot

def read_images_from_disk(input_queue):
    label = input_queue[1]
    file_contents = tf.read_file(input_queue[0])
    example = tf.image.decode_jpeg(file_contents, channels=3)
    # example = tf.cast(example, tf.float32 )
    return example, label

def weight_variable(shape, name):
    initial = tf.truncated_normal(shape, stddev=0.01, name=name)
    return tf.Variable(initial)

def bias_variable(shape, name):
    initial = tf.constant(0.1, shape=shape, name=name)
    return tf.Variable(initial)


def conv(input, kernel, biases, k_h, k_w, c_o, s_h, s_w, padding = "VALID", group = 1):
    '''From https://github.com/ethereon/caffe-tensorflow
    '''
    c_i = input.get_shape()[-1]
    assert c_i % group == 0
    assert c_o % group == 0
    convolve = lambda i, k: tf.nn.conv2d(i, k, [1, s_h, s_w, 1], padding=padding)

    if group == 1:
        conv = convolve(input, kernel)
    else:
        input_groups = tf.split(3, group, input)
        kernel_groups = tf.split(3, group, kernel)
        output_groups = [convolve(i, k) for i, k in zip(input_groups, kernel_groups)]
        conv = tf.concat(3, output_groups)
    return tf.reshape(tf.nn.bias_add(conv, biases), [-1] + conv.get_shape().as_list()[1:])

def conv2d(x, W, stride_h, stride_w, padding='SAME'):
    return tf.nn.conv2d(x, W, strides=[1, stride_h, stride_w, 1], padding=padding)

def max_pool_2x2(x):
    return tf.nn.max_pool(x, ksize=[1, 2, 2, 1], strides=[1, 2, 2, 1], padding='SAME')

def max_pool_3x3(x):
    return tf.nn.max_pool(x, ksize=[1, 3, 3, 1], strides=[1, 2, 2, 1], padding='SAME')

def max_pool_4x4(x):
    return tf.nn.max_pool(x, ksize=[1, 4, 4, 1], strides=[1, 4, 4, 1], padding='SAME')

# Spatial Pyramid Pooling block
# https://arxiv.org/abs/1406.4729
def spatial_pyramid_pool(previous_conv, num_sample, previous_conv_size, out_pool_size):
    if str(image_size[0]) == '?':
        previous_conv_size[0] = 512
    if str(image_size[1]) == '?':
        previous_conv_size[1] = 512
    
    spp = tf.Variable(tf.truncated_normal([num_sample, ] stddev=0.01))
    
    for i in range(0, len(out_pool_size)):
        h_strd = previous_conv_size[0] / out_pool_size[i]
        w_strd = previous_conv_size[1] / out_pool_size[i]
        h_wid = previous_conv_size[0] - h_strd * out_pool_size[i] + 1
        w_wid = previous_conv_size[1] - w_strd * out_pool_size[i] + 1
        max_pool = tf.nn.max_pool(previous_conv,
                                   ksize=[1,h_wid,w_wid, 1],
                                   strides=[1,h_strd, w_strd,1],
                                   padding='VALID')
        if (i == 0):
			spp = tf.reshape(max_pool, [num_sample, -1])
		else:
			spp = tf.concat(1, [spp, tf.reshape(max_pool, [num_sample, -1])])
    
    return spp

# --------------------------------------------------------------------------
# Modeling
size_cluster = defaultdict(list)
for tid in trnid:
    img = Image.open(data_dir[tid])
    key = (img.size[0] - img.size[0] % 10, img.size[1] - img.size[1] % 10)
    size_cluster[key].append(tid)
    
size_cluster_keys = size_cluster.keys()

train_accuracies = []
train_cost = []
validation_accuracies = []
x_range = []
batch_size = 50
print('Training ...')

# Training block
# 1. Combime all iamges have the same size to a batch.
# 2. Then, train parameters in a batch
# 3. Transfer trained parameters to another batch
it = 0
while it < TRAINING_ITERATIONS:
    graph = tf.Graph()
    with graph.as_default():
        y_train = labels[size_cluster[size_cluster_keys[it%len(size_cluster_keys)]]]
        print(len(y_train))
        if len(y_train) < 50:
          batch_size = len(y_train)

        y_train = dense_to_one_hot(y_train, num_classes)
        x_train = [data_dir[i] for i in size_cluster[size_cluster_keys[it%len(size_cluster_keys)]]]

        input_queue_train = tf.train.slice_input_producer([x_train, y_train],
                                                        num_epochs=None,
                                                        shuffle=True)

        x_train, y_train = read_images_from_disk(input_queue_train)

        print(size_cluster_keys[it%len(size_cluster_keys)])
        x_train = tf.image.resize_images(x_train,
                                       [size_cluster_keys[it%len(size_cluster_keys)][1]/2,
                                       size_cluster_keys[it%len(size_cluster_keys)][0]/2],
                                       method=1, align_corners=False)

        # x_train.set_shape((size_cluster_keys[it%len(size_cluster_keys)][0],
        #                                   size_cluster_keys[it%len(size_cluster_keys)][1], 3))

        x_train, y_train = tf.train.batch([x_train, y_train], batch_size = batch_size)

        x = tf.placeholder('float', shape = x_train.get_shape())
        y_ = tf.placeholder('float', shape = [None, num_classes])

        conv1W = tf.Variable(net_data["conv1"][0])
        conv1b = tf.Variable(net_data["conv1"][1])
        conv2W = tf.Variable(net_data["conv2"][0])
        conv2b = tf.Variable(net_data["conv2"][1])
        conv3W = tf.Variable(net_data["conv3"][0])
        conv3b = tf.Variable(net_data["conv3"][1])
        conv4W = tf.Variable(net_data["conv4"][0])
        conv4b = tf.Variable(net_data["conv4"][1])
        conv5W = tf.Variable(net_data["conv5"][0])
        conv5b = tf.Variable(net_data["conv5"][1])
        fc6W = weight_variable([hidden_dim * 256, 4096], 'fc6W')
        fc6b = tf.Variable(net_data["fc6"][1])
        fc7W = tf.Variable(net_data["fc7"][0])
        fc7b = tf.Variable(net_data["fc7"][1])
        fc8W = weight_variable([4096, num_classes], 'W_fc8')
        fc8b = bias_variable([num_classes], 'b_fc8')
        keep_prob = tf.placeholder('float')


        def model(x):
            # conv1
            # conv(11, 11, 96, 4, 4, padding='VALID', name='conv1')
            k_h = 11;
            k_w = 11;
            c_o = 96;
            s_h = 4;
            s_w = 4
            conv1_in = conv(x, conv1W, conv1b, k_h, k_w, c_o, s_h, s_w, padding="SAME", group=1)
            conv1 = tf.nn.relu(conv1_in)

            # lrn1
            # lrn(2, 2e-05, 0.75, name='norm1')
            radius = 5;
            alpha = 0.0001;
            beta = 0.75;
            bias = 1.0
            lrn1 = tf.nn.local_response_normalization(conv1,
                                                      depth_radius=radius,
                                                      alpha=alpha,
                                                      beta=beta,
                                                      bias=bias)

            # maxpool1
            # max_pool(3, 3, 2, 2, padding='VALID', name='pool1')
            k_h = 3;
            k_w = 3;
            s_h = 2;
            s_w = 2;
            padding = 'VALID'
            maxpool1 = tf.nn.max_pool(lrn1, ksize=[1, k_h, k_w, 1], strides=[1, s_h, s_w, 1], padding=padding)

            # conv2
            # conv(5, 5, 256, 1, 1, group=2, name='conv2')
            k_h = 5;
            k_w = 5;
            c_o = 256;
            s_h = 1;
            s_w = 1;
            group = 2
            conv2_in = conv(maxpool1, conv2W, conv2b, k_h, k_w, c_o, s_h, s_w, padding="SAME", group=group)
            conv2 = tf.nn.relu(conv2_in)

            # lrn2
            # lrn(2, 2e-05, 0.75, name='norm2')
            radius = 5;
            alpha = 0.0001;
            beta = 0.75;
            bias = 1.0
            lrn2 = tf.nn.local_response_normalization(conv2,
                                                      depth_radius=radius,
                                                      alpha=alpha,
                                                      beta=beta,
                                                      bias=bias)

            # maxpool2
            # max_pool(3, 3, 2, 2, padding='VALID', name='pool2')
            k_h = 3;
            k_w = 3;
            s_h = 2;
            s_w = 2;
            padding = 'VALID'
            maxpool2 = tf.nn.max_pool(lrn2, ksize=[1, k_h, k_w, 1], strides=[1, s_h, s_w, 1], padding=padding)

            # conv3
            # conv(3, 3, 384, 1, 1, name='conv3')
            k_h = 3;
            k_w = 3;
            c_o = 384;
            s_h = 1;
            s_w = 1;
            group = 1

            conv3_in = conv(maxpool2, conv3W, conv3b, k_h, k_w, c_o, s_h, s_w, padding="SAME", group=group)
            conv3 = tf.nn.relu(conv3_in)

            # conv4
            # conv(3, 3, 384, 1, 1, group=2, name='conv4')
            k_h = 3;
            k_w = 3;
            c_o = 384;
            s_h = 1;
            s_w = 1;
            group = 2
            conv4_in = conv(conv3, conv4W, conv4b, k_h, k_w, c_o, s_h, s_w, padding="SAME", group=group)
            conv4 = tf.nn.relu(conv4_in)

            # conv5
            # conv(3, 3, 256, 1, 1, group=2, name='conv5')
            k_h = 3;
            k_w = 3;
            c_o = 256;
            s_h = 1;
            s_w = 1;
            group = 2
            conv5_in = conv(conv4, conv5W, conv5b, k_h, k_w, c_o, s_h, s_w, padding="SAME", group=group)
            conv5 = tf.nn.relu(conv5_in)

            # maxpool5
            # max_pool(3, 3, 2, 2, padding='VALID', name='pool5')
            k_h = 3;
            k_w = 3;
            s_h = 2;
            s_w = 2;
            maxpool5 = spatial_pyramid_pool(conv5,
                                            conv5.get_shape()[0],
                                           [conv5.get_shape()[1], conv5.get_shape()[2]],
                                           out_pool_size)

            # fc6
            # fc(4096, name='fc6')
            fc6 = tf.nn.relu_layer(tf.reshape(maxpool5, [-1, int(prod(maxpool5.get_shape()[1:]))]), fc6W, fc6b)
            fc6_drop = tf.nn.dropout(fc6, keep_prob)

            # fc7
            # fc(4096, name='fc7')
            fc7 = tf.nn.relu_layer(fc6_drop, fc7W, fc7b)
            fc7_drop = tf.nn.dropout(fc7, keep_prob)
            # fc8
            # fc(1000, relu=False, name='fc8')
            fc8 = tf.nn.xw_plus_b(fc7_drop, fc8W, fc8b)

            # prob
            # softmax(name='prob'))
            return fc8
        
        logits = model(x)
        cross_entropy = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(logits, y_))
        regularizers = tf.nn.l2_loss(conv1W) + tf.nn.l2_loss(conv1b) + \
                       tf.nn.l2_loss(conv2W) + tf.nn.l2_loss(conv2b) + \
                       tf.nn.l2_loss(conv3W) + tf.nn.l2_loss(conv3b) + \
                       tf.nn.l2_loss(conv4W) + tf.nn.l2_loss(conv4b) + \
                       tf.nn.l2_loss(conv5W) + tf.nn.l2_loss(conv5b) + \
                       tf.nn.l2_loss(fc6W) + tf.nn.l2_loss(fc6b) + \
                       tf.nn.l2_loss(fc7W) + tf.nn.l2_loss(fc7b) + \
                       tf.nn.l2_loss(fc8W) + tf.nn.l2_loss(fc8b)

        loss = tf.reduce_mean(cross_entropy + WEIGHT_DECAY * regularizers)

        cross_entropy = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(logits, y_))
        # optimisation loss function
        global_step = tf.Variable(0)
        learning_rate = tf.train.exponential_decay(LEARNING_RATE, global_step, 10000, 0.9, staircase=True)
        train_step = tf.train.GradientDescentOptimizer(learning_rate).minimize(loss, global_step=global_step)

        # evaluation
        correct_prediction = tf.equal(tf.argmax(logits,1), tf.argmax(y_,1))
        accuracy = tf.reduce_mean(tf.cast(correct_prediction, 'float'))
        predict = tf.argmax(logits,1)
        saver = tf.train.Saver({v.op.name: v for v in [conv1W, conv1b,
                                                       conv2W, conv2b,
                                                       conv3W, conv3b,
                                                       conv4W, conv4b,
                                                       conv5W, conv5b,
                                                       fc6W, fc6b,
                                                       fc7W, fc7b,
                                                       fc8W, fc8b]})


    with tf.Session(graph=graph) as sess:
        init = tf.initialize_all_variables()
        sess.run(init)
        coord = tf.train.Coordinator()
        threads = tf.train.start_queue_runners(coord=coord)
        # saver.restore(sess, './alex_model_spp.ckpt')

        cnt_tmp = 0
        xtrain, ytrain = sess.run([x_train, y_train])
        for i in range(1000):
            it = it + 1
            train_accuracy = accuracy.eval(feed_dict = {x: xtrain,
                                                        y_: ytrain, 
                                                        keep_prob: 1.0})
            
            
            cost = cross_entropy.eval(feed_dict = {x: xtrain, 
                                                   y_: ytrain, 
                                                   keep_prob: 1.0})
            
            print('training_accuracy => %.4f, cost value => %.4f for step %d'
                  %(train_accuracy, cost, it))

            if (train_accuracy > 0.95):
                cnt_tmp = cnt_tmp + 1
            #    break

            if (cnt_tmp > 10):
                break

            train_accuracies.append(train_accuracy)
            x_range.append(it)
            train_cost.append(cost)
            sess.run(train_step, feed_dict = {x: xtrain, y_: ytrain, keep_prob: 1.0})

        saver.save(sess, './alex_model_spp.ckpt')
        coord.request_stop()
        coord.join(threads)
    sess.close()
    del sess
	
# Plot accuracy and loss curve
plt.plot(x_range, train_cost,'-b')
plt.ylabel('spp_cost')
plt.xlabel('step')
plt.savefig('spp_cost.png')
plt.close()
plt.plot(x_range, train_accuracies,'-b')
plt.ylabel('spp_accuracies')
plt.ylim(ymax = 1.1)
plt.xlabel('step')
plt.savefig('spp_accuracy.png')


# --------------------------------------------------------------------------
# Testing block
# 1. Gather all images have the same size into a batch
# 2. Feed to Alexnet_SPP to predict the expected labels
it = 0
result = list()
f = open('result_spp.txt', 'w')
while it < len(tstid):
    if (it % 10 == 0):
        print(it)
    graph = tf.Graph()
    with graph.as_default():
        # with tf.device('/cpu:0'):
        img = Image.open(data_dir[tstid[it]])
        filename_queue = tf.train.string_input_producer([data_dir[tstid[it]]])
        reader = tf.WholeFileReader()
        key, value = reader.read(filename_queue)
        my_img = tf.image.decode_jpeg(value, channels = 3)
        # my_img = tf.cast(my_img, tf.float32)
        my_img = tf.image.resize_images(my_img,
                                        [img.size[1] / 2,
                                        img.size[0] / 2],
                                        method = 1,
                                        align_corners = False)

        my_img = tf.expand_dims(my_img, 0)

        x = tf.placeholder('float', shape=my_img.get_shape())
        print(my_img.get_shape())
        conv1W = tf.Variable(net_data["conv1"][0])
        conv1b = tf.Variable(net_data["conv1"][1])
        conv2W = tf.Variable(net_data["conv2"][0])
        conv2b = tf.Variable(net_data["conv2"][1])
        conv3W = tf.Variable(net_data["conv3"][0])
        conv3b = tf.Variable(net_data["conv3"][1])
        conv4W = tf.Variable(net_data["conv4"][0])
        conv4b = tf.Variable(net_data["conv4"][1])
        conv5W = tf.Variable(net_data["conv5"][0])
        conv5b = tf.Variable(net_data["conv5"][1])
        fc6W = weight_variable([hidden_dim * 256, 4096], 'fc6W')
        fc6b = tf.Variable(net_data["fc6"][1])
        fc7W = tf.Variable(net_data["fc7"][0])
        fc7b = tf.Variable(net_data["fc7"][1])
        fc8W = weight_variable([4096, num_classes], 'W_fc8')
        fc8b = bias_variable([num_classes], 'b_fc8')
        keep_prob = tf.placeholder('float')


        def model(x):
            # conv1
            # conv(11, 11, 96, 4, 4, padding='VALID', name='conv1')
            k_h = 11;
            k_w = 11;
            c_o = 96;
            s_h = 4;
            s_w = 4
            conv1_in = conv(x, conv1W, conv1b, k_h, k_w, c_o, s_h, s_w, padding="SAME", group=1)
            conv1 = tf.nn.relu(conv1_in)

            # lrn1
            # lrn(2, 2e-05, 0.75, name='norm1')
            radius = 5;
            alpha = 0.0001;
            beta = 0.75;
            bias = 1.0
            lrn1 = tf.nn.local_response_normalization(conv1,
                                                      depth_radius=radius,
                                                      alpha=alpha,
                                                      beta=beta,
                                                      bias=bias)

            # maxpool1
            # max_pool(3, 3, 2, 2, padding='VALID', name='pool1')
            k_h = 3;
            k_w = 3;
            s_h = 2;
            s_w = 2;
            padding = 'VALID'
            maxpool1 = tf.nn.max_pool(lrn1, ksize=[1, k_h, k_w, 1], strides=[1, s_h, s_w, 1], padding=padding)

            # conv2
            # conv(5, 5, 256, 1, 1, group=2, name='conv2')
            k_h = 5;
            k_w = 5;
            c_o = 256;
            s_h = 1;
            s_w = 1;
            group = 2
            conv2_in = conv(maxpool1, conv2W, conv2b, k_h, k_w, c_o, s_h, s_w, padding="SAME", group=group)
            conv2 = tf.nn.relu(conv2_in)

            # lrn2
            # lrn(2, 2e-05, 0.75, name='norm2')
            radius = 5;
            alpha = 0.0001;
            beta = 0.75;
            bias = 1.0
            lrn2 = tf.nn.local_response_normalization(conv2,
                                                      depth_radius=radius,
                                                      alpha=alpha,
                                                      beta=beta,
                                                      bias=bias)

            # maxpool2
            # max_pool(3, 3, 2, 2, padding='VALID', name='pool2')
            k_h = 3;
            k_w = 3;
            s_h = 2;
            s_w = 2;
            padding = 'VALID'
            maxpool2 = tf.nn.max_pool(lrn2, ksize=[1, k_h, k_w, 1], strides=[1, s_h, s_w, 1], padding=padding)

            # conv3
            # conv(3, 3, 384, 1, 1, name='conv3')
            k_h = 3;
            k_w = 3;
            c_o = 384;
            s_h = 1;
            s_w = 1;
            group = 1

            conv3_in = conv(maxpool2, conv3W, conv3b, k_h, k_w, c_o, s_h, s_w, padding="SAME", group=group)
            conv3 = tf.nn.relu(conv3_in)

            # conv4
            # conv(3, 3, 384, 1, 1, group=2, name='conv4')
            k_h = 3;
            k_w = 3;
            c_o = 384;
            s_h = 1;
            s_w = 1;
            group = 2
            conv4_in = conv(conv3, conv4W, conv4b, k_h, k_w, c_o, s_h, s_w, padding="SAME", group=group)
            conv4 = tf.nn.relu(conv4_in)

            # conv5
            # conv(3, 3, 256, 1, 1, group=2, name='conv5')
            k_h = 3;
            k_w = 3;
            c_o = 256;
            s_h = 1;
            s_w = 1;
            group = 2
            conv5_in = conv(conv4, conv5W, conv5b, k_h, k_w, c_o, s_h, s_w, padding="SAME", group=group)
            conv5 = tf.nn.relu(conv5_in)

            # maxpool5
            # max_pool(3, 3, 2, 2, padding='VALID', name='pool5')
            k_h = 3;
            k_w = 3;
            s_h = 2;
            s_w = 2;
            maxpool5 = spatial_pyramid_pool(conv5,
                                            conv5.get_shape()[0],
                                           [conv5.get_shape()[1], conv5.get_shape()[2]],
                                           out_pool_size)

            # fc6
            # fc(4096, name='fc6')
            fc6 = tf.nn.relu_layer(tf.reshape(maxpool5, [-1, int(prod(maxpool5.get_shape()[1:]))]), fc6W, fc6b)
            fc6_drop = tf.nn.dropout(fc6, keep_prob)

            # fc7
            # fc(4096, name='fc7')
            fc7 = tf.nn.relu_layer(fc6_drop, fc7W, fc7b)
            fc7_drop = tf.nn.dropout(fc7, keep_prob)
            # fc8
            # fc(1000, relu=False, name='fc8')
            fc8 = tf.nn.xw_plus_b(fc7_drop, fc8W, fc8b)

            # prob
            # softmax(name='prob'))
            prob = tf.nn.softmax(fc8)
            return prob

        logits = model(x)
        predict = tf.argmax(logits, 1)
        saver = tf.train.Saver({v.op.name: v for v in [conv1W, conv1b,
                                                       conv2W, conv2b,
                                                       conv3W, conv3b,
                                                       conv4W, conv4b,
                                                       conv5W, conv5b,
                                                       fc6W, fc6b,
                                                       fc7W, fc7b,
                                                       fc8W, fc8b]})

    with tf.Session(graph=graph) as sess:
        init = tf.initialize_all_variables()
        sess.run(init)
        coord = tf.train.Coordinator()
        threads = tf.train.start_queue_runners(coord=coord)
        saver.restore(sess, './alex_model_spp.ckpt')
        image = sess.run(my_img)
        predict = predict.eval(feed_dict={x: image, keep_prob: 1.0})
        result.append(predict[0])
        f.write(data_dir[tstid[it]] + '\t' + str(predict[0]) + '\t' + str(labels[tstid[it]]))
        f.write('\n')
        coord.request_stop()
        coord.join(threads)
    sess.close()
    del sess
    it = it + 1

print('Test accuracy: %f' %(sum(np.array(result) == np.array(labels[tstid])).astype('float')/len(tstid)))
f.close()
