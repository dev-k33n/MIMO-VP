# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/02_tcn.ipynb (unless otherwise specified).

__all__ = ['VGGBlock', 'VGG', 'Chomp1d', 'TemporalBlock3D', 'TemporalConvNet3D', 'TCN3D', 'SimpleTCN', 'SimpleTCN2']

# Cell
from torch.nn.utils import weight_norm
from fastai.vision.all import *
from .conv_rnn import *

# Cell
class VGGBlock(nn.Sequential):
    def __init__(self, ni, nf, ks=3, pool=True, conv_cls=nn.Conv2d, act=nn.ReLU(), xtra=None):
        padding = (ks-1)//2
        layers = [conv_cls(ni,nf,ks, padding=padding), nn.BatchNorm2d(nf), act]
        if pool: layers.append(nn.MaxPool2d(2,2))
        if xtra: layers.append(xtra)
        super().__init__(*layers)

# Cell
class VGG(Module):
    "VGG Net https://neurohive.io/en/popular-networks/vgg16/"
    def __init__(self, n_in=3, layers=[1,1,2], ks=3, conv_cls=nn.Conv2d, act=nn.ReLU(), ni=64, last_pool=False, self_attention=False):
        blocks = [VGGBlock(n_in, ni, ks=ks, pool=True,conv_cls=conv_cls, act=act)]
        for i, nl in enumerate(layers):
            nf = (2**(i+1)) * ni if i+1<len(layers) else (2**i) * ni  #last block case
            fltrs = [(2**i) * ni]*nl + [nf]
            pool = True if i+1 < len(layers) else last_pool
            blocks += [VGGBlock(ni,nf,ks=ks, pool=pool, conv_cls=conv_cls, act=act) for ni, nf in zip(fltrs[:-1], fltrs[1:])]
        if self_attention: blocks.append(SelfAttention(nf))
        self.image_encoder = nn.Sequential(*blocks)
    def forward(self, x):
        return self.image_encoder(x)

# Cell
class Chomp1d(Module):
    def __init__(self, chomp_size, tdim=2):
        self.chomp_size = chomp_size
        self.tdim = tdim

    def forward(self, x):
        if self.tdim==2:
            return x[:, :, :-self.chomp_size,...].contiguous()
        else: raise Exception('Please put time dim on second dimension')

    def __repr__(self):
        return f'Chomp({self.chomp_size})'

# Cell
class TemporalBlock3D(Module):
    "A resnet type temporal Block"
    def __init__(self, n_in, n_out, ks, stride=1, dilation=2, dropout=0.2):
        kernel_size = (ks, 3, 3)
        dilation=(dilation,1,1)
        padding=((ks-1)*dilation[0], ks//2, ks//2)
        stride=(stride, 1, 1)
        self.conv1 = weight_norm(nn.Conv3d(n_in, n_out, kernel_size,
                                           stride=stride, padding=padding, dilation=dilation))
        self.chomp1 = Chomp1d(padding[0])
        self.relu1 = nn.ReLU()
        self.dropout1 = nn.Dropout(dropout)

        self.conv2 = weight_norm(nn.Conv3d(n_out, n_out, kernel_size,
                                           stride=stride, padding=padding, dilation=dilation))
        self.chomp2 = Chomp1d(padding[0])
        self.relu2 = nn.ReLU()
        self.dropout2 = nn.Dropout(dropout)

        self.net = nn.Sequential(self.conv1, self.chomp1, self.relu1, self.dropout1,
                                 self.conv2, self.chomp2, self.relu2, self.dropout2)
        self.downsample = nn.Conv3d(n_in, n_out, 1) if n_in != n_out else None
        self.relu = nn.ReLU()
#         self.init_weights()

#     def init_weights(self):
#         self.conv1.weight.data.normal_(0, 0.01)
#         self.conv2.weight.data.normal_(0, 0.01)
#         if self.downsample is not None:
#             self.downsample.weight.data.normal_(0, 0.01)

    def forward(self, x):
        out = self.net(x)
        res = x if self.downsample is None else self.downsample(x)
        return self.relu(out + res)

# Cell
class TemporalConvNet3D(Module):
    def __init__(self, num_inputs, num_channels, ks=3, dropout=0.2):
        layers = []
        num_levels = len(num_channels)
        for i in range(num_levels):
            dilation_size = 2 ** i
            in_channels = num_inputs if i == 0 else num_channels[i-1]
            out_channels = num_channels[i]
            layers += [TemporalBlock3D(in_channels, out_channels, ks, stride=1, dilation=dilation,
                                       dropout=dropout)]

        self.network = nn.Sequential(*layers)

    def forward(self, x):
        return self.network(x)

# Cell
class TCN3D(Module):
    def __init__(self, input_size, num_channels, kernel_size=3, p=0.2):
        self.tcn = TemporalConvNet3D(input_size, num_channels, kernel_size, dropout=p)
    def forward(self, x):
        "x needs to have dimension (N, C, L, W, H) in order to be passed into CNN"
        output = self.tcn(x.transpose(1, 2)).transpose(1, 2)
        return output

# Cell
class SimpleTCN(Module):
    "Simple TCN model"
    def __init__(self, n_in=1, ks=3, n_out=1, norm=NormType.Batch, dilation=2, debug=False):
        #enc
        self.coord = TimeDistributed(CoordConv(n_in, 8, kernel_size=1), tdim=2)
        self.conv0 = TimeDistributed(ConvLayer(8, 16, stride=2, act_cls=nn.ReLU,
                                               norm_type=norm), tdim=2)
        self.tcn1  = TemporalBlock3D(16, 16, ks, dilation=dilation)
        self.conv1 = TimeDistributed(ConvLayer(16, 32, stride=2, act_cls=nn.ReLU, norm_type=norm), tdim=2)
        self.tcn2  = TemporalBlock3D(32, 32, ks, dilation=dilation)
        self.conv2 = TimeDistributed(ConvLayer(32, 64, stride=2, act_cls=nn.ReLU, norm_type=norm), tdim=2)
        self.tcn3  = TemporalBlock3D(64, 64, ks, dilation=dilation)

        #dec
        self.tcn1_u  = TemporalBlock3D(64, 64, ks, dilation=dilation)
        self.conv1_u = TimeDistributed(UpsampleBlock(64, 32, debug=debug, norm_type=norm), tdim=2)
        self.tcn2_u  = TemporalBlock3D(32, 32, ks, dilation=dilation)
        self.conv2_u = TimeDistributed(UpsampleBlock(32, 16, debug=debug, norm_type=norm), tdim=2)
        self.tcn3_u  = TemporalBlock3D(16, 16, ks, dilation=dilation)
        self.conv3_u = TimeDistributed(UpsampleBlock(16, n_out, residual=False, debug=debug, norm_type=norm), tdim=2)

    def forward(self, x):
        "x shape (bs, channels, time, h, w)"
        x = self.coord(x)
        #encoder
        x = self.conv0(x) #16
        x1 = self.tcn1(x)
        x = self.conv1(x)
        x2 = self.tcn2(x)  #32
        x = self.conv2(x)
        x = self.tcn3(x)  #64
#         print('encoded: ',x.shape)

        #decoder
        x = self.tcn1_u(x)
#         print('tcn1_u: ',x.shape, x2.shape)
        x = self.conv1_u(x, x2)
        x = self.tcn2_u(x)
#         print('tcn1_u: ',x.shape, x1.shape)
        x = self.conv2_u(x, x1)
        x = self.tcn3_u(x)
        x = self.conv3_u(x)
#         print('decoded: ',x.shape)
        return x


# Cell
class SimpleTCN2(Module):
    "Simple TCN model"
    def __init__(self, n_in=1, ks=3, n_out=1, norm=NormType.Batch, dilation=2, debug=False):
        #enc
        self.coord = TimeDistributed(CoordConv(n_in, 8, kernel_size=1), tdim=2)
        self.conv0 = TimeDistributed(ConvLayer(8, 16, stride=1, act_cls=nn.ReLU,
                                               norm_type=norm), tdim=2)
        self.tcn1  = TemporalBlock3D(16, 16, ks, dilation=dilation)
        self.conv1 = TimeDistributed(ConvLayer(16, 32, stride=1, act_cls=nn.ReLU, norm_type=norm), tdim=2)
        self.tcn2  = TemporalBlock3D(32, 32, ks, dilation=dilation)
        self.conv2 = TimeDistributed(ConvLayer(32, 64, stride=1, act_cls=nn.ReLU, norm_type=norm), tdim=2)
        self.tcn3  = TemporalBlock3D(64, 64, ks, dilation=dilation)

        #dec
        self.tcn1_u  = TemporalBlock3D(64, 64, ks, dilation=dilation)
        self.conv1_u = TimeDistributed(ConvLayer(64, 32, stride=1, act_cls=nn.ReLU, norm_type=norm), tdim=2)
        self.tcn2_u  = TemporalBlock3D(32, 32, ks, dilation=dilation)
        self.conv2_u = TimeDistributed(ConvLayer(32, 16, stride=1, act_cls=nn.ReLU, norm_type=norm), tdim=2)
        self.tcn3_u  = TemporalBlock3D(16, 16, ks, dilation=dilation)
        self.conv3_u = TimeDistributed(ConvLayer(16, n_out, stride=1, act_cls=nn.ReLU, norm_type=norm), tdim=2)

    def forward(self, x):
        "x shape (bs, channels, time, h, w)"
        x = self.coord(x)
        #encoder
        x = self.conv0(x) #16
        x1 = self.tcn1(x)
        x = self.conv1(x)
        x2 = self.tcn2(x)  #32
        x = self.conv2(x)
        x = self.tcn3(x)  #64
#         print('encoded: ',x.shape)

        #decoder
        x = self.tcn1_u(x)
#         print('tcn1_u: ',x.shape, x2.shape)
        x = self.conv1_u(x)
        x = self.tcn2_u(x)
#         print('tcn1_u: ',x.shape, x1.shape)
        x = self.conv2_u(x)
        x = self.tcn3_u(x)
        x = self.conv3_u(x)
#         print('decoded: ',x.shape)
        return x
