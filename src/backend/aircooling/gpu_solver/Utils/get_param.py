import argparse
import json
import os
import random
import itertools
import numpy as np

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
        parser.add_argument('--net', default="TransFVGN_v2", type=str, help='network to train (default: TransFVGN_v2)', choices=["TransFVGN_v2", "GNN", "Transolver", "TransolverPlus"])
        parser.add_argument('--n_epochs', default=1000000, type=int, help='number of training iterations (manual early-stop)')
        parser.add_argument('--batch_size', default=1, type=int, help='batch size per GPU (default: 1)')
        parser.add_argument('--average_sequence_length', default=500, type=int, help='average time steps per case per reset cycle')
        parser.add_argument('--dataset_size', default=500, type=int, help='TOTAL number of cases in the training pool, shared across DDP ranks (each rank takes dataset_size // world_size); must be divisible by world_size')
        parser.add_argument('--lr', default=5e-5, type=float, help='learning rate of optimizer (default: 0.0001)')
        parser.add_argument('--on_gpu', default=0, type=int, help='set training on which VISIBLE gpu (select the physical device with CUDA_VISIBLE_DEVICES; this index is into the visible set)')

        # Optimizer parameters
        parser.add_argument('--optimizer_type', default='soap', type=str, help='optimizer type: adam, soap, adamw, sgd', choices=['adam', 'soap', 'adamw', 'sgd'])

        # train strategy parameters
        parser.add_argument('--integrator', default="imex", type=str, help='integration scheme (implicit / imex) (default: imex)',choices=['implicit','imex'])
        parser.add_argument('--conserved_form', default=True, type=str2bool, help='Assemble the FV residual in conserved (face-flux) form; the non-conserved form has been removed, so the solvers raise if this is False (default: True)')
        parser.add_argument('--max_inner_steps', default=5, type=int, help='unsteady time stepping convergence max iteration steps')
        parser.add_argument('--grad_accum_steps', default=1, type=int, help='micro-batches accumulated per optimizer step; effective batch = world_size * batch_size * grad_accum_steps')
        parser.add_argument('--order', default="1st", type=str, help='order of WLSQ', choices=["1st","2nd","3rd", "4th", "GG"])
        parser.add_argument('--cfc_smooth', default=False, type=str2bool, help='Whether use cfc_smooth to smooth the solution (default:False)')
        parser.add_argument('--sample_ratio', default=50, type=int, help='number of sample ratio for subgraph')
        parser.add_argument('--k_hop', default=1, type=int, help='number of k_hop for subgraph')
        parser.add_argument('--fvconfig', type=dict, default={}, help='fvconfig.toml dict; it is populated by the mesh pool in src/FVdomain/Graph_pool.py while loading the case')
        
        # loss parameters
        # NOTE: For FV cases these will be overwritten by fvconfig.fvSolution during mesh loading.
        parser.add_argument('--loss_cont', default=1e5, type=float, help='loss factor for continuity equation')
        parser.add_argument('--loss_mom', default=1e4, type=float, help='loss factor for the momentum equations')
        parser.add_argument('--loss_phi_jump', default=0, type=float, help='DEPRECATED: loss factor for phi jump on face; must stay 0 (it masks genuine numerical oscillation)')
        parser.add_argument('--loss_energy', default=1e4, type=float, help='loss factor for energy equation')
        parser.add_argument('--loss_pRef', default=1, type=float, help='loss factor for pressure reference constraint')
        
        # Load parameters
        parser.add_argument('--load_date_time', default=None, type=str, help='date_time of run to load (default: None)')
        parser.add_argument('--load_index', default=None, type=int, help='index of run to load (default: None)')


        # Mmap state management
        parser.add_argument('--resume_state', default=False, type=str2bool, help='Resume phi state from mmap/snapshot on disk (default: False)')
        parser.add_argument('--resume_mmap', default=None, type=str,
                            help='Path to old state_save_dir to resume mmap state from. '
                                 'Enforces same dataset_size and world_size. (default: None)')
        parser.add_argument('--resume_optimizer', default=False, type=str2bool,
                            help='Resume optimizer state from checkpoint (default: False)')
        parser.add_argument('--resume_scheduler', default=False, type=str2bool,
                            help='Resume scheduler state from checkpoint (default: False)')
        parser.add_argument('--max_snapshots', default=3, type=int, help='Number of rolling phi snapshots to keep (default: 3)')

        #model parameters
        parser.add_argument('--hidden_size', default=128, type=int, help='hidden size of network (default: 128)')
        parser.add_argument('--message_passing_num', default=1, type=int, help='message passing layer number (default:3)')
        parser.add_argument('--attn_processor_num', default=4, type=int, help='AttnProcessor layer number (default:2)')
        parser.add_argument('--num_channels', type=int, help='now is dynamic set in src/FVdomain/Load_mesh.py')
        parser.add_argument('--phi_num_channels', type=int, help='now is dynamic set in src/FVdomain/Load_mesh.py')
        
        # Input feature augmentation
        parser.add_argument('--num_heads', default=8, type=int,
                            help='Physics-Attention heads (default: 8, matching Transolver)')
        parser.add_argument('--slice_num', default=32, type=int,
                            help='Physics-Attention slice tokens (default: 32, matching Transolver)')
        parser.add_argument('--fourier_num_freqs', default=4, type=int,
                            help='Number of Fourier frequency bands for positional encoding per coordinate dimension. '
                                 'Each freq k gives sin/cos with k complete cycles across the domain. '
                                 'Total added features = 3 * 2 * fourier_num_freqs. Set 0 to disable. (default: 4)')

        #dataset params
        parser.add_argument('--dataset_dir', default='mesh_example/Cylinder_3D_farfield_sym_cyc_heat', type=str, help='path to dataset/case directory (expects an h5 folder inside)')

        # Data-driven / split-aware loading parameters
        parser.add_argument('--loss_mode', default='fvm', type=str,
                            choices=['fvm', 'data_driven'],
                            help='Loss computation mode: fvm (FV residual) or data_driven (L2 vs OpenFOAM labels)')

        parser.add_argument('--split', default='all', type=str,
                            choices=['train', 'val', 'test', 'all'],
                            help='Meta.json split to load. Default "all" = legacy 1-slot-per-h5.')

        parser.add_argument('--labels_dir', default=None, type=str,
                            help='Optional explicit path to labels directory. '
                                 'Default: auto-detect from dataset_dir.')

        params = parser.parse_args()

        return params

def get_hyperparam(params):
    return f"net {params.net}; hs {params.hidden_size};"


# ---------------------------------------------------------------------------
# Reading a run's architecture back off its checkpoint.
#
# The .state file holds only {"model": state_dict}, so the shapes that built it
# live in the states/commandline_args.json Logger writes beside it.  Evaluation
# does not take them from argv, where an evaluator launched with the defaults
# against an --attn_processor_num 8 checkpoint builds a 4-layer model, and the
# strict=False load then accepts it with most of the weights unassigned.  Read
# the sidecar, and load strict.
# ---------------------------------------------------------------------------

# Flags that change a module's shape.  fourier_num_freqs is here because it sets
# node_input_size through the mesh pool, not because the network reads it.
ARCH_KEYS = (
    "net", "hidden_size", "message_passing_num", "attn_processor_num",
    "num_heads", "slice_num", "fourier_num_freqs",
)


def arch_from_checkpoint(ckpt_path):
    """Architecture flags recorded next to `ckpt_path`, or None if absent."""
    sidecar = os.path.join(os.path.dirname(os.path.abspath(ckpt_path)),
                           "commandline_args.json")
    if not os.path.isfile(sidecar):
        return None
    with open(sidecar, "rt") as f:
        saved = json.load(f)
    # None means the flag did not exist when the run was recorded (the 2026-07
    # runs predate --num_heads / --slice_num); leave such keys on their defaults.
    return {k: saved[k] for k in ARCH_KEYS if saved.get(k) is not None}


def apply_checkpoint_arch(params, ckpt_path, argv=None, verbose=True):
    """Overwrite params' architecture with the checkpoint's own.

    An architecture flag given explicitly on the command line wins, so a
    deliberate override stays possible -- but it is reported, because silently
    evaluating a checkpoint under a different architecture is the failure this
    function exists to prevent.
    """
    import sys
    argv = sys.argv[1:] if argv is None else argv
    explicit = {a.lstrip("-").split("=")[0] for a in argv if a.startswith("--")}

    arch = arch_from_checkpoint(ckpt_path)
    if arch is None:
        if verbose:
            print(f"[arch] no commandline_args.json beside {ckpt_path}; "
                  f"using the command line, which may not match the weights")
        return params

    for k, v in arch.items():
        cur = getattr(params, k, None)
        if k in explicit:
            if cur != v:
                print(f"[arch] OVERRIDE {k}: checkpoint {v!r} -> command line {cur!r}")
            continue
        if cur != v:
            if verbose:
                print(f"[arch] {k}: {cur!r} -> {v!r} (from checkpoint)")
            setattr(params, k, v)
    return params
