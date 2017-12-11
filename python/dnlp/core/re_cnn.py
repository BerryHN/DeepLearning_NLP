# -*- coding: UTF-8 -*-
import tensorflow as tf
import numpy as np
import pickle
from collections import Counter
from dnlp.core.re_cnn_base import RECNNBase
from dnlp.config import RECNNConfig
from dnlp.utils.constant import BATCH_PAD, BATCH_PAD_VAL


class RECNN(RECNNBase):
  def __init__(self, config: RECNNConfig, dtype: type = tf.float32, dict_path: str = '', mode: str = 'train',
               data_path: str = '', relation_count: int = 2, model_path: str = '', embedding_path: str = '',
               remark:str=''):
    tf.reset_default_graph()
    RECNNBase.__init__(self, config, dict_path)
    self.dtype = dtype
    self.mode = mode
    self.data_path = data_path
    self.model_path = model_path
    self.relation_count = relation_count
    self.embedding_path = embedding_path
    self.remark = remark

    self.concat_embed_size = self.word_embed_size + 2 * self.position_embed_size
    self.input_characters = tf.placeholder(tf.int32, [None, self.batch_length])
    self.input_position = tf.placeholder(tf.int32, [None, self.batch_length])
    self.input = tf.placeholder(self.dtype, [None, self.batch_length, self.concat_embed_size, 1])
    self.input_relation = tf.placeholder(self.dtype, [None, self.relation_count])
    self.position_embedding = self.__weight_variable([2 * self.batch_length - 1, self.position_embed_size],
                                                     name='position_embedding')
    if self.embedding_path:
      self.word_embedding = tf.Variable(np.load(self.embedding_path), dtype=self.dtype, name='word_embedding',
                                        trainable=True)
    else:
      self.word_embedding = self.__weight_variable([self.words_size, self.word_embed_size], name='word_embedding')
    self.conv_kernel = self.get_conv_kernel()
    self.bias = [self.__weight_variable([self.filter_size], name='conv_bias')] * len(self.window_size)
    self.full_connected_weight = self.__weight_variable([self.filter_size * len(self.window_size), self.relation_count],
                                                        name='full_connected_weight')
    self.full_connected_bias = self.__weight_variable([self.relation_count], name='full_connected_bias')
    self.position_lookup = tf.nn.embedding_lookup(self.position_embedding, self.input_position)
    self.character_lookup = tf.nn.embedding_lookup(self.word_embedding, self.input_characters)
    self.character_embed_holder = tf.placeholder(self.dtype,
                                                 [None, self.batch_length, self.word_embed_size])
    self.primary_embed_holder = tf.placeholder(self.dtype,
                                               [None, self.batch_length, self.position_embed_size])
    self.secondary_embed_holder = tf.placeholder(self.dtype,
                                                 [None, self.batch_length, self.position_embed_size])
    self.emebd_concat = tf.expand_dims(
      tf.concat([self.character_embed_holder, self.primary_embed_holder, self.secondary_embed_holder], 2), 3)
    self.words, self.primary, self.secondary, self.labels = self.load_data()

    if self.mode == 'train':
      self.start = 0
      self.hidden_layer = tf.layers.dropout(self.get_hidden(), self.dropout_rate)
      self.data_count = len(self.words)
      self.saver = tf.train.Saver(max_to_keep=100)
    else:
      self.hidden_layer = self.get_hidden()
      # self.hidden_layer = tf.expand_dims(tf.layers.dropout(self.get_hidden(), self.dropout_rate), 0)
      self.sess = tf.Session()
      self.saver = tf.train.Saver().restore(self.sess, self.model_path)
    self.output_no_softmax = tf.matmul(self.hidden_layer, self.full_connected_weight) + self.full_connected_bias
    self.output = tf.nn.softmax(tf.matmul(self.hidden_layer, self.full_connected_weight) + self.full_connected_bias)
    self.params = [self.position_embedding, self.word_embedding, self.full_connected_weight,
                   self.full_connected_bias] + self.conv_kernel + self.bias
    self.regularization = tf.contrib.layers.apply_regularization(tf.contrib.layers.l2_regularizer(self.lam),
                                                                 self.params)
    self.loss = tf.reduce_sum(tf.square(self.output - self.input_relation)) / self.batch_size + self.regularization
    self.cross_entropy = tf.nn.softmax_cross_entropy_with_logits(labels=self.input_relation,
                                                                 logits=self.output_no_softmax) + self.regularization
    # self.optimizer = tf.train.GradientDescentOptimizer(self.learning_rate)
    self.optimizer = tf.train.AdagradOptimizer(self.learning_rate)
    self.train_model = self.optimizer.minimize(self.loss)
    self.train_cross_entropy_model = self.optimizer.minimize(self.cross_entropy)

  def get_conv_kernel(self):
    conv_kernel = []
    for w in self.window_size:
      conv_kernel.append(self.__weight_variable([w, self.concat_embed_size, 1, self.filter_size], name='conv_kernel'))
    return conv_kernel

  def get_max_pooling(self, x):
    max_pooling = []
    for w in self.window_size:
      max_pooling.append(self.max_pooling(x, w))
    return max_pooling

  def get_hidden(self):
    h = None
    for w, conv, bias in zip(self.window_size, self.conv_kernel, self.bias):
      if h is None:
        h = tf.squeeze(self.max_pooling(tf.nn.relu(self.conv(conv) + bias), w))
      else:
        hh = tf.squeeze(self.max_pooling(tf.nn.relu(self.conv(conv) + bias), w))
        # if self.mode == 'train':
        h = tf.concat([h, hh], 1)
        # else:
        #   h = tf.concat([h, hh], 0)
    return h

  def conv(self, conv_kernel):
    return tf.nn.conv2d(self.input, conv_kernel, strides=[1, 1, 1, 1], padding='VALID')

  def max_pooling(self, x, window_size):
    return tf.nn.max_pool(x, ksize=[1, self.batch_length - window_size + 1, 1, 1],
                          strides=[1, 1, 1, 1], padding='VALID')

  def fit(self, epochs=40, interval=5):
    with tf.Session() as sess:
      tf.global_variables_initializer().run()
      sess.graph.finalize()
      for i in range(1, epochs + 1):
        print('epoch:' + str(i))
        for _ in range(self.data_count // self.batch_size):
          words, primary, secondary, labels = self.load_batch()
          character_embeds, primary_embeds = sess.run([self.character_lookup, self.position_lookup],
                                                      feed_dict={self.input_characters: words,
                                                                 self.input_position: primary})
          secondary_embeds = sess.run(self.position_lookup, feed_dict={self.input_position: secondary})
          input = sess.run(self.emebd_concat, feed_dict={self.character_embed_holder: character_embeds,
                                                         self.primary_embed_holder: primary_embeds,
                                                         self.secondary_embed_holder: secondary_embeds})
          # sess.run(self.train_model, feed_dict={self.input: input, self.input_relation: batch['label']})
          sess.run(self.train_cross_entropy_model, feed_dict={self.input: input, self.input_relation: labels})
        if i % interval == 0:
          if self.relation_count == 2:
            model_name = '../dnlp/models/re_{2}/{0}-{1}{3}.ckpt'.format(i, '_'.join(map(str, self.window_size)),
                                                                        'two',self.remark)
          else:
            model_name = '../dnlp/models/re_{2}/{0}-{1}{3}.ckpt'.format(i, '_'.join(map(str, self.window_size)),
                                                                        'multi',self.remark)

          self.saver.save(sess, model_name)

  def predict(self, words, primary, secondary):
    character_embeds, primary_embeds = self.sess.run([self.character_lookup, self.position_lookup],
                                                     feed_dict={self.input_characters: words,
                                                                self.input_position: primary})
    secondary_embeds = self.sess.run(self.position_lookup, feed_dict={self.input_position: secondary})
    input = self.sess.run(self.emebd_concat, feed_dict={self.character_embed_holder: character_embeds,
                                                        self.primary_embed_holder: primary_embeds,
                                                        self.secondary_embed_holder: secondary_embeds})
    output = self.sess.run(self.output, feed_dict={self.input: input})
    return np.argmax(output, 1)

  def evaluate(self):
    res = self.predict(self.words, self.primary, self.secondary)
    res_count = Counter(res)[1]
    target = np.argmax(self.labels, 1)
    target_count = Counter(target)[1]
    correct_number = Counter(np.array(res) - target)
    print(correct_number)
    return self.get_score(np.array(res), target)

  def get_score(self, predict, true):
    types = Counter(predict).keys()
    corr_count = []
    true_count = []
    pred_count = []

    for t in types:
      corr_count.append(len([v for v, c in zip(predict - t, predict - true) if v == 0 and c == 0]))
      true_count.append(len([te for te in true if te == t]))
      pred_count.append(len([pd for pd in predict if pd == t]))

    precs = [c / p for c, p in zip(corr_count, pred_count) if p != 0 and c != 0]
    recalls = [c / r for c, r in zip(corr_count, true_count) if r != 0 and c != 0]
    prec = sum(precs) / len(precs)
    recall = sum(recalls) / len(recalls)
    f1 = 2 * prec * recall / (prec + recall)
    print(prec, recall, f1)
    return prec, recall, f1

  def load_batch(self):
    if self.start + self.batch_size > self.data_count:
      new_start = self.start + self.batch_size - self.data_count
      words = np.concatenate([self.words[self.start:], self.words[:new_start]])
      primary = np.concatenate([self.primary[self.start:], self.primary[:new_start]])
      secondary = np.concatenate([self.secondary[self.start:], self.secondary[:new_start]])
      labels = np.concatenate([self.labels[self.start:], self.labels[:new_start]])
      self.start = new_start
    else:
      new_start = self.start + self.batch_size
      words = self.words[self.start:new_start]
      primary = self.primary[self.start:new_start]
      secondary = self.secondary[self.start:new_start]
      labels = self.labels[self.start:new_start]
      self.start = new_start
    return words, primary, secondary, labels

  def load_data(self):
    primary = []
    secondary = []
    words = []
    labels = []
    with open(self.data_path, 'rb') as f:
      data = pickle.load(f)
      for sentence in data:
        sentence_words = sentence['words']
        if len(sentence_words) < self.batch_length:
          sentence_words += [self.dictionary[BATCH_PAD]] * (self.batch_length - len(sentence_words))
        else:
          sentence_words = sentence_words[:self.batch_length]
        words.append(sentence_words)
        primary.append(np.arange(self.batch_length) - sentence['primary'] + self.batch_length - 1)
        secondary.append(np.arange(self.batch_length) - sentence['secondary'] + self.batch_length - 1)
        sentence_labels = np.zeros([self.relation_count])
        sentence_labels[sentence['type']] = 1
        labels.append(sentence_labels)
    return np.array(words, np.int32), np.array(primary, np.int32), np.array(secondary, np.int32), np.array(labels,
                                                                                                           np.float32)

  def __weight_variable(self, shape, name):
    initial = tf.truncated_normal(shape, stddev=0.1, dtype=self.dtype)
    return tf.Variable(initial, name=name)
