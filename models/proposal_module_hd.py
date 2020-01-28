# Copyright (c) Facebook, Inc. and its affiliates.
# 
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
import os
import sys
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
sys.path.append(os.path.join(ROOT_DIR, 'pointnet2'))
from pointnet2_modules import PointnetSAModuleVotes
from pointnet2_modules import PointnetSAModulePairwise
import pointnet2_utils

UPPER_THRESH = 10.0

def decode_scores(net, end_points, num_class, num_heading_bin, num_size_cluster, mean_size_arr, mode=''):
    net_transposed = net.transpose(2,1) # (batch_size, 1024, ..)
    batch_size = net_transposed.shape[0]
    num_proposal = net_transposed.shape[1]

    #if mode == 'opt':
    #    start = 0
    #else:
    start = 2
    objectness_scores = net_transposed[:,:,0:2]
    end_points['objectness_scores'+mode] = objectness_scores
    
    base_xyz = end_points['aggregated_vote_xyz'+mode] # (batch_size, num_proposal, 3)
    end_points['centerres'+mode] = net_transposed[:,:,start:start+3]
    center = base_xyz + net_transposed[:,:,start:start+3] # (batch_size, num_proposal, 3)
    end_points['center'+mode] = center

    heading_scores = net_transposed[:,:,start+3:start+3+num_heading_bin]
    heading_residuals_normalized = net_transposed[:,:,start+3+num_heading_bin:start+3+num_heading_bin*2]
    end_points['heading_scores'+mode] = heading_scores # Bxnum_proposalxnum_heading_bin
    end_points['heading_residuals_normalized'+mode] = heading_residuals_normalized # Bxnum_proposalxnum_heading_bin (should be -1 to 1)
    end_points['heading_residuals'+mode] = heading_residuals_normalized * (np.pi/num_heading_bin) # Bxnum_proposalxnum_heading_bin

    if False:#mode == 'corner':
        import pdb;pdb.set_trace()
        size_scores = net_transposed[:,:,start+3+num_heading_bin*2:start+3+num_heading_bin*2+num_size_cluster]
        size_residuals_normalized = net_transposed[:,:,start+3+num_heading_bin*2+num_size_cluster:start+3+num_heading_bin*2+num_size_cluster*4].view([batch_size, num_proposal, num_size_cluster, 3]) # Bxnum_proposalxnum_size_clusterx3
        end_points['size_scores'+mode] = size_scores
        end_points['size_residuals_normalized'+mode] = size_residuals_normalized
        end_points['size_residuals'+mode] = size_residuals_normalized * torch.from_numpy(mean_size_arr.astype(np.float32)).cuda().unsqueeze(0).unsqueeze(0)
    else:
        size_scores = net_transposed[:,:,start+3+num_heading_bin*2:start+3+num_heading_bin*2+num_size_cluster]
        size_residuals_normalized = net_transposed[:,:,start+3+num_heading_bin*2+num_size_cluster:start+3+num_heading_bin*2+num_size_cluster*4].view([batch_size, num_proposal, num_size_cluster, 3]) # Bxnum_proposalxnum_size_clusterx3
        end_points['size_scores'+mode] = size_scores
        end_points['size_residuals_normalized'+mode] = size_residuals_normalized
        end_points['size_residuals'+mode] = size_residuals_normalized * torch.from_numpy(mean_size_arr.astype(np.float32)).cuda().unsqueeze(0).unsqueeze(0)
        
    sem_cls_scores = net_transposed[:,:,start+3+num_heading_bin*2+num_size_cluster*4:] # Bxnum_proposalx10
    end_points['sem_cls_scores'+mode] = sem_cls_scores
    return end_points


class ProposalModule(nn.Module):
    def __init__(self, num_class, num_heading_bin, num_size_cluster, mean_size_arr, num_proposal, sampling, seed_feat_dim=128):
        super().__init__() 

        self.num_class = num_class
        self.num_heading_bin = num_heading_bin
        self.num_size_cluster = num_size_cluster
        self.mean_size_arr = mean_size_arr
        self.num_proposal = num_proposal
        self.num_proposal_comb = num_proposal
        self.sampling = sampling
        self.seed_feat_dim = seed_feat_dim
        self.vote_aggregation_corner = []
        self.vote_aggregation_plane = []

        # Vote clustering
        self.vote_aggregation = PointnetSAModuleVotes( 
                npoint=self.num_proposal,
                radius=0.3,
                nsample=16,
                mlp=[self.seed_feat_dim+3, 128, 128, 128],
                use_xyz=True,
                normalize_xyz=True
            )
        '''
        self.vote_aggregation_opt = PointnetSAModuleVotes( 
                npoint=self.num_proposal,
                radius=0.6,
                nsample=32,
                mlp=[self.seed_feat_dim+2+3+num_heading_bin*2+num_size_cluster*4+self.num_class, 128, 128, 128],
                use_xyz=True,
                normalize_xyz=True
            )
        '''
        #for _ in range(2):
        self.vote_aggregation_corner = PointnetSAModuleVotes( 
                npoint=self.num_proposal,
                radius=0.3,
                nsample=16,
                mlp=[self.seed_feat_dim+3+3, 128, 128, 128],
                use_xyz=True,
                normalize_xyz=True
            )

        self.vote_aggregation_corner_scale = PointnetSAModuleVotes( 
                npoint=self.num_proposal,
                radius=0.3,
                nsample=16,
                mlp=[self.seed_feat_dim+3, 128, 128, 128],
                use_xyz=True,
                normalize_xyz=True
            )

        #for _ in range(6):
        self.vote_aggregation_plane = PointnetSAModuleVotes( 
                npoint=self.num_proposal,
                radius=0.3,
                nsample=16,
                mlp=[self.seed_feat_dim+3, 128, 128, 128],
                use_xyz=True,
                normalize_xyz=True
            )

        self.vote_aggregation_comb = PointnetSAModuleVotes( 
                npoint=self.num_proposal_comb,
                radius=0.3,
                nsample=16,
                mlp=[(self.seed_feat_dim+3)*1, 128, 128, 128],
                use_xyz=True,
                normalize_xyz=True
            )

        '''
        self.vote_aggregation_refine1 = PointnetSAModuleVotes( 
                npoint=self.num_proposal_comb,
                radius=0.6,
                nsample=16,
                mlp=[2+3+self.num_class*5, 64, 64, 48],
                use_xyz=True,
                normalize_xyz=True,
                same_idx=True
            )

        self.vote_aggregation_refine2 = PointnetSAModuleVotes( 
                npoint=self.num_proposal_comb,
                radius=0.9,
                nsample=32,
                mlp=[2+3+self.num_class*5, 64, 64, 48],
                use_xyz=True,
                normalize_xyz=True,
                same_idx=True
            )

        self.vote_aggregation_refine3 = PointnetSAModuleVotes( 
                npoint=self.num_proposal_comb,
                radius=1.2,
                nsample=64,
                mlp=[2+3+self.num_class*5, 64, 64, 48],
                use_xyz=True,
                normalize_xyz=True,
                same_idx=True
            )
        '''
        '''
        ### For all 256
        self.vote_aggregation_refine = PointnetSAModulePairwise( 
                npoint=self.num_proposal_comb,
                radius=1.0,
                nsample=self.num_proposal_comb,
                mlp=[2+3+self.num_class*5, 128, 128, 64, 2],
                use_xyz=False,
                normalize_xyz=True,
                same_idx=True
            )
        '''
        '''
        self.vote_aggregation_refine = PointnetSAModulePairwise( 
                npoint=self.num_proposal_comb,
                radius=0.6,
                nsample=64,
                split=self.num_class,
                mlp=[2+3+self.num_class*5, 128, 128, 64, 2],
                use_xyz=False,
                normalize_xyz=False,
                same_idx=True
            )
        '''
        '''
        self.proposal_refine_corner = PointnetSAModuleVotes( 
                npoint=self.num_proposal,
                radius=0.5,
                nsample=16,
                mlp=[self.seed_feat_dim, 128, 128, 128],
                use_xyz=True,
                normalize_xyz=True,
                same_idx=True
            )

        self.proposal_refine_plane = PointnetSAModuleVotes( 
                npoint=self.num_proposal,
                radius=0.5,
                nsample=16,
                mlp=[self.seed_feat_dim, 128, 128, 128],
                use_xyz=True,
                normalize_xyz=True,
                same_idx=True
            )
        '''
        
        # Object proposal/detection
        # Objectness scores (2), center residual (3),
        # heading class+residual (num_heading_bin*2), size class+residual(num_size_cluster*4)
        self.conv1 = torch.nn.Conv1d(128,128,1)
        self.conv2 = torch.nn.Conv1d(128,128,1)
        self.conv3 = torch.nn.Conv1d(128,2+3+num_heading_bin*2+num_size_cluster*4+self.num_class,1)
        self.bn1 = torch.nn.BatchNorm1d(128)
        self.bn2 = torch.nn.BatchNorm1d(128)

        self.conv_corner1 = torch.nn.Conv1d(128,128,1)
        self.conv_corner2 = torch.nn.Conv1d(128,128,1)
        self.conv_corner3 = torch.nn.Conv1d(128,2+3+num_heading_bin*2+num_size_cluster*4+self.num_class,1)
        self.bn_corner1 = torch.nn.BatchNorm1d(128)
        self.bn_corner2 = torch.nn.BatchNorm1d(128)
        
        self.conv_corner_scale1 = torch.nn.Conv1d(128,128,1)
        self.conv_corner_scale2 = torch.nn.Conv1d(128,128,1)
        #self.conv_corner_scale3 = torch.nn.Conv1d(128,num_size_cluster*4,1)
        self.conv_corner_scale3 = torch.nn.Conv1d(128,2+3+num_heading_bin*2+num_size_cluster*4+self.num_class,1)
        self.bn_corner_scale1 = torch.nn.BatchNorm1d(128)
        self.bn_corner_scale2 = torch.nn.BatchNorm1d(128)
        '''
        self.conv_corner1 = torch.nn.Conv1d(128*2,128,1)
        self.conv_corner2 = torch.nn.Conv1d(128,128,1)
        self.conv_corner3 = torch.nn.Conv1d(128,2+3+num_heading_bin*2+self.num_class,1)
        self.bn_corner1 = torch.nn.BatchNorm1d(128)
        self.bn_corner2 = torch.nn.BatchNorm1d(128)
        '''
        self.conv_plane1 = torch.nn.Conv1d(128,128,1)
        self.conv_plane2 = torch.nn.Conv1d(128,128,1)
        self.conv_plane3 = torch.nn.Conv1d(128,2+3+num_heading_bin*2+num_size_cluster*4+self.num_class,1)
        self.bn_plane1 = torch.nn.BatchNorm1d(128)
        self.bn_plane2 = torch.nn.BatchNorm1d(128)

        self.conv_comb1 = torch.nn.Conv1d(128,128,1)
        self.conv_comb2 = torch.nn.Conv1d(128,128,1)
        self.conv_comb3 = torch.nn.Conv1d(128,2+3+num_heading_bin*2+num_size_cluster*4+self.num_class,1)
        self.bn_comb1 = torch.nn.BatchNorm1d(128)
        self.bn_comb2 = torch.nn.BatchNorm1d(128)

        self.conv_refine1 = torch.nn.Conv1d(128,128,1)
        self.conv_refine2 = torch.nn.Conv1d(128,128,1)
        #self.conv_refine3 = torch.nn.Conv1d(128,2+3+num_heading_bin*2+num_size_cluster*4+self.num_class,1)
        self.conv_refine3 = torch.nn.Conv1d(128,2,1)
        self.bn_refine1 = torch.nn.BatchNorm1d(128)
        self.bn_refine2 = torch.nn.BatchNorm1d(128)

        #self.conv_global1 = torch.nn.Conv1d(256*2, 256, 1)
        #self.conv_global2 = torch.nn.Conv1d(256,128,1)
        #self.conv_refine3 = torch.nn.Conv1d(128,2+3+num_heading_bin*2+num_size_cluster*4+self.num_class,1)
        #self.conv_global3 = torch.nn.Conv1d(128,2,1)
        #self.bn_global1 = torch.nn.BatchNorm1d(256)
        #self.bn_global2 = torch.nn.BatchNorm1d(128)
        
    def forward(self, xyz, features, xyz_corner, features_corner, xyz_plane, features_plane, end_points, mode=''):
        """
        Args:
            xyz: (B,K,3)
            features: (B,C,K)
        Returns:
            scores: (B,num_proposal,2+3+NH*2+NS*4) 
        """
        """
        if mode != '':
            xyz, features, fps_inds = self.vote_aggregation(xyz, features, inds=end_points['aggregated_vote_inds'].detach().int())
            ##sample_inds = fps_inds
            #xyz_transpose = xyz.transpose(2,1).contiguous()
            #xyz = pointnet2_utils.gather_operation(xyz_transpose, end_points['aggregated_vote_inds'].detach().int())
            #xyz = xyz.transpose(2,1).contiguous()
            #features = pointnet2_utils.gather_operation(features, end_points['aggregated_vote_inds'].detach().int())
            end_points['aggregated_vote_xyz'+mode] = xyz
            end_points['aggregated_vote_inds'+mode] = end_points['aggregated_vote_inds'] # (batch_size, num_proposal,) # should be 0,1,2,...,num_proposal
                        
            # --------- PROPOSAL GENERATION ---------
            net = F.relu(self.bn1(self.conv1(features))) 
            net = F.relu(self.bn2(self.conv2(net))) 
            net = self.conv3(net) # (batch_size, 2+3+num_heading_bin*2+num_size_cluster*4, num_proposal)
            
            end_points = decode_scores(net, endpoints, self.num_class, self.num_heading_bin, self.num_size_cluster, self.mean_size_arr, mode=mode)
            return end_points
        """
        if self.sampling == 'vote_fps':
            '''
            # Farthest point sampling (FPS) on votes
            inds = pointnet2_utils.furthest_point_sample(xyz, self.num_proposal)
            sample_inds = inds
            
            sample_inds_comb = torch.cat((sample_inds, sample_inds+1024, sample_inds+1024*2, sample_inds+1024*3, sample_inds+1024*4, sample_inds+1024*5, sample_inds+1024*6, sample_inds+1024*7, sample_inds+1024*8), 1)
            xyz_comb = torch.cat((xyz, xyz_corner, xyz_plane), 1)
            center_sel = (torch.ones((features.shape[0], 1, features.shape[2])) * 0.1).cuda()
            corner_sel = (torch.ones((features_corner.shape[0], 1, features_corner.shape[2])) * 0.2).cuda()
            plane_sel = (torch.ones((features_plane.shape[0], 1, features_plane.shape[2])) * 0.3).cuda()

            num_point = features.shape[2]
            xyz_corner_center = torch.cat([(xyz_corner[:,:num_point,:] + xyz_corner[:,num_point:2*num_point,:]) / 2.0]*2, 1).transpose(2,1).contiguous()
            xyz_plane_center = torch.cat([torch.stack(((xyz_plane[:,4*num_point:5*num_point,0]+xyz_plane[:,5*num_point:,0]) / 2.0, (xyz_plane[:,2*num_point:3*num_point,1]+xyz_plane[:,3*num_point:4*num_point,1]) / 2.0, (xyz_plane[:,:num_point,2]+xyz_plane[:,num_point:2*num_point,2]) / 2.0), -1)]*6, 1).transpose(2,1).contiguous()
            
            features_comb = torch.cat((torch.cat((center_sel, xyz.transpose(2,1).contiguous(), features), 1), torch.cat((corner_sel, xyz_corner_center, features_corner), 1), torch.cat((plane_sel, xyz_plane_center, features_plane), 1)), 2)
            
            xyz_comb, features_comb, fps_inds_comb = self.vote_aggregation_comb(xyz_comb, features_comb, inds=sample_inds_comb)
            xyz = xyz_comb[:,:self.num_proposal,:]
            features = features_comb[:,:,:self.num_proposal]
            
            #sample_inds_corner = fps_inds_corner
            features_corner = torch.cat((features_comb[:,:,self.num_proposal:2*self.num_proposal], features_comb[:,:,2*self.num_proposal:3*self.num_proposal]), 1)
            xyz_corner = (xyz_comb[:,self.num_proposal:2*self.num_proposal,:] + xyz_comb[:,2*self.num_proposal:3*self.num_proposal,:]) / 2.0

            features_plane = torch.cat((features_comb[:,:,3*self.num_proposal:4*self.num_proposal], features_comb[:,:,4*self.num_proposal:5*self.num_proposal], features_comb[:,:,5*self.num_proposal:6*self.num_proposal], features_comb[:,:,6*self.num_proposal:7*self.num_proposal], features_comb[:,:,7*self.num_proposal:8*self.num_proposal], features_comb[:,:,8*self.num_proposal:]), 1)
            xyz_plane = xyz_comb[:,3*self.num_proposal:,:]
            xyz_plane = torch.stack(((xyz_plane[:,4*self.num_proposal:5*self.num_proposal,0]+xyz_plane[:,5*self.num_proposal:,0]) / 2.0, (xyz_plane[:,2*self.num_proposal:3*self.num_proposal,1]+xyz_plane[:,3*self.num_proposal:4*self.num_proposal,1]) / 2.0, (xyz_plane[:,:self.num_proposal,2]+xyz_plane[:,self.num_proposal:2*self.num_proposal,2]) / 2.0), -1)
            '''
            # Farthest point sampling (FPS) on votes
            batch_size = features.shape[0]
            num_point = features.shape[2]

            #sample_inds_corner = torch.cat((sample_inds, sample_inds+num_point), 1)
            voted_xyz_scale = xyz_corner[:,num_point:,:] - xyz_corner[:,:num_point,:]
            voted_xyz_center = (xyz_corner[:,num_point:,:] + xyz_corner[:,:num_point,:]) / 2.0
            voted_xyz_corner1_feature = torch.cat((voted_xyz_center.transpose(2,1).contiguous(), voted_xyz_scale.transpose(2,1).contiguous(), features_corner[:,:,:num_point]), 1)
            voted_xyz_corner2_feature = torch.cat((voted_xyz_center.transpose(2,1).contiguous(), voted_xyz_scale.transpose(2,1).contiguous(), features_corner[:,:,num_point:]), 1)
            #voted_xyz_corner_feature = torch.cat(((voted_xyz_center - xyz).transpose(2,1).contiguous(), voted_xyz_scale.transpose(2,1).contiguous(), features_corner[:,:,:num_point], features_corner[:,:,num_point:]), 1)
            #voted_xyz_corner_feature = torch.cat((voted_xyz_scale.transpose(2,1).contiguous(), features_corner[:,:,:num_point], features_corner[:,:,num_point:]), 1)
            voted_xyz_corner_feature = torch.cat((voted_xyz_scale.transpose(2,1).contiguous(), (features_corner[:,:,:num_point]+features_corner[:,:,num_point:]) / 2.0), 1)
            
            #features_center = features
            #features_center = torch.cat((features, voted_xyz_corner_feature), 1)
            #features_center = torch.cat(((xyz - voted_xyz_center).transpose(2,1).contiguous(), features), 1)
            #voted_xyz_corner_feature = torch.cat((features, voted_xyz_corner_feature), 1)

            features_center = torch.cat((voted_xyz_scale.transpose(2,1).contiguous(), features), 1)
            features_plane = torch.cat((voted_xyz_scale.transpose(2,1).contiguous(), features_plane), 1)
            #features_combine = torch.cat((features_center, voted_xyz_corner_feature, features_plane), -1)
            #xyz_combine = torch.cat((xyz, voted_xyz_center, xyz_plane), 1)

            #xyz_comb, features_comb, _ = self.vote_aggregation_comb(xyz_combine, features_combine)

            dist1 = torch.sum((xyz - voted_xyz_center)**2, dim=-1)
            dist2 = torch.sum((xyz - xyz_plane)**2, dim=-1)
            dist3 = torch.sum((voted_xyz_center - xyz_plane)**2, dim=-1)
            #mask = (dist1 < 0.6) & (dist2 < 0.6) & (dist3 < 0.6)
            mask = (dist1 < 0.5) & (dist2 < 0.5) & (dist3 < 0.5)
            #mask = (dist1 < 0.3) & (dist2 < 0.3) & (dist3 < 0.3)

            xyz_agree = []
            features_agree = []
            xyz_agree = torch.zeros((batch_size, num_point*3, 3)).cuda()
            features_agree = torch.zeros((batch_size, (features_center.shape[1]), num_point*3)).cuda()
            #features_agree = torch.zeros((batch_size, (features_center.shape[1])*3, num_point*3)).cuda()
            inds_agree = []
            for i in range(batch_size):
                inds = torch.nonzero(mask[i,:]).transpose(1,0).contiguous().int()
                xyz_center_a = pointnet2_utils.gather_operation(
                    xyz[i,...].unsqueeze(0).transpose(1, 2).contiguous(),
                    inds
                ).transpose(1, 2).contiguous()
                xyz_corner_a = pointnet2_utils.gather_operation(
                    voted_xyz_center[i,...].unsqueeze(0).transpose(1, 2).contiguous(),
                    inds
                ).transpose(1, 2).contiguous()
                xyz_plane_a = pointnet2_utils.gather_operation(
                    xyz_plane[i,...].unsqueeze(0).transpose(1, 2).contiguous(),
                    inds
                ).transpose(1, 2).contiguous()
                features_center_a = pointnet2_utils.gather_operation(
                    features_center[i,...].unsqueeze(0),
                    inds
                )
                features_corner_a = pointnet2_utils.gather_operation(
                    voted_xyz_corner_feature[i,...].unsqueeze(0),
                    inds
                )
                features_plane_a = pointnet2_utils.gather_operation(
                    features_plane[i,...].unsqueeze(0),
                    inds
                )

                ### Concate here
                xyz_all = torch.cat((xyz_center_a, xyz_corner_a, xyz_plane_a), 1)
                features_all = torch.cat((features_center_a, features_corner_a, features_plane_a), -1)

                ### Cluster here
                #xyz_all = (xyz_center_a + xyz_corner_a + xyz_plane_a) / 3.0
                #features_all = torch.cat((features_center_a, features_corner_a, features_plane_a), 1)
                
                #xyz_flipped = xyz_all.transpose(1, 2).contiguous()
                inds_proposal = pointnet2_utils.furthest_point_sample(xyz_all, self.num_proposal_comb)

                xyz_agree[i,:xyz_all.shape[1],:] += xyz_all.squeeze(0)
                xyz_agree[i,xyz_all.shape[1]:,:] += UPPER_THRESH
                features_agree[i,:,:features_all.shape[2]] += features_all.squeeze(0)

                inds_agree.append(inds_proposal)
                #new_xyz = pointnet2_utils.gather_operation(
                #    xyz_flipped,
                #    inds_proposal
                #).transpose(1, 2).contiguous()
                #new_features = pointnet2_utils.gather_operation(
                #    features_all,
                #    inds_proposal
                #)
                #xyz_agree.append(new_xyz)
                #features_agree.append(new_features)
            #xyz_agree = torch.cat(xyz_agree, 0)
            #features_agree = torch.cat(features_agree, 0)
            #xyz_sub
            inds_agree = torch.cat(inds_agree, 0)
            
            xyz, features, fps_inds = self.vote_aggregation(xyz, features_center)
            sample_inds = fps_inds
            #voted_xyz_corner_feature = torch.cat((features_corner[:,:,:num_point], features_corner[:,:,num_point:]), 1)
            #features_corner = torch.cat((voted_xyz_corner1_feature, voted_xyz_corner2_feature), 2)
            #xyz_corner, features_corner, fps_inds_corner = self.vote_aggregation_corner(xyz_corner, features_corner, inds=sample_inds_corner)

            #xyz_corner1, features_corner1, _ = self.vote_aggregation_corner(xyz_corner[:,num_point:,:].contiguous(), voted_xyz_corner1_feature, inds=sample_inds)

            #sample_inds_corner = fps_inds_corner
            #features_corner = torch.cat((features_corner[:,:,:self.num_proposal], features_corner[:,:,self.num_proposal:]), 1)
            #xyz_corner_center = (xyz_corner[:,:self.num_proposal,:] + xyz_corner[:,self.num_proposal:,:]) / 2.0
            #xyz_corner_scale = (xyz_corner[:,self.num_proposal:,:] - xyz_corner[:,:self.num_proposal,:])
            #xyz_corner_center, features_corner_scale, _ = self.vote_aggregation_corner_scale(voted_xyz_center, voted_xyz_corner_feature, inds=sample_inds)
            xyz_corner_center, features_corner_scale, _ = self.vote_aggregation_corner_scale(voted_xyz_center, voted_xyz_corner_feature)

            #xyz_corner2, features_corner2, _ = self.vote_aggregation_corner_scale(xyz_corner[:,:num_point,:].contiguous(), voted_xyz_corner2_feature, inds=sample_inds)

            #sample_inds_corner = fps_inds_corner
            #xyz_corner_center = (xyz_corner1 + xyz_corner2) / 2.0
            #features_corner_scale = torch.cat((features_corner1, features_corner2), 1)
            
            sample_inds_plane = sample_inds#torch.cat((sample_inds, sample_inds+num_point, sample_inds+num_point*2, sample_inds+num_point*3, sample_inds+num_point*4, sample_inds+num_point*5), 1)
            #xyz_plane, features_plane, fps_inds_plane = self.vote_aggregation_plane(xyz_plane, features_plane, inds=sample_inds_plane)
            xyz_plane, features_plane, fps_inds_plane = self.vote_aggregation_plane(xyz_plane, features_plane)
            #features_plane = torch.cat((features_plane[:,:,:self.num_proposal], features_plane[:,:,self.num_proposal:2*self.num_proposal], features_plane[:,:,2*self.num_proposal:3*self.num_proposal], features_plane[:,:,3*self.num_proposal:4*self.num_proposal], features_plane[:,:,4*self.num_proposal:5*self.num_proposal], features_plane[:,:,5*self.num_proposal:]), 1)
            #xyz_plane_center = torch.stack(((xyz_plane[:,4*self.num_proposal:5*self.num_proposal,0]+xyz_plane[:,5*self.num_proposal:,0]) / 2.0, (xyz_plane[:,2*self.num_proposal:3*self.num_proposal,1]+xyz_plane[:,3*self.num_proposal:4*self.num_proposal,1]) / 2.0, (xyz_plane[:,:self.num_proposal,2]+xyz_plane[:,self.num_proposal:2*self.num_proposal,2]) / 2.0), -1)
            xyz_plane_center = xyz_plane
            #sample_inds_plane = fps_inds_plane
            #features_combine = torch.cat((features, features_corner_scale, features_plane), -1)
            #xyz_combine = torch.cat((xyz, xyz_corner_center, xyz_plane), 1)
            #xyz_comb, features_comb, _ = self.vote_aggregation_fine(xyz_combine, features_combine)
            xyz_comb, features_comb, _ = self.vote_aggregation_comb(xyz_agree, features_agree, inds=inds_agree)
            #xyz_comb, features_comb, _ = self.vote_aggregation_comb(xyz, features_center)

            #features = torch.cat((features, features_corner, features_plane), 1)
        elif self.sampling == 'seed_fps': 
            # FPS on seed and choose the votes corresponding to the seeds
            # This gets us a slightly better coverage of *object* votes than vote_fps (which tends to get more cluster votes)
            sample_inds = pointnet2_utils.furthest_point_sample(end_points['seed_xyz'], self.num_proposal)
            xyz, features, _ = self.vote_aggregation(xyz, features, sample_inds)
        elif self.sampling == 'random':
            # Random sampling from the votes
            num_seed = end_points['seed_xyz'].shape[1]
            sample_inds = torch.randint(0, num_seed, (batch_size, self.num_proposal), dtype=torch.int).cuda()
            xyz, features, _ = self.vote_aggregation(xyz, features, sample_inds)
        else:
            log_string('Unknown sampling strategy: %s. Exiting!'%(self.sampling))
            exit()
        end_points['aggregated_vote_xyzcenter'] = xyz # (batch_size, num_proposal, 3)
        end_points['aggregated_vote_inds'] = sample_inds # (batch_size, num_proposal,) # should be 0,1,2,...,num_proposal

        end_points['aggregated_vote_xyzcorner'] = xyz_corner_center # (batch_size, num_proposal, 3)
        #end_points['aggregated_vote_xyzcornersize'] = xyz_corner_scale # (batch_size, num_proposal, 3)
        #end_points['aggregated_vote_inds'] = sample_inds # (batch_size, num_proposal,) # should be 0,1,2,...,num_proposal

        end_points['aggregated_vote_xyzplane'] = xyz_plane_center # (batch_size, num_proposal, 3)
        #end_points['aggregated_vote_inds'] = sample_inds # (batch_size, num_proposal,) # should be 0,1,2,...,num_proposal

        end_points['aggregated_vote_xyzcomb'] = xyz_comb # (batch_size, num_proposal, 3)
        # --------- PROPOSAL GENERATION ---------
        net = F.relu(self.bn1(self.conv1(features))) 
        net = F.relu(self.bn2(self.conv2(net))) 
        net = self.conv3(net) # (batch_size, 2+3+num_heading_bin*2+num_size_cluster*4, num_proposal)

        end_points = decode_scores(net, end_points, self.num_class, self.num_heading_bin, self.num_size_cluster, self.mean_size_arr, mode='center')

        #net_corner = F.relu(self.bn_corner1(self.conv_corner1(features_corner))) 
        #net_corner = F.relu(self.bn_corner2(self.conv_corner2(net_corner))) 
        #net_corner = self.conv_corner3(net_corner) # (batch_size, 2+3+num_heading_bin*2+num_size_cluster*4, num_proposal)
        
        net_corner_scale = F.relu(self.bn_corner_scale1(self.conv_corner_scale1(features_corner_scale))) 
        net_corner_scale = F.relu(self.bn_corner_scale2(self.conv_corner_scale2(net_corner_scale))) 
        net_corner_scale = self.conv_corner_scale3(net_corner_scale) # (batch_size, 2+3+num_heading_bin*2+num_size_cluster*4, num_proposal)
        net_corner = net_corner_scale#torch.cat((net_corner[:,:2+3+self.num_heading_bin*2,:], net_corner_scale, net_corner[:,2+3+self.num_heading_bin*2:,:]), 1)
        #net_corner = torch.cat((net_corner, net_corner_scale), 1)
        end_points = decode_scores(net_corner, end_points, self.num_class, self.num_heading_bin, self.num_size_cluster, self.mean_size_arr, mode='corner')

        net_plane = F.relu(self.bn_plane1(self.conv_plane1(features_plane))) 
        net_plane = F.relu(self.bn_plane2(self.conv_plane2(net_plane))) 
        net_plane = self.conv_plane3(net_plane) # (batch_size, 2+3+num_heading_bin*2+num_size_cluster*4, num_proposal)

        end_points = decode_scores(net_plane, end_points, self.num_class, self.num_heading_bin, self.num_size_cluster, self.mean_size_arr, mode='plane')

        net_comb = F.relu(self.bn_comb1(self.conv_comb1(features_comb))) 
        net_comb = F.relu(self.bn_comb2(self.conv_comb2(net_comb))) 
        net_comb = self.conv_comb3(net_comb) # (batch_size, 2+3+num_heading_bin*2+num_size_cluster*4, num_proposal)

        end_points = decode_scores(net_comb, end_points, self.num_class, self.num_heading_bin, self.num_size_cluster, self.mean_size_arr, mode='comb')
        
        newxyz = xyz_comb + end_points['centerres'+'comb']
        #import pdb;pdb.set_trace()
        #newfeatures = torch.cat((end_points['objectness_scores'+'comb'].transpose(2,1).contiguous(), newxyz.transpose(2,1).contiguous(), end_points['sem_cls_scores'+'comb'].transpose(2,1).contiguous(), features_comb), 1)
        size_feature = torch.cat((end_points['size_scores'+'comb'].unsqueeze(-1), end_points['size_residuals'+'comb']), -1)
        #newfeatures = torch.cat((end_points['objectness_scores'+'comb'].transpose(2,1).contiguous(), newxyz.transpose(2,1).contiguous(), end_points['sem_cls_scores'+'comb'].transpose(2,1).contiguous(), size_feature.view(batch_size, self.num_proposal, self.num_class*4).transpose(2,1).contiguous(), features_comb), 1)
        newfeatures = torch.cat((end_points['objectness_scores'+'comb'].transpose(2,1).contiguous(), newxyz.transpose(2,1).contiguous(), end_points['sem_cls_scores'+'comb'].transpose(2,1).contiguous(), size_feature.view(batch_size, self.num_proposal_comb, self.num_class*4).transpose(2,1).contiguous()), 1)

        #xyz_refine1, features_refine1, _ = self.vote_aggregation_refine1(newxyz, newfeatures)
        #xyz_refine2, features_refine2, _ = self.vote_aggregation_refine2(newxyz, newfeatures)
        #xyz_refine3, features_refine3, _ = self.vote_aggregation_refine3(newxyz, newfeatures)

        #newfeatures = torch.cat((newfeatures, features_refine1, features_refine2, features_refine3), 1)

        '''
        xyz_refine, features_refine, sample_idx, grouped_features = self.vote_aggregation_refine(newxyz, newfeatures)
        #xyz_refine, features_refine, grouped_features = self.vote_aggregation_refine(newxyz, newfeatures)

        _,sem_sel = torch.max(grouped_features[:,2+3:2+3+self.num_class,:,:], 1)
        _,size_sel = torch.max(grouped_features[:,2+3+self.num_class:2+3+self.num_class*2,:,:], 1)

        #final_sel = size_sel
        final_sel = sem_sel
        features_refine = torch.gather(features_refine, 1, final_sel.unsqueeze(1).unsqueeze(1).repeat(1,1,2,1,1)).squeeze(1)

        end_points['aggregated_vote_xyzrefine'] = xyz_refine# (batch_size, num_proposal, 3)
        end_points['aggregated_vote_mrf'] = grouped_features#[:,:5,:,:]# (batch_size, num_proposal, 3)
        end_points['aggregated_vote_sem'] = grouped_features[:,5:5+self.num_class,:,:]#[:,:5,:,:]# (batch_size, num_proposal, 3)
        end_points['objectness_scores'+'refine'] = features_refine.view(batch_size, 2,-1).transpose(2,1).contiguous()
        #_, inds_obj = torch.max(features_refine[:,1,:,:], -1)
        end_points['temp'+'refine'] = features_refine
        end_points['temp'+'idx'] = sample_idx
        _, inds_obj = torch.topk(features_refine[:,1,:,:], k=5, dim=-1)
        #end_points['objectness_scores'+'mrf'] = torch.gather(features_refine, -1, inds_obj.unsqueeze(-1).repeat(1,1,2).transpose(2,1).unsqueeze(-1)).squeeze(-1).transpose(2,1).contiguous()
        end_points['objectness_scores'+'mrf'] = torch.mean(torch.gather(features_refine, -1, inds_obj.unsqueeze(1).repeat(1,2,1,1)), dim=-1).transpose(2,1).contiguous()
        '''
        #end_points['objectness_scores'+'mrf'] = torch.mean(features_refine, dim=-1).transpose(2,1).contiguous()

        #net_global = F.relu(self.bn_global1(self.conv_global1(features_refine.transpose(3,2).view(batch_size, self.num_proposal*2, self.num_proposal).contiguous())))
        #net_global = F.relu(self.bn_global2(self.conv_global2(net_global))) 
        #net_global = self.conv_global3(net_global) # (batch_size, 2+3+num_heading_bin*2+num_size_cluster*4, num_proposal)
        #end_points['objectness_scores'+'global'] = net_global.transpose(2,1).contiguous()
        #net_refine = F.relu(self.bn_refine1(self.conv_refine1(features_refine))) 
        #net_refine = F.relu(self.bn_refine2(self.conv_refine2(net_refine))) 
        #net_refine = self.conv_refine3(net_refine) # (batch_size, 2+3+num_heading_bin*2+num_size_cluster*4, num_proposal)

        #end_points = decode_scores(net_refine, end_points, self.num_class, self.num_heading_bin, self.num_size_cluster, self.mean_size_arr, mode='refine')
        
        '''
        comb_xyz = torch.cat((xyz, xyz_corner, xyz_plane), 1)
        features_center = pointnet2_utils.gather_operation(features_center, sample_inds).contiguous()
        comb_xyz_center_feature = torch.cat((net, features_center), 1)
        comb_xyz_corner_feature = torch.cat((torch.zeros((features_corner.shape[0], net.shape[1], features_corner.shape[2])).cuda(), features_corner), 1)
        comb_xyz_plane_feature = torch.cat((torch.zeros((features_plane.shape[0], net.shape[1], features_plane.shape[2])).cuda(), features_plane), 1)
        comb_xyz_feature = torch.cat((comb_xyz_center_feature, comb_xyz_corner_feature, comb_xyz_plane_feature), -1)
        '''
        #comb_xyz_plane_feature = torch.cat((net, features_plane), 1)
        #comb_xyz_corner_plane = torch.cat((xyz, xyz_plane), 1)
        #comb_xyz_corner_plane_feature = torch.cat((net, features_plane), -1)
        
        #check_xyz, features_opt, _ = self.vote_aggregation_opt(comb_xyz, comb_xyz_feature, inds=torch.stack([np.arange(0,self.num_proposal)]*comb_xyz.shape[0], 0).cuda())
        #check_xyz, features_opt, _ = self.vote_aggregation_opt(comb_xyz, comb_xyz_feature, inds=torch.stack([torch.tensor(np.arange(0,self.num_proposal)).int().cuda()]*comb_xyz.shape[0], 0))
        #xyz_plane, features_plane, _ = self.vote_aggregation_plane(comb_xyz_corner_plane, comb_xyz_corner_plane_feature, inds=sample_inds)
        #net_corner = F.relu(self.bn_corner1(self.conv_corner1(features_opt))) 
        #net_corner = F.relu(self.bn_corner2(self.conv_corner2(net_corner))) 
        #net_corner = self.conv_corner3(net_corner) # (batch_size, 2+3+num_heading_bin*2+num_size_cluster*4, num_proposal)

        '''
        net_plane = F.relu(self.bn_plane1(self.conv_plane1(features_plane))) 
        net_plane = F.relu(self.bn_plane2(self.conv_plane2(net_plane))) 
        net_plane = self.conv_plane3(net_plane) # (batch_size, 2+3+num_heading_bin*2+num_size_cluster*4, num_proposal)
        '''
        #net = torch.cat((net[:,:2,:], net_corner), 1)
        #end_points = decode_scores(net, end_points, self.num_class, self.num_heading_bin, self.num_size_cluster, self.mean_size_arr, mode='opt')
        #end_points = decode_scores(net_plane, end_points, self.num_class, self.num_heading_bin, self.num_size_cluster, self.mean_size_arr, mode='plane')
        return end_points

if __name__=='__main__':
    sys.path.append(os.path.join(ROOT_DIR, 'sunrgbd'))
    from sunrgbd_detection_dataset import SunrgbdDetectionVotesDataset, DC
    net = ProposalModule(DC.num_class, DC.num_heading_bin,
        DC.num_size_cluster, DC.mean_size_arr,
        128, 'seed_fps').cuda()
    end_points = {'seed_xyz': torch.rand(8,1024,3).cuda()}
    out = net(torch.rand(8,1024,3).cuda(), torch.rand(8,256,1024).cuda(), end_points)
    for key in out:
        print(key, out[key].shape)
