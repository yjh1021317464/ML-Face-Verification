import math

from pooling import Pooling
import other
import os

import torch
import numpy as np

floatX = torch.float32
device = 'cuda'


# 增强版 im2col
def im2col_enhanced(im: torch.Tensor, kernel_size, stride, inner_stride=(1, 1)) -> torch.Tensor:
    kh, kw = kernel_size
    sh, sw = stride
    ish, isw = inner_stride
    b, h, w, c = im.shape
    assert (h - kh * ish) % sh == 0
    assert (w - kw * isw) % sw == 0
    out_h = (h - kh * ish) // sh + 1
    out_w = (w - kw * isw) // sw + 1
    out_size = (b, out_h, out_w, kh, kw, c)
    s = im.stride()
    out_stride = (s[0], s[1] * sh, s[2] * sw, s[1] * ish, s[2] * isw, s[3])
    col_img = im.as_strided(size=out_size, stride=out_stride)
    return col_img


class Convolution:
    batch: int

    in_h: int
    in_w: int
    real_in_h: int
    real_in_w: int
    in_pad_h: int
    in_pad_w: int

    out_h: int
    out_w: int

    kernel_h: int
    kernel_w: int

    stride_h: int
    stride_w: int

    in_channel: int
    out_channel: int

    learning_rate: float

    in_data: torch.Tensor
    filters: torch.Tensor
    biases: torch.Tensor

    # filters_gradient: torch.Tensor

    def __init__(self, input_shape, out_channel, kernel_size, stride, learning_rate, padding=(0, 0),
                 activate_func: str = None):
        self.in_h, self.in_w, self.in_channel = input_shape
        self.learning_rate = learning_rate
        self.out_channel = out_channel
        self.kernel_h, self.kernel_w = kernel_size
        self.stride_h, self.stride_w = stride
        self.in_pad_h, self.in_pad_w = padding

        # padding
        self.real_in_h = self.in_h
        self.real_in_w = self.in_w
        self.in_h += 2 * self.in_pad_h
        self.in_w += 2 * self.in_pad_w
        assert (self.in_h - self.kernel_h) % self.stride_h == 0
        assert (self.in_w - self.kernel_w) % self.stride_w == 0

        self.out_h = (self.in_h - self.kernel_h) // self.stride_h + 1
        self.out_w = (self.in_w - self.kernel_w) // self.stride_w + 1

        # xaiver 初始化
        n_in = self.in_h * self.in_w * self.in_channel
        n_out = self.out_h * self.out_w * self.out_channel
        coe = math.sqrt(6) / math.sqrt(n_in + n_out)
        print('conv coe', coe)
        self.filters = torch.tensor(
            np.random.uniform(-coe, coe, size=(self.kernel_h, self.kernel_w, self.in_channel, out_channel)),
            dtype=floatX, device=device
        )
        self.biases = torch.tensor(
            np.random.uniform(-coe, coe, size=(self.out_channel,)),
            dtype=floatX, device=device
        )
        # self.filters = torch.randn(
        #     (self.kernel_h, self.kernel_w, self.in_channel, out_channel),
        #     dtype=floatX, device=device)
        # self.biases = torch.randn((self.out_channel,), dtype=floatX, device=device)
        # self.filters_gradient = torch.empty(
        #     (self.kernel_h, self.kernel_w, self.in_channel, out_channel),
        #     dtype=floatX, device=device)

        if activate_func == 'relu':
            self.activation = other.Relu()
        elif activate_func == 'mfm':
            self.activation = other.MFM()
        else:
            self.activation = None

    # 已通过测试
    def forward(self, in_data: torch.Tensor) -> torch.Tensor:
        assert in_data.is_cuda
        self.batch = in_data.shape[0]
        assert in_data.shape == (self.batch, self.real_in_h, self.real_in_w, self.in_channel)
        # padding
        if self.in_pad_h != 0 or self.in_pad_w != 0:
            new_in_data = torch.zeros((self.batch, self.in_h, self.in_w, self.in_channel), dtype=floatX, device=device)
            new_in_data[:, self.in_pad_h:-self.in_pad_h, self.in_pad_w:-self.in_pad_w, :] = in_data
            in_data = new_in_data

        self.in_data = in_data
        col_img = im2col_enhanced(in_data, (self.kernel_h, self.kernel_w), (self.stride_h, self.stride_w))
        out = torch.tensordot(col_img, self.filters, dims=[(3, 4, 5), (0, 1, 2)]) \
              + self.biases.reshape((1, 1, 1, self.out_channel))
        if self.activation is not None:
            out = self.activation.forward(out)
        return out

    # 测试通过
    def backward(self, eta: torch.Tensor) -> torch.Tensor:
        assert eta.is_cuda
        if self.activation is not None:
            eta = self.activation.backward(eta)
        assert eta.shape == (self.batch, self.out_h, self.out_w, self.out_channel)
        # filters 梯度
        col_img = im2col_enhanced(self.in_data, (self.kernel_h, self.kernel_w), (self.stride_h, self.stride_w))
        filters_gradient = torch.zeros(
            (self.kernel_h, self.kernel_w, self.in_channel, self.out_channel),
            dtype=floatX, device=device)
        for b in range(self.batch):
            filters_gradient += torch.tensordot(
                col_img[b], eta[b], dims=[(0, 1), (0, 1)]
            )
        # biases 梯度
        biases_gradient = eta.sum(dim=(0, 1, 2))
        # in_data 梯度
        # 这部分的实现参照 PPT
        if self.kernel_h != 1 or self.kernel_w != 1 or self.stride_h != 1 or self.stride_w != 1:
            padding_eta = torch.zeros(
                (self.batch,
                 2 * (self.kernel_h - 1) + (self.out_h - 1) * self.stride_h + 1,
                 2 * (self.kernel_w - 1) + (self.out_w - 1) * self.stride_w + 1,
                 self.out_channel), dtype=floatX, device=device)
            if self.kernel_h != 1 or self.kernel_w != 1:
                pad_h = self.kernel_h - 1
                pad_w = self.kernel_w - 1
                padding_eta[:, pad_h:-pad_h:self.stride_h, pad_w:-pad_w:self.stride_w, :] = eta  # padding_eta 其他部分为0
            else:
                padding_eta[:, ::self.stride_h, ::self.stride_w, :] = eta
        else:
            padding_eta = eta
        filters_flip = self.filters.flip(dims=(0, 1))
        # 进行卷积运算
        col_eta = im2col_enhanced(padding_eta, (self.kernel_h, self.kernel_w), (1, 1))
        assert col_eta.shape == (self.batch, self.in_h, self.in_w, self.kernel_h, self.kernel_w, self.out_channel)
        next_eta = torch.tensordot(col_eta, filters_flip, dims=[(3, 4, 5), (0, 1, 3)])
        assert next_eta.shape == (self.batch, self.in_h, self.in_w, self.in_channel)
        # 更新
        self.filters -= self.learning_rate * filters_gradient
        self.biases -= self.learning_rate * biases_gradient
        # 去 padding
        if self.in_pad_h != 0 or self.in_pad_w != 0:
            next_eta = next_eta[:, self.in_pad_h:-self.in_pad_h, self.in_pad_w:-self.in_pad_w, :]
        return next_eta

    def save(self, folder_path: str):
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
        torch.save(self.filters, os.path.join(folder_path, 'filters.bin'))
        torch.save(self.biases, os.path.join(folder_path, 'biases.bin'))

    def load(self, folder_path: str):
        self.filters = torch.load(os.path.join(folder_path, 'filters.bin'))
        self.biases = torch.load(os.path.join(folder_path, 'biases.bin'))


# 卷积的 naive 实现
def easy_conv(conv: Convolution) -> torch.Tensor:
    output = torch.empty(size=(conv.batch, conv.out_h, conv.out_w, conv.out_channel), dtype=floatX)
    for b in range(conv.batch):
        for i in range(conv.out_h):
            for j in range(conv.out_w):
                h, w = i * conv.stride_h, j * conv.stride_w
                sub = conv.in_data[b, h:h + conv.kernel_h, w:w + conv.kernel_w, :]
                for oc in range(conv.out_channel):
                    assert sub.shape == conv.filters[:, :, :, oc].shape
                    output[b, i, j, oc] = (sub * conv.filters[:, :, :, oc]).sum() + conv.biases[oc]
    return output


# 卷积函数测试
# if __name__ == '__main__':
#     ci = 1
#     co = 1
#     shape = (10, 100, 100, ci)
#     x = torch.arange(np.prod(shape), dtype=floatX).reshape(shape)
#     conv = Convolution(input_shape=shape, out_channel=co, kernel_size=(2, 4), stride=(2, 4), learning_rate=1e-4)
#     res1 = conv.forward(x)
#     # print(res1)
#     res2 = easy_conv(conv)
#     # print(res2)
#     print(((res1 - res2).abs() <= 2e-5).all())
#     # print((res1 - res2).abs().argmax())
#     print(res1.shape)
#     print(np.unravel_index((res1 - res2).abs().argmax(), res1.shape))
#     print((res1 - res2).abs().max())
#     print()

# 反向传播测试
if __name__ == '__main__':
    shape = (400, 200, 200, 3)
    x = torch.randn(shape)
    conv1 = Convolution(input_shape=x.shape, out_channel=20, kernel_size=(4, 4), stride=(2, 2), learning_rate=5e-12,
                        activate_func='relu')
    x1 = conv1.forward(x)
    conv2 = Convolution(input_shape=x1.shape, out_channel=40, kernel_size=(3, 3), stride=(2, 2), learning_rate=5e-12,
                        activate_func='relu')
    x2 = conv2.forward(x1)
    conv3 = Convolution(input_shape=x2.shape, out_channel=60, kernel_size=(2, 2), stride=(1, 1), learning_rate=5e-12,
                        activate_func='relu')
    for i in range(10):
        pred = conv3.forward(conv2.forward(conv1.forward(x)))
        dy = pred
        print(f'mean dy = {dy.abs().mean()}')
        conv1.backward(conv2.backward(conv3.backward(dy)))
