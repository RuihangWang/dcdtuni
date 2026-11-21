import argparse
import json

def str2bool(v):
    """
    'boolean type variable' for add_argument
    """
    if v.lower() in ('yes','true','t','y','1'):
        return True
    elif v.lower() in ('no','false','f','n','0'):
        return False
    else:
        raise argparse.ArgumentTypeError('boolean value expected.')

def params(load=None):
    if load is not None:
        parser = argparse.ArgumentParser(description='train / test a pytorch model to predict frames')
        params = vars(parser.parse_args([]))
        with open(load+'/commandline_args.json', 'rt') as f:
            params.update(json.load(f))
        for k, v in params.items():
            parser.add_argument('--' + k, default=v)
        args = parser.parse_args([])
        return  args
    else:
        """
        return parameters for training / testing / plotting of NNmodels
        :return: parameter-Namespace
        """
        parser = argparse.ArgumentParser(description='train / test a pytorch model to predict frames')

        # Training parameters
        parser.add_argument('--net', default="TransFVGN_v2", type=str, help='network to train (default: FVGN)', choices=["FVGN", "TransFVGN_v2"])
        parser.add_argument('--n_epochs', default=210000, type=int, help='number of epochs (after each epoch, the model gets saved)')
        parser.add_argument('--batch_size', default=1, type=int, help='batch size (default: 100)')
        parser.add_argument('--average_sequence_length', default=5, type=int, help='average sequence length in dataset=dataset_size/batch_size(default: 5000)')
        parser.add_argument('--dataset_size', default=5, type=int, help='size of dataset (default: 1000)')
        parser.add_argument('--lr', default=5e-5, type=float, help='learning rate of optimizer (default: 0.0001)')
        parser.add_argument('--on_gpu', default=0, type=int, help='set training on which gpu')

        # train strategy parameters
        parser.add_argument('--integrator', default="imex", type=str, help='integration scheme (implicit / imex) (default: imex)',choices=['implicit','imex'])
        parser.add_argument('--conserved_form', default=True, type=str2bool, help='Use artifacial wall method, which is setting boudary node to zero which it was flow into the domain (default:False)')
        parser.add_argument('--max_inner_steps', default=5, type=int, help='unsteady time stepping convergence max iteration steps')
        parser.add_argument('--order', default="1st", type=str, help='order of WLSQ', choices=["1st","2nd","3rd", "4th", "GG"])
        parser.add_argument('--cfc_smooth', default=False, type=str2bool, help='Whether use cfc_smooth to smooth the solution (default:False)')
        parser.add_argument('--sample_ratio', default=50, type=int, help='number of sample ratio for subgraph')
        parser.add_argument('--k_hop', default=1, type=int, help='number of k_hop for subgraph')

        # loss parameters
        parser.add_argument('--loss_cont', default=1e5, type=float, help='loss factor for continuity equation')
        parser.add_argument('--loss_mom', default=1e4, type=float, help='loss factor for uvw diffusion flux on face')
        parser.add_argument('--loss_phi_jump', default=1, type=float, help='loss factor for phi jump on face')
        parser.add_argument('--loss_outlet_bc', default=1e-1, type=float, help='loss factor for phi jump on face')
        
        # Load parameters
        parser.add_argument('--load_date_time', default=None, type=str, help='date_time of run to load (default: None)')
        parser.add_argument('--load_index', default=None, type=int, help='index of run to load (default: None)')
        parser.add_argument('--load_optimizer', default=False, type=str2bool, help='load state of optimizer (default: True)')

        #model parameters
        parser.add_argument('--hidden_size', default=128, type=int, help='hidden size of network (default: 128)')
        parser.add_argument('--message_passing_num', default=3, type=int, help='message passing layer number (default:3)')
        parser.add_argument('--attn_processor_num', default=2, type=int, help='AttnProcessor layer number (default:2)')
        parser.add_argument('--node_phi_size', default=4, type=int, help='node encoder for u,v,w,p (default: 4)')
        parser.add_argument('--node_input_size', default=14, type=int, help='[u,v,w,p,ThetaPDE] (default: 14)')

        #dataset params
        parser.add_argument('--dataset_dir', default='mesh_example/Cylinder_flow_3D_cyclic', type=str, help='path to dataset/case directory (expects an h5 folder inside)')

        params = parser.parse_args([])

        # 验证有效性

        
        return params

def get_hyperparam(params):
    return f"net {params.net}; hs {params.hidden_size};"
