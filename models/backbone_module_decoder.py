# Copyright (c) Facebook, Inc. and its affiliates.
# 
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
sys.path.append(ROOT_DIR)
sys.path.append(os.path.join(ROOT_DIR, 'utils'))
sys.path.append(os.path.join(ROOT_DIR, 'pointnet2'))

from pointnet2_modules import PointnetSAModuleVotes, PointnetFPModule

class Pointnet2BackboneDecoder(nn.Module):
    r"""
       Backbone network for point cloud feature learning.
       Based on Pointnet++ single-scale grouping network. 
        
       Parameters
       ----------
       input_feature_dim: int
            Number of input channels in the feature descriptor for each point.
            e.g. 3 for RGB.
    """
    def __init__(self, input_feature_dim=0, task=""):
        super().__init__()
        
        #self.sa0 = PointnetSAModuleVotes(
        #        npoint=2048,
        #        radius=0.2,
        #        nsample=64,
        #        mlp=[input_feature_dim, 128, 128, 256],
        #        use_xyz=True,
        #        normalize_xyz=True
        #    )

        
        self.sa1 = PointnetSAModuleVotes(
                npoint=512,
                radius=0.4,
                nsample=32,
                mlp=[input_feature_dim, 128, 128, 256],
                use_xyz=True,
                normalize_xyz=True
            )

        self.sa2 = PointnetSAModuleVotes(
                npoint=256,
                radius=0.8,
                nsample=16,
                mlp=[256, 128, 128, 256],
                use_xyz=True,
                normalize_xyz=True
            )

        self.sa3 = PointnetSAModuleVotes(
                npoint=128,
                radius=1.2,
                nsample=16,
                mlp=[256, 128, 128, 256],
                use_xyz=True,
                normalize_xyz=True
            )

        self.fp1 = PointnetFPModule(mlp=[256+256,256,256])
        self.fp2 = PointnetFPModule(mlp=[256+256,256,256])
        self.fp3 = PointnetFPModule(mlp=[256+input_feature_dim,256,256])

        self.task = task

    def _break_up_pc(self, pc):
        xyz = pc[..., 0:3].contiguous()
        features = (
            pc[..., 3:].transpose(1, 2).contiguous()
            if pc.size(-1) > 3 else None
        )

        return xyz, features

    def forward(self, pointcloud: torch.cuda.FloatTensor, end_points=None, reuse=False):
        r"""
            Forward pass of the network

            Parameters
            ----------
            pointcloud: Variable(torch.cuda.FloatTensor)
                (B, N, 3 + input_feature_dim) tensor
                Point cloud to run predicts on
                Each point in the point-cloud MUST
                be formated as (x, y, z, features...)

            Returns
            ----------
            end_points: {XXX_xyz, XXX_features, XXX_inds}
                XXX_xyz: float32 Tensor of shape (B,K,3)
                XXX_features: float32 Tensor of shape (B,K,D)
                XXX-inds: int64 Tensor of shape (B,K) values in [0,N-1]
        """
        if not end_points: end_points = {}
        batch_size = pointcloud.shape[0]

        if reuse:
            self.task = 'reuse'
        else:
            self.task = 'decoder'
                        
        xyz, features = self._break_up_pc(pointcloud)
        
        #xyz, features, fps_inds = self.sa0(xyz, features)

        end_points[self.task+'sa0_xyz'] = xyz
        end_points[self.task+'sa0_features'] = features
        
        # --------- 4 SET ABSTRACTION LAYERS ---------
        xyz, features, fps_inds = self.sa1(xyz, features)
        end_points[self.task+'sa1_inds'] = fps_inds
        end_points[self.task+'sa1_xyz'] = xyz
        end_points[self.task+'sa1_features'] = features

        xyz, features, fps_inds = self.sa2(xyz, features) # this fps_inds is just 0,1,...,1023
        end_points[self.task+'sa2_inds'] = fps_inds
        end_points[self.task+'sa2_xyz'] = xyz
        end_points[self.task+'sa2_features'] = features

        xyz, features, fps_inds = self.sa3(xyz, features) # this fps_inds is just 0,1,...,511
        end_points[self.task+'sa3_xyz'] = xyz
        end_points[self.task+'sa3_features'] = features

        #xyz, features, fps_inds = self.sa4(xyz, features) # this fps_inds is just 0,1,...,255
        #end_points[self.task+'sa4_xyz'] = xyz
        #end_points[self.task+'sa4_features'] = features

        # --------- 2 FEATURE UPSAMPLING LAYERS --------
        features = self.fp1(end_points[self.task+'sa2_xyz'], end_points[self.task+'sa3_xyz'], end_points[self.task+'sa2_features'], end_points[self.task+'sa3_features'])
        features = self.fp2(end_points[self.task+'sa1_xyz'], end_points[self.task+'sa2_xyz'], end_points[self.task+'sa1_features'], features)
        features = self.fp3(end_points[self.task+'sa0_xyz'], end_points[self.task+'sa1_xyz'], end_points[self.task+'sa0_features'], features)
        #features = self.fp4(end_points[self.task+'sa0_xyz'], end_points[self.task+'sa1_xyz'], end_points[self.task+'sa0_features'], features)
        
        end_points[self.task+'fp3_features'] = features
        #import pdb;pdb.set_trace()
        #end_points[self.task+'xyz'] = end_points[self.task+'sa1_xyz']
        #num_seed = end_points['fp_xyz'].shape[1]
        #end_points['inds'] = end_points['sa1_inds']#[:,0:num_seed] # indices among the entire input point clouds
        #end_points['xyz'] = end_points['sa1_xyz']
        
        return end_points


if __name__=='__main__':
    backbone_net = Pointnet2Backbone(input_feature_dim=3).cuda()
    print(backbone_net)
    backbone_net.eval()
    out = backbone_net(torch.rand(4,8192,6).cuda())
    for key in sorted(out.keys()):
        print(key, '\t', out[key].shape)
