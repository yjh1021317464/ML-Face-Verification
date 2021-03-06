import numpy as np
from PIL import Image
import losses
import torch

floatX = np.float32
theano_type = 'float32'
torch_type = torch.float32


class Convolution(object):
    def __init__(self, input_shape, out_channels=3, kernel_size=3, stride=1, learning_rate=0.0006):
        self.input_shape = input_shape  # (batch_size, h, w, c)
        self.in_channels = input_shape[-1]  # Normally RGB.
        self.batch_size = input_shape[0]
        # Kernel total size = kernel_size * kernel_size.
        self.kernel_size = kernel_size
        self.stride = stride
        self.out_channels = out_channels  # Filter num.
        # input channels -> output channels
        self.filters = 0.01 * np.random.normal(
            0, size=(self.kernel_size, self.kernel_size, self.in_channels, out_channels)).astype(np.float32)
        self.biases = np.random.randn(self.out_channels).astype(floatX)
        self.w_gradient = np.zeros(self.filters.shape, dtype=floatX)
        self.eta_forward = np.zeros(self.input_shape[1:], dtype=floatX)  # TODO: Check this.
        self.learning_rate = learning_rate

        # 用 pytorch 加速
        # self.filters = torch.randn((self.kernel_size, self.kernel_size, self.in_channels, out_channels),
        #                            dtype=torch_type)
        # self.w_gradient = torch.empty(self.filters.shape, dtype=torch_type)

        h = (self.input_shape[1] - self.kernel_size) // self.stride + 1 + 2 * (self.kernel_size - 1)
        w = (self.input_shape[2] - self.kernel_size) // self.stride + 1 + 2 * (self.kernel_size - 1)
        self.padding_eta = np.zeros((self.batch_size, h, w, self.out_channels), dtype=floatX)

    def forward(self, img):
        # Update img.
        self.img = img
        self.padding_img = self.img
        # Padding.
        if (self.input_shape[2] - self.kernel_size) % self.stride != 0:
            self.padding_img = np.lib.pad(self.img, ((0, 0), (0, self.kernel_size -
                                                              (self.input_shape[2] - self.kernel_size) % self.stride),
                                                     (0, 0), (0, 0)),
                                          'constant')
        if (self.input_shape[3] - self.kernel_size) % self.stride != 0:
            self.padding_img = np.lib.pad(self.img, ((0, 0), (0, 0), (0, self.kernel_size -
                                                                      (self.input_shape[
                                                                           3] - self.kernel_size) % self.stride),
                                                     (0, 0)),
                                          'constant')
        print(self.padding_img.shape)
        self.input_shape = self.padding_img.shape
        # Initialize feature_maps, the num of feature_map is out_channels.
        # feature_maps = np.zeros(
        #     (self.batch_size, (self.input_shape[1] - self.kernel_size) // self.stride + 1,
        #      (self.input_shape[2] - self.kernel_size) // self.stride + 1, self.out_channels), dtype=floatX)

        # Do convolution.
        col_img = self.im2col(self.padding_img.shape, self.kernel_size, self.padding_img)
        feature_maps = np.tensordot(col_img, self.filters, axes=[(3, 4, 5), (0, 1, 2)]) + self.biases
        return feature_maps

    # todo 性能瓶颈
    def gradient(self, eta):
        # eta shape = (n, h, w, c).
        print(eta.shape, self.padding_img.shape)
        h, w = eta.shape[1], eta.shape[2]
        for i in range(self.kernel_size):
            for j in range(self.kernel_size):
                # self.w_gradient[i, j, :, :] = torch.tensordot(
                #     torch.tensor(self.padding_img[:, i: i + h, j: j + w, :]), torch.tensor(eta),
                #     dims=([0, 1, 2], [0, 1, 2])
                # )
                self.w_gradient[i, j, :, :] = np.tensordot(self.padding_img[:, i: i + h, j: j + w, :], eta,
                                                           axes=([0, 1, 2], [0, 1, 2]))
        # print(self.w_gradient)
        # self.b_gradient = np.array([np.sum(eta[:, :, :, i])
        #                             for i in range(self.out_channels)])
        self.b_gradient = eta.sum(axis=(0, 1, 2))
        # print('===========')
        flip_filters = np.flip(self.filters, (0, 1))
        # print('===========')
        # padding_eta = np.lib.pad(eta, ((0, 0), (self.kernel_size - 1, self.kernel_size - 1),
        #                                (self.kernel_size - 1, self.kernel_size - 1), (0, 0)), 'constant')
        delta = self.kernel_size - 1
        self.padding_eta[:, delta:-delta, delta:-delta, :] = eta
        # print('============eta', eta.shape)
        # print('============kernel', self.kernel_size)
        # print('============padding', padding_eta.shape)
        # print('============', padding_eta[:, 1:- 1, 1:- 1:] == eta)
        col_eta = self.im2col(self.padding_eta.shape, self.kernel_size, self.padding_eta)
        print(col_eta.shape, flip_filters.shape)
        self.eta_forward = np.tensordot(
            col_eta, flip_filters, axes=[(3, 4, 5), (0, 1, 3)])
        # self.eta_forward = torch.tensordot(
        #     torch.tensor(col_eta), torch.tensor(flip_filters), dims=[(3, 4, 5), (0, 1, 3)]
        # ).numpy()
        return self.eta_forward

    def backward(self):
        print('====conv params', self.filters.mean())
        print('====conv biases', self.biases.mean())
        print('====conv params grad', self.w_gradient.mean())
        print('====conv biases grad', self.b_gradient.mean())
        self.filters -= self.learning_rate * self.w_gradient
        self.biases -= self.learning_rate * self.b_gradient

    def im2col(self, shape, ks, image):
        N, H, W, C = shape
        out_h = (H - ks) // self.stride + 1
        out_w = (W - ks) // self.stride + 1
        out_shape = (N, out_h, out_w, ks, ks, C)
        strides = (image.strides[0], image.strides[1] * self.stride, image.strides[2] * self.stride, *image.strides[1:])
        col_img = np.lib.stride_tricks.as_strided(image, shape=out_shape, strides=strides)
        return col_img


class Pooling(object):
    def __init__(self, input_shape, pool_size=3, stride=1):
        self.batch_size = input_shape[0]  # (n, h, w, c)
        self.shape = input_shape[1:3]
        self.out_channels = input_shape[-1]
        self.pool_size = pool_size
        self.stride = stride
        self.gradient_out = np.zeros(
            (self.batch_size, self.shape[0], self.shape[1], self.out_channels), dtype=floatX)

    def forward(self, feature_maps):
        self.feature_maps = feature_maps
        print(feature_maps.shape)
        pool_output = feature_maps.reshape(feature_maps.shape[0], feature_maps.shape[1] // self.pool_size,
                                           self.pool_size, feature_maps.shape[2] // self.pool_size, self.pool_size,
                                           feature_maps.shape[3]).max(axis=(2, 4))
        self.index = pool_output.repeat(self.pool_size, axis=1).repeat(self.pool_size, axis=2) == feature_maps
        return pool_output

    def gradient(self, eta):
        self.gradient_out = eta.repeat(self.pool_size, axis=1).repeat(self.pool_size, axis=2) * self.index
        return self.gradient_out


class Relu(object):
    mask: torch.Tensor

    def __init__(self, input_shape):
        self.input_shape = input_shape
        self.gradient_out = np.ones(input_shape, dtype=floatX)

    def forward(self, feature_maps):
        self.feature_maps = feature_maps
        # relu = np.maximum(feature_maps, 0)
        print("relu", feature_maps.shape)
        self.mask = torch.tensor(feature_maps > 0, dtype=torch.int)
        return torch.mul(
            torch.tensor(feature_maps), self.mask
        ).numpy()

    def gradient(self, eta):
        # print("gradient",self.gradient_out.shape)
        # self.gradient_out[self.feature_maps < 0] = 0
        # return self.gradient_out
        # # TODO: Not check.
        return torch.mul(
            torch.tensor(eta), self.mask
        ).numpy()


if __name__ == "__main__":
    # img = Image.open(b"../../../LFW/match pairs/0001/Aaron_Peirsol_0001.jpg")
    # print(img.size)
    # img = np.array(img, dtype=floatX)
    # print(img.shape)
    z = np.random.rand(10, 51, 51, 3).astype(floatX)
    # print(z)
    Conv1 = Convolution(z.shape, kernel_size=2, stride=1, learning_rate=0.00001)
    out1 = Conv1.forward(z)
    pooling1 = Pooling(out1.shape, pool_size=2)
    out2 = pooling1.forward(out1)
    Conv2 = Convolution(out2.shape, kernel_size=2, stride=1, learning_rate=0.00001)
    y_pred = Conv2.forward(out2)
    print(y_pred.shape)
    y_true = np.ones((10, 24, 24, 3), dtype=floatX)
    for i in range(10):
        error, dy = losses.mean_squared_error(y_pred, y_true)
        print(error)
        print("------")
        eta3to2 = Conv2.gradient(dy)
        Conv2.backward()
        eta2to1 = pooling1.gradient(eta3to2)
        _ = Conv1.gradient(eta2to1)
        Conv1.backward()
        out1 = Conv1.forward(z)
        out2 = pooling1.forward(out1)
        y_pred = Conv2.forward(out2)

    # batch_size = 100
    # learning_rate_conv1 = 0.01 / batch_size
    # learning_rate_conv2 = 0.01 / batch_size
    # learning_rate_conv3 = 0.01 / batch_size
    #
    # conv1_input = (batch_size, 247, 247, 3)
    # max1_input = (batch_size, 244, 244, 20)
    # conv2_input = (batch_size, 122, 122, 20)
    # max2_input = (batch_size, 120, 120, 40)
    # conv3_input = (batch_size, 60, 60, 40)
    # max3_input = (batch_size, 58, 58, 60)
    # conv1 = Convolution(input_shape=conv1_input, out_channels=20, kernel_size=4, stride=1,
    #                     learning_rate=learning_rate_conv1)
    # conv2 = Convolution(input_shape=conv2_input, out_channels=40, kernel_size=3, stride=1,
    #                     learning_rate=learning_rate_conv2)
    # conv3 = Convolution(input_shape=conv3_input, out_channels=60, kernel_size=3, stride=1,
    #                     learning_rate=learning_rate_conv3)
    #
    # relu1 = Relu(max1_input)
    # relu2 = Relu(max2_input)
    # relu3 = Relu(max3_input)
    #
    # max1 = Pooling(input_shape=max1_input, pool_size=2, stride=2)
    # max2 = Pooling(input_shape=max2_input, pool_size=2, stride=2)
    # max3 = Pooling(input_shape=max3_input, pool_size=2, stride=2)
