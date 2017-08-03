import numpy as np
import torch

from torch import nn
from .layers import Loss, PostRes, GetPBB

config = {
    'anchors': [10.0, 30.0, 60.],
    'channel': 1,
    'crop_size': [128, 128, 128],
    'stride': 4,
    'max_stride': 16,
    'num_neg': 800,
    'th_neg': 0.02,
    'th_pos_train': 0.5,
    'th_pos_val': 1,
    'num_hard': 2,
    'bound_size': 12,
    'reso': 1,
    'sizelim': 6.,  # mm
    'sizelim2': 30,
    'sizelim3': 40,
    'aug_scale': True,
    'r_rand_crop': 0.3,
    'pad_value': 170,
    'luna_raw': True,
    'cleaning': True,
    'augtype': {'flip': True, 'swap': False, 'scale': True, 'rotate': False},
    'blacklist': [
        '868b024d9fa388b7ddab12ec1c06af38',
        '990fbe3f0a1b53878669967b9afd1441',
        'adc3bbc63d40f8761c59be10f1e504c3'],
    'lr_stage': np.array([50, 100, 120]),
    'lr': [0.01, 0.001, 0.0001],
}


class Net(nn.Module):
    def __init__(self):
        super(Net, self).__init__()
        # The first few layers consumes the most memory, so use simple
        # convolution to save memory. Call these layers preBlock, i.e., before
        # the residual blocks of later layers.
        self.preBlock = nn.Sequential(
            nn.Conv3d(1, 24, kernel_size=3, padding=1),
            nn.BatchNorm3d(24),
            nn.ReLU(inplace=True),
            nn.Conv3d(24, 24, kernel_size=3, padding=1),
            nn.BatchNorm3d(24),
            nn.ReLU(inplace=True))

        # 3 poolings, each pooling downsamples the feature map by a factor 2.
        # 3 groups of blocks. The first block of each group has one pooling.
        num_blocks_forw = [2, 2, 3, 3]
        num_blocks_back = [3, 3]
        self.featureNum_forw = [24, 32, 64, 64, 64]
        self.featureNum_back = [128, 64, 64]

        for i in range(len(num_blocks_forw)):
            blocks = []

            for j in range(num_blocks_forw[i]):
                forw_i_p1 = self.featureNum_forw[i + 1]

                if j == 0:
                    blocks.append(PostRes(self.featureNum_forw[i], forw_i_p1))
                else:
                    blocks.append(PostRes(forw_i_p1, forw_i_p1))

            setattr(self, 'forw' + str(i + 1), nn.Sequential(* blocks))

        for i in range(len(num_blocks_back)):
            blocks = []

            for j in range(num_blocks_back[i]):
                if j == 0:
                    extra = 3 if i == 0 else 0
                    back_forw = self.featureNum_back[i + 1] + self.featureNum_forw[i + 2]
                    blocks.append(
                        PostRes(back_forw + extra, self.featureNum_back[i]))
                else:
                    blocks.append(PostRes(self.featureNum_back[i], self.featureNum_back[i]))

            setattr(self, 'back' + str(i + 2), nn.Sequential(* blocks))

        self.maxpool1 = nn.MaxPool3d(kernel_size=2, stride=2, return_indices=True)
        self.maxpool2 = nn.MaxPool3d(kernel_size=2, stride=2, return_indices=True)
        self.maxpool3 = nn.MaxPool3d(kernel_size=2, stride=2, return_indices=True)
        self.maxpool4 = nn.MaxPool3d(kernel_size=2, stride=2, return_indices=True)
        self.unmaxpool1 = nn.MaxUnpool3d(kernel_size=2, stride=2)
        self.unmaxpool2 = nn.MaxUnpool3d(kernel_size=2, stride=2)

        self.path1 = nn.Sequential(
            nn.ConvTranspose3d(64, 64, kernel_size=2, stride=2),
            nn.BatchNorm3d(64),
            nn.ReLU(inplace=True))

        self.path2 = nn.Sequential(
            nn.ConvTranspose3d(64, 64, kernel_size=2, stride=2),
            nn.BatchNorm3d(64),
            nn.ReLU(inplace=True))

        self.drop = nn.Dropout3d(p=0.2, inplace=False)
        self.output = nn.Sequential(
                nn.Conv3d(self.featureNum_back[0], 64, kernel_size=1),
                nn.ReLU(),
                nn.Conv3d(64, 5 * len(config['anchors']), kernel_size=1))

    def forward(self, x, coord):
        out = self.preBlock(x)  # 16
        out_pool, indices0 = self.maxpool1(out)
        out1 = self.forw1(out_pool)  # 32
        out1_pool, indices1 = self.maxpool2(out1)
        out2 = self.forw2(out1_pool)  # 64
        out2_pool, indices2 = self.maxpool3(out2)
        out3 = self.forw3(out2_pool)  # 96
        out3_pool, indices3 = self.maxpool4(out3)
        out4 = self.forw4(out3_pool)  # 96

        rev3 = self.path1(out4)
        comb3 = self.back3(torch.cat((rev3, out3), 1))  # 96 + 96
        rev2 = self.path2(comb3)

        feat = self.back2(torch.cat((rev2, out2, coord), 1))  # 64 + 64
        comb2 = self.drop(feat)
        out5 = self.output(comb2)
        size = out5.size()
        out6 = out5.view(out5.size(0), out5.size(1), -1)
        contig = out6.transpose(1, 2).contiguous()
        num_anchors = len(config['anchors'])
        contig.view(size[0], size[2], size[3], size[4], num_anchors, 5)
        return out


def get_model():
    net = Net()
    loss = Loss(config['num_hard'])
    get_pbb = GetPBB(config)
    return config, net, loss, get_pbb
