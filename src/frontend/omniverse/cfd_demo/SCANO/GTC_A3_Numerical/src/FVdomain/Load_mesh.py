"""Utility functions for reading the datasets."""

import sys
import os
file_dir = os.path.dirname(os.path.dirname(__file__))
sys.path.append(file_dir)


import json
import torch
from torch.utils.data import Dataset
import numpy as np
import h5py
from Utils.utilities import FaceType
from FVsolver.FVdiscretization.FVgrad import compute_normal_matrix

class CFDdatasetBase:
    # Base class for CFDdataset with process_trajectory method

    @staticmethod
    def init_fvPatchlist(
        mesh,
        boundary_info=None, # 来自toml
    ):
        """
        Initialize fvPatch list from mesh patch_list and boundary condition information.

        Args:
            mesh: Mesh dictionary containing patch_list
            boundary_info: Dictionary containing boundary condition information from TOML files

        Returns:
            List of fvPatch objects
        """
        from FVdomain.SetBC.boundarybase import fvPatch

        fvPatch_list = []

        # 关于fvPatchlist的 class定义放到了src/FVdomain/SetBC/boundarybase.py这里
        for patch_name, patch_info in mesh["patch_list"].items():
            # Create fvPatch object
            fv_patch = fvPatch(patch_info)

            # Register variables and boundary conditions if boundary_info is provided
            if boundary_info is not None:
                for field_name, field_bc_info in boundary_info.items():
                    # Check if this patch has boundary conditions for this field
                    if 'boundary' in field_bc_info and patch_name in field_bc_info['boundary']:
                        patch_bc_info = field_bc_info['boundary'][patch_name]

                        # Register the variable and its boundary condition
                        fv_patch.register_variable_and_bc(
                            field_name=field_name,
                            bc_info=patch_bc_info,
                            toml_info=field_bc_info
                        )

            fvPatch_list.append(fv_patch)

        return fvPatch_list
    
    @staticmethod
    def init_env(
        mesh,
        boundary_info=None,
    ):
        """
        Initialize fields and targets for a single-case mesh.
        - Creates initial uvwp on cpd cells
        - Creates original variable placeholder used for Dirichlet BC targets

        Args:
            mesh: Mesh dictionary
            boundary_info: Dictionary containing boundary condition information from TOML files
        """
        import os

        # Initialize fvPatch list first
        fvPatch_list = CFDdatasetBase.init_fvPatchlist(mesh, boundary_info)
        mesh["fvPatch_list"] = fvPatch_list

        # Get case path for reading TOML files
        case_path = mesh.get("case_path", "")
        bc_dir = os.path.join(case_path, "h5", "bc")

        # Initialize fields based on available TOML files
        available_fields = set()
        if os.path.exists(bc_dir):
            for filename in os.listdir(bc_dir):
                if filename.endswith('.toml'):
                    field_name = filename[:-5]  # Remove .toml extension
                    available_fields.add(field_name)

        # Initialize velocity field (U)
        if 'U' in available_fields or any(patch.has_field('U') for patch in fvPatch_list):
            mesh["cpd_cell|uvw"] = torch.zeros((mesh["cpd|centroid"].shape[0], 3), dtype=torch.float32)

            # extract U.toml info from boundary_info
            u_data = boundary_info.get('U', {})

            # Parse internalField
            if 'internalField' in u_data and 'value' in u_data['internalField']:
                internal_value = u_data['internalField']['value']
                if isinstance(internal_value, dict):
                    value_type = internal_value.get('value_type', 'uniform')
                    val = internal_value.get('val', [0.0, 0.0, 0.0])

                    if value_type == 'uniform':
                        if isinstance(val, list) and len(val) == 3:
                            mesh["cpd_cell|uvw"][:] = torch.tensor(val, dtype=torch.float32)
                        else:
                            raise NotImplementedError(f"Non-uniform internalField not supported: {value_type}")

        # Initialize pressure field (p)
        if 'p' in available_fields or any(patch.has_field('p') for patch in fvPatch_list):
            mesh["cpd_cell|p"] = torch.zeros((mesh["cpd|centroid"].shape[0], 1), dtype=torch.float32)

            # extract p.toml info from boundary_info
            p_data = boundary_info.get('p', {})

            # Parse internalField
            if 'internalField' in p_data and 'value' in p_data['internalField']:
                internal_value = p_data['internalField']['value']
                if isinstance(internal_value, dict):
                    value_type = internal_value.get('value_type', 'uniform')
                    val = internal_value.get('val', 0.0)

                    if value_type == 'uniform':
                        mesh["cpd_cell|p"][:] = float(val)
                    else:
                        raise NotImplementedError(f"Non-uniform internalField not supported: {value_type}")

        # Initialize temperature field (T)
        if 'T' in available_fields or any(patch.has_field('T') for patch in fvPatch_list):
            mesh["cpd_cell|T"] = torch.zeros((mesh["cpd|centroid"].shape[0], 1), dtype=torch.float32)
            
            # extract T.toml info from boundary_info
            t_data = boundary_info.get('T', {})

            # Parse internalField
            if 'internalField' in t_data and 'value' in t_data['internalField']:
                internal_value = t_data['internalField']['value']
                if isinstance(internal_value, dict):
                    value_type = internal_value.get('value_type', 'uniform')
                    val = internal_value.get('val', 300.0)  # Default temperature

                    if value_type == 'uniform':
                        mesh["cpd_cell|T"][:] = float(val)


        # Initialize p_rgh field if available
        if 'p_rgh' in available_fields or any(patch.has_field('p_rgh') for patch in fvPatch_list):
            mesh["cpd_cell|p_rgh"] = torch.zeros((mesh["cpd|centroid"].shape[0], 1), dtype=torch.float32)

            # extract p_rgh.toml info from boundary_info
            p_rgh_data = boundary_info.get('p_rgh', {})

            # Parse internalField
            if 'internalField' in p_rgh_data and 'value' in p_rgh_data['internalField']:
                internal_value = p_rgh_data['internalField']['value']
                if isinstance(internal_value, dict):
                    value_type = internal_value.get('value_type', 'uniform')
                    val = internal_value.get('val', 0.0)

                    if value_type == 'uniform':
                        mesh["cpd_cell|p_rgh"][:] = float(val)

        # Initialize density field (rho) if available
        if 'rho' in available_fields or any(patch.has_field('rho') for patch in fvPatch_list):
            mesh["cpd_cell|rho"] = torch.zeros((mesh["cpd|centroid"].shape[0], 1), dtype=torch.float32)

            # extract rho.toml info from boundary_info
            rho_data = boundary_info.get('rho', {})
                
            # Parse internalField
            if 'internalField' in rho_data and 'value' in rho_data['internalField']:
                internal_value = rho_data['internalField']['value']
                if isinstance(internal_value, dict):
                    value_type = internal_value.get('value_type', 'uniform')
                    val = internal_value.get('val', 1.0)  # Default density

                    if value_type == 'uniform':
                        mesh["cpd_cell|rho"][:] = float(val)

        return mesh

    @staticmethod
    def set_theta_PDE_dim(mesh, uvw_mag, rho, mu, source, aoa, dt, L):
        """
        Set PDE coefficients by parsing fvSchemes.toml file.
        If parameters are not defined in fvSchemes.toml, default values are used.

        Args:
            mesh: Mesh dictionary
            uvw_mag: Velocity magnitude
            rho: Density
            mu: Dynamic viscosity
            source: Source term
            aoa: Angle of attack
            dt: Time step
            L: Characteristic length
        """
        import math

        # Calculate inlet velocity components
        U_in = uvw_mag * torch.tensor(
            [math.cos(math.radians(aoa)), math.sin(math.radians(aoa))]
        )

        mesh_pos = mesh["cpd|centroid"]

        # Default PDE coefficients
        unsteady_coeff = 1.0
        continuity_eq_coeff = 1.0
        convection_coeff = 1.0
        grad_p_coeff = 1.0 / rho if rho != 0 else 1.0
        nu_coeff = mu / rho if rho != 0 and convection_coeff != 0 else mu
        source_term = source / rho if rho != 0 else source

        # Use already loaded fvSchemes data from mesh
        if "fvSchemes" in mesh:
            fv_schemes_data = mesh["fvSchemes"]

            # Parse fvSchemes section
            if 'fvSchemes' in fv_schemes_data:
                schemes = fv_schemes_data['fvSchemes']

                # Parse time derivative schemes
                ddt_schemes = schemes.get('ddtSchemes', 'implicit')
                if ddt_schemes == 'implicit':
                    unsteady_coeff = 1.0
                elif ddt_schemes == 'imex':
                    unsteady_coeff = 1.0  # Can be adjusted based on specific IMEX scheme

                # Parse divergence schemes
                div_schemes = schemes.get('divSchemes', {})
                if isinstance(div_schemes, dict):
                    # Check for specific field schemes
                    u_scheme = div_schemes.get('U', 'Linear')
                    if 'Linear' in u_scheme:
                        convection_coeff = 1.0
                    elif 'Upwind' in u_scheme:
                        convection_coeff = 0.8  # Reduced for upwind schemes

                # Parse pressure schemes
                pressure_schemes = schemes.get('PressureSchemes', 'p_flux')
                if pressure_schemes == 'p_flux':
                    grad_p_coeff = 1.0 / rho if rho != 0 else 1.0
                elif pressure_schemes == 'p_grad':
                    grad_p_coeff = 1.0 / rho if rho != 0 else 1.0

                # Parse gradient schemes
                grad_schemes = schemes.get('gradSchemes', '1st')
                if grad_schemes in ['1st', 'GG']:
                    # First order schemes
                    pass  # Keep default coefficients
                elif grad_schemes in ['2nd', '3rd', '4th']:
                    # Higher order schemes might need adjustment
                    pass  # Keep default coefficients for now

            # Parse PDE theta section if available
            if 'pde_theta' in fv_schemes_data:
                pde_theta = fv_schemes_data['pde_theta']

                # Override with specific PDE coefficients if defined
                if 'unsteady_coeff' in pde_theta:
                    unsteady_coeff = float(pde_theta['unsteady_coeff'])
                if 'continuity_coeff' in pde_theta:
                    continuity_eq_coeff = float(pde_theta['continuity_coeff'])
                if 'convection_coeff' in pde_theta:
                    convection_coeff = float(pde_theta['convection_coeff'])

            # Parse fvSolution section for reference pressure
            if 'fvSolution' in fv_schemes_data:
                fv_solution = fv_schemes_data['fvSolution']

                # Set reference pressure cell and value
                if 'pRefcell' in fv_solution:
                    mesh["pRefCell"] = torch.tensor(fv_solution['pRefcell'], dtype=torch.long)
                if 'pRefvalue' in fv_solution:
                    mesh["pRefValue"] = torch.tensor(fv_solution['pRefvalue'], dtype=torch.float32)

            print("Successfully parsed fvSchemes.toml configuration")
        else:
            print("fvSchemes.toml not found in mesh data, using default coefficients")

        # Adjust nu_coeff based on convection coefficient
        if convection_coeff == 0:
            # Poisson equation case
            nu_coeff = mu
        else:
            # Navier-Stokes equation case
            nu_coeff = mu / rho if rho != 0 else mu

        # Create theta_PDE tensor
        theta_PDE = torch.tensor(
            [
                unsteady_coeff,      # 0
                continuity_eq_coeff, # 1
                convection_coeff,    # 2
                grad_p_coeff,        # 3
                nu_coeff,            # 4
                source_term,         # 5
                dt,                  # 6
                U_in[0].item(),      # 7
                U_in[1].item(),      # 8
            ],
            device=mesh_pos.device,
            dtype=torch.float32,
        ).view(1, -1)

        mesh["theta_PDE"] = theta_PDE

        return mesh

    @staticmethod
    def calc_WLSQ_A_B_normal_matrix(mesh, order):
        
        if order == "GG":
            order = "1st" # 临时替换
        
        if not "A_cell_to_cell" in mesh.keys():
            
            """>>> compute WLSQ cell to cell left A matrix >>>"""
            cpd_centroid = mesh["cpd|centroid"]
            cpd_neighbor_cell_x = mesh["cpd|neighbor_cell_x"].long()
            
            # 注意后续在WLSQ梯度重构的时候，不要让边界面元与边界面元出现连接边: 这里是打补丁，后续需要优化
            cell_type = mesh["cpd|cell_type"].long().squeeze()
            mask_cell_interior = (cell_type == FaceType.NORMAL)  # NORMAL
            mask_cell_boundary = ~mask_cell_interior
            mask_valid_nb_cell_x = ~(mask_cell_boundary[cpd_neighbor_cell_x[0]] & mask_cell_boundary[cpd_neighbor_cell_x[1]])
            cpd_neighbor_cell_x = cpd_neighbor_cell_x[:,mask_valid_nb_cell_x]
            mesh["cpd|neighbor_cell_x"] = cpd_neighbor_cell_x
            
            """ >>> compute WLSQ cell to cell left A matrix >>> """
            (A_cell_to_cell, two_way_B_cell_to_cell) = compute_normal_matrix(
                order=order,
                mesh_pos=cpd_centroid,
                edge_index=cpd_neighbor_cell_x, # 默认应该是仅包含1阶邻居点+构成共点的单元的所有点
            )
            
            mesh["A_cell_to_cell"] = A_cell_to_cell.to(torch.float32)
            mesh["single_B_cell_to_cell"] = (
                torch.chunk(two_way_B_cell_to_cell, 2, dim=0)[0]
            ).to(torch.float32)
            """ <<< compute WLSQ cell to cell right B matrix<<< """

        return mesh
    
    @staticmethod
    def load_boundary_files(case_path):
        # Load boundary condition information
        bc_dir = os.path.join(case_path, "h5", "bc")
        boundary_info = {}

        if os.path.exists(bc_dir):
            import tomlkit
            for filename in os.listdir(bc_dir):
                if filename.endswith('.toml'):
                    field_name = filename[:-5]  # Remove .toml extension
                    try:
                        with open(os.path.join(bc_dir, filename), 'r') as f:
                            boundary_info[field_name] = tomlkit.load(f)
                    except Exception as e:
                        print(f"Warning: Failed to load {filename}: {e}")
        return boundary_info
        
    @staticmethod
    def transform_mesh(
        mesh, 
        params=None
    ):

        # Load boundary condition information
        boundary_info = CFDdatasetBase.load_boundary_files(mesh["case_path"])

        # Initialize fields and fvpatch
        mesh, phi_cpd_cell = CFDdatasetBase.init_env(
            mesh,
            boundary_info=boundary_info,
        )

        mesh = CFDdatasetBase.set_theta_PDE_dim(
            mesh,
        )

        mesh = CFDdatasetBase.calc_WLSQ_A_B_normal_matrix(mesh,params.order)
        
        ''' 这里将出口处的cpd_cell改为ghost cell的坐标，这里继续打补丁，为了暂时不改变h5网格 '''
        # cpd_centroid = mesh["cpd|centroid"]
        # cpd_neighbor_cell = mesh["cpd|neighbor_cell"].long()
        # face_type = mesh["face|face_type"].long().squeeze()
        # mask_outflow_face = (face_type == FaceType.OUTFLOW)  # OUTFLOW

        # outflow_idx_cpd_cell = cpd_neighbor_cell[1,mask_outflow_face]
        # owner_outflow_idx_cpd_cell = cpd_neighbor_cell[0,mask_outflow_face]
        # cpd_centroid[outflow_idx_cpd_cell] = 2*cpd_centroid[outflow_idx_cpd_cell] - cpd_centroid[owner_outflow_idx_cpd_cell]
        # mesh["cpd|centroid"] = cpd_centroid
        '''这里将出口处的cpd_cell改为ghost cell的坐标，这里继续打补丁，为了暂时不改变h5网格'''
        
        return mesh, phi_cpd_cell

class H5CFDdataset(Dataset):
    def __init__(self, params, file_list):
        super().__init__()

        self.file_list = file_list
        self.params = params
        
    def __getitem__(self, index):
        path = self.file_list[index]
        file_dir = os.path.dirname(path)
        h5_file = h5py.File(path, "r")

        ''' >>> load mesh data to CPU from h5 file >>> '''
        # store case name
        mesh = {"case_name":h5_file.attrs['case_name']}
        # Load patch_list from HDF5 metadata if available
        mesh["patch_list"] = json.loads(h5_file.attrs['patch_list'])
        # Add case_path for boundary condition loading (parent of h5 directory)
        mesh["case_path"] = os.path.dirname(file_dir)
        # convert to tensors
        for key in h5_file.keys():
            value = h5_file[key][()]
            if isinstance(value, np.ndarray):
                # convert numpy array to torch tensor
                mesh[key] = torch.from_numpy(value)
            else:
                mesh[key] = value
        ''' <<< load mesh data to CPU from h5 file <<< '''
        
        # Load config.toml configuration
        import tomlkit
        fvschemes_path = os.path.join(file_dir, "config.toml")
        mesh["config"] = tomlkit.load(open(fvschemes_path, "r"))

        # start to calculate other attributes like stencil, WLSQ matrix, etc.
        mesh_transformed, phi_cpd_cell = CFDdatasetBase.transform_mesh(
            mesh, 
            self.params
        )

        # return to CPU!
        return mesh_transformed, phi_cpd_cell 

    def __len__(self):
        return len(self.file_list)

