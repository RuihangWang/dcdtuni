import torch
import numpy as np
import os
import pyvista as pv


def export_full_mesh_vtu(mesh_pos, pv_cells_node, pv_cells_type, save_file_path, data_dict=None, t:float=None):
    """Export VTU file for the complete mesh using PyVista cell format.

    This method creates a VTU file containing all cells in the mesh using the
    PyVista-compatible cell connectivity format (pv_cells_node and pv_cells_type).

    Args:
        mesh_pos: Tensor of node positions.
        pv_cells_node: PyVista format cell connectivity.
        pv_cells_type: PyVista format cell types.
        save_file_path (str): The full path to save the VTU file.
        data_dict (dict, optional): Dictionary containing node or cell data.
                                    Keys should start with 'node|' or 'cell|'.
                                    Defaults to None.
        t (float, optional): Time value for time series. If provided, creates ParaView-compatible
                            time series files with PVD collection. Must be float32 precision.
    """

    # Handle time series export
    if t is not None:
        # Ensure t is float32
        time_value = np.float32(t)

        # For time series, we need to manage the file structure differently
        base_path = save_file_path.replace('.vtu', '') if save_file_path.endswith('.vtu') else save_file_path

        # Create time series directory
        time_series_dir = os.path.dirname(base_path)
        os.makedirs(time_series_dir, exist_ok=True)

        # Generate time step file name with zero-padded index
        # Use a global counter stored in a file to maintain consistency across calls
        counter_file = os.path.join(time_series_dir, '.time_step_counter')
        if os.path.exists(counter_file):
            with open(counter_file, 'r') as f:
                time_step_index = int(f.read().strip())
        else:
            time_step_index = 0

        vtu_file_name = f"{os.path.basename(base_path)}_{time_step_index:06d}.vtu"
        vtu_file_path = os.path.join(time_series_dir, vtu_file_name)

        # Export VTU file
        success = _export_single_vtu(mesh_pos, pv_cells_node, pv_cells_type, vtu_file_path, data_dict)

        if success:
            # Create or update PVD file for ParaView time series
            pvd_file_path = f"{base_path}.pvd"
            _update_pvd_file(pvd_file_path, vtu_file_name, time_value)

            # Update time step counter
            with open(counter_file, 'w') as f:
                f.write(str(time_step_index + 1))

            print(f"✅ Exported time series step {time_step_index} (t={time_value:.6f}) to: {vtu_file_path}")
            print(f"📁 Updated PVD file: {pvd_file_path}")

        return success
    else:
        # Original single file export
        return _export_single_vtu(mesh_pos, pv_cells_node, pv_cells_type, save_file_path, data_dict)


def export_cell_subset_vtu(mesh_pos, pv_cells_node, pv_cells_type, cell_ids,
                           save_file_path, data_dict=None, extra_cell_data=None):
    """Export ONLY the cells in ``cell_ids`` — the ROI-restricted sibling of
    :func:`export_full_mesh_vtu`.

    ``cell_ids`` index the VTK cells in ``pv_cells_type`` order, which is also the row
    order of every array in ``data_dict``. Each value written is the value the full export
    would have written for the same cell: this selects, it does not resample or re-average.

    Point compaction is delegated to ``UnstructuredGrid.extract_cells``. The mesh is
    polyhedral (every cell is VTK_POLYHEDRON=42, a nested face stream), so hand-slicing
    the connectivity array would mean re-parsing and re-indexing that stream per cell —
    ``extract_cells`` already does it correctly, and measured 0.22 s for an 8 k-cell subset
    of the 344,629-cell GTC mesh. It tags the result with ``vtkOriginalCellIds`` /
    ``vtkOriginalPointIds``; both are dropped, because the caller supplies the provenance
    that actually means something here — the cpd id — via ``extra_cell_data``.

    Args:
        mesh_pos: node positions of the FULL mesh.
        pv_cells_node / pv_cells_type: PyVista connectivity of the FULL mesh.
        cell_ids: integer indices of the cells to keep.
        save_file_path: destination .vtu.
        data_dict: per-FULL-mesh-cell arrays; sliced with ``cell_ids`` here.
        extra_cell_data: per-SUBSET-cell arrays, already in ``cell_ids`` order.

    Returns:
        The pyvista subset grid that was written.
    """
    vtu_output_dir = os.path.dirname(save_file_path)
    os.makedirs(vtu_output_dir, exist_ok=True)

    points = mesh_pos.cpu().numpy() if isinstance(mesh_pos, torch.Tensor) else np.asarray(mesh_pos)
    cells = pv_cells_node.cpu().numpy() if isinstance(pv_cells_node, torch.Tensor) else np.asarray(pv_cells_node)
    cell_types = pv_cells_type.cpu().numpy() if isinstance(pv_cells_type, torch.Tensor) else np.asarray(pv_cells_type)
    if points.shape[1] == 2:
        points = np.c_[points, np.zeros(points.shape[0])]

    ids = np.asarray(cell_ids, dtype=np.int64).reshape(-1)
    grid = pv.UnstructuredGrid(cells, cell_types, points)

    if data_dict:
        for key, value in data_dict.items():
            if value is None:
                continue
            safe_key = key.replace('|', '_')
            v = value.cpu().numpy() if isinstance(value, torch.Tensor) else np.asarray(value)
            if v.shape[0] != cell_types.shape[0]:
                print(f"Warning: Skipping key '{safe_key}' - {v.shape[0]} rows is not the "
                      f"cell count {cell_types.shape[0]}; a subset cannot be aligned to it")
                continue
            grid.cell_data[safe_key] = v

    sub = grid.extract_cells(ids)
    for name in ("vtkOriginalCellIds", "vtkOriginalPointIds"):
        if name in sub.cell_data:
            del sub.cell_data[name]
        if name in sub.point_data:
            del sub.point_data[name]

    if extra_cell_data:
        for key, value in extra_cell_data.items():
            v = value.cpu().numpy() if isinstance(value, torch.Tensor) else np.asarray(value)
            sub.cell_data[key.replace('|', '_')] = v

    sub.save(save_file_path)
    return sub


def _export_single_vtu(mesh_pos, pv_cells_node, pv_cells_type, save_file_path, data_dict=None):
    """Internal function to export a single VTU file."""
    # Ensure the output directory exists
    vtu_output_dir = os.path.dirname(save_file_path)
    os.makedirs(vtu_output_dir, exist_ok=True)

    # Convert tensor data to numpy arrays, checking type first
    points = mesh_pos.cpu().numpy() if isinstance(mesh_pos, torch.Tensor) else np.asarray(mesh_pos)
    cells = pv_cells_node.cpu().numpy() if isinstance(pv_cells_node, torch.Tensor) else np.asarray(pv_cells_node)
    cell_types = pv_cells_type.cpu().numpy() if isinstance(pv_cells_type, torch.Tensor) else np.asarray(pv_cells_type)

    # Ensure points are 3D
    if points.shape[1] == 2:
        points = np.c_[points, np.zeros(points.shape[0])]

    # Create PyVista UnstructuredGrid directly
    grid = pv.UnstructuredGrid(cells, cell_types, points)

    # Add data from data_dict
    if data_dict:
        for key, value in data_dict.items():
            if value is None:
                continue

            # Replace '|' with '_' in key to avoid Windows file system issues
            safe_key = key.replace('|', '_')

            numpy_value = value.cpu().numpy() if isinstance(value, torch.Tensor) else np.asarray(value)

            if safe_key.startswith("node"):
                grid.point_data[safe_key] = numpy_value
            elif safe_key.startswith("cell"):
                grid.cell_data[safe_key] = numpy_value
            else:
                if numpy_value.shape[0] == points.shape[0]:
                    grid.point_data[safe_key] = numpy_value
                elif numpy_value.shape[0] == cell_types.shape[0]:
                    grid.cell_data[safe_key] = numpy_value
                else:
                    print(
                        f"Warning: Skipping key '{safe_key}' - data shape {numpy_value.shape} "
                        f"doesn't match points ({points.shape}) or cells ({cell_types.shape})"
                    )
    
    # Save the grid to VTU file
    grid.save(save_file_path)
    

def _update_pvd_file(pvd_file_path, vtu_file_name, time_value):
    """
    Create or update a PVD file for ParaView time series visualization.

    Args:
        pvd_file_path (str): Path to the PVD file
        vtu_file_name (str): Name of the VTU file (relative to PVD file)
        time_value (float): Time value for this step
    """
    import xml.etree.ElementTree as ET

    # Check if PVD file already exists
    if os.path.exists(pvd_file_path):
        # Parse existing PVD file
        tree = ET.parse(pvd_file_path)
        root = tree.getroot()
        collection = root.find('Collection')
    else:
        # Create new PVD file structure
        root = ET.Element('VTKFile')
        root.set('type', 'Collection')
        root.set('version', '0.1')
        root.set('byte_order', 'LittleEndian')

        collection = ET.SubElement(root, 'Collection')
        tree = ET.ElementTree(root)

    # Add new DataSet entry
    dataset = ET.SubElement(collection, 'DataSet')
    dataset.set('timestep', str(time_value))
    dataset.set('group', '')
    dataset.set('part', '0')
    dataset.set('file', vtu_file_name)

    # Write the updated PVD file
    tree.write(pvd_file_path, encoding='utf-8', xml_declaration=True)

    # Format the XML file for better readability
    _format_xml_file(pvd_file_path)


def _format_xml_file(xml_file_path):
    """Format XML file for better readability."""
    try:
        import xml.dom.minidom as minidom

        # Parse and pretty print
        dom = minidom.parse(xml_file_path)
        pretty_xml = dom.toprettyxml(indent="  ")

        # Remove empty lines and write back
        with open(xml_file_path, 'w') as f:
            f.write('\n'.join([line for line in pretty_xml.split('\n') if line.strip()]))
    except:
        # If formatting fails, just keep the original file
        pass


def unique_preserve_order(x: torch.Tensor) -> torch.Tensor:
    """
    等价于 NumPy 的 `np.unique(x, return_index=True)[0][np.argsort(idx)]`，
    但全部在 PyTorch 内部完成，支持 CUDA。
    """
    # 1) 先做一次 unique 拿到 inverse，下步用来找“第一次出现在哪儿”
    uniq, inv = torch.unique(x, return_inverse=True)          # uniq 仍然是升序的
    # 2) 对 inv 做 scatter-amin 得到每个唯一值第一次出现的下标
    first_occ = torch.full_like(uniq, x.numel(), dtype=torch.long)
    first_occ.scatter_reduce_(0, inv, torch.arange(x.numel(), device=x.device),
                            reduce="amin")                  # PyTorch ≥1.12
    # 3) 根据 first_occ 升序重新排列 uniq
    order = torch.argsort(first_occ, stable=True)             # stable=True 保持同下标稳定
    return uniq[order]


def to_pv_cells_nodes_and_cell_types(cells_node:torch.Tensor,cells_ptr:torch.Tensor):

    from Parse_mesh.utils.utilities import seperate_domain
    
    # 暂时先写vtk来可视化
    domain_list = seperate_domain(cells_ptr=cells_ptr, cells_node=cells_node, cells_face=None)
    
    pv_cells_node_list=[]
    pv_cells_type_list=[]
    new_cells_ptr=[]
    for domain in domain_list:
        
        _ct, _cells_node, _, _cells_ptr, _ = domain
        new_cells_ptr.append(_cells_ptr)
        _cells_node = _cells_node.reshape(-1,_ct)
        num_cells = _cells_node.shape[0]
        _cells_node = torch.cat(
            (torch.full((_cells_node.shape[0],1),_ct), _cells_node),
            dim=1,
        ).reshape(-1)
        pv_cells_node_list.append(_cells_node)
        
        # 根据顶点数设置单元类型，支持2D和3D
        if _ct == 3:
            cells_types = torch.full((num_cells,),pv.CellType.TRIANGLE)
        elif _ct == 4:
            # 可能是2D四边形或3D四面体，根据mesh的维度判断
            # 通常4个顶点在3D中是四面体(tetrahedra)，在2D中是四边形(quad)
            cells_types = torch.full((num_cells,),pv.CellType.QUAD)  # 默认为2D四边形
        elif _ct == 8:
            # 3D六面体/立方体
            cells_types = torch.full((num_cells,),pv.CellType.HEXAHEDRON)
        elif _ct == 6:
            # 3D楔形/三角柱
            cells_types = torch.full((num_cells,),pv.CellType.WEDGE)
        elif _ct == 5:
            # 3D金字塔
            cells_types = torch.full((num_cells,),pv.CellType.PYRAMID)
        else:
            # 多边形/多面体
            cells_types = torch.full((num_cells,),pv.CellType.POLYGON)
        pv_cells_type_list.append(cells_types)
        
    pv_cells_node = torch.cat(pv_cells_node_list,dim=0) 
    pv_cells_type = torch.cat(pv_cells_type_list,dim=0)
    new_cells_ptr = unique_preserve_order(torch.cat(new_cells_ptr, dim=0).squeeze())

    return pv_cells_node,pv_cells_type,new_cells_ptr


# def write_hybrid_mesh_to_vtu(mesh_pos, cells_node:torch.Tensor, cells_ptr:torch.Tensor, data_dict, file_path):
#     """
#     使用 PyVista 写入包含混合单元类型的 VTU 文件。
#     函数内部处理单元类型和顶点索引的转换。

#     参数:
#     - mesh_pos: 顶点坐标 [N, 3] (numpy array or torch tensor)
#     - cells: flat 形式的顶点索引 [M,] (numpy array or torch tensor)
#     - cells_index: 单元索引 [M,] - 标记每个顶点属于哪个单元 (numpy array or torch tensor)
#     - data_dict: 包含顶点或单元数据的字典
#     - file_path: 输出文件路径
#     """

#     # --- 1. 从 flat 格式重构 PyVista 需要的 cells 和 cell_types ---
#     pv_cells, pv_cell_types, new_cells_ptr = to_pv_cells_nodes_and_cell_types(
#         cells_node=cells_node, cells_ptr=cells_ptr
#     )

#     # --- 2. 数据格式转换 ---
#     if hasattr(mesh_pos, 'cpu'):
#         mesh_pos = mesh_pos.cpu().numpy()
#     if hasattr(cells_node, 'cpu'):
#         cells_node = cells_node.cpu().numpy()
#     if hasattr(cells_ptr, 'cpu'):
#         cells_ptr = cells_ptr.cpu().numpy()

#     # 确保 mesh_pos 是 3D 的
#     if mesh_pos.shape[1] == 2:
#         points = np.c_[mesh_pos, np.zeros(mesh_pos.shape[0])]
#     else:
#         points = mesh_pos

#     # --- 3. 创建 PyVista UnstructuredGrid ---
#     grid = pv.UnstructuredGrid(pv_cells.numpy(), pv_cell_types.numpy(), points)

#     # --- 4. 添加顶点和单元数据 ---
#     for key, values in data_dict.items():
#         if key.startswith("node|"):
#             # 添加顶点数据，去掉前缀 'node|'
#             grid.point_data[key] = np.array(values)
#         elif key.startswith("cell|") or key.startswith("face|"):
#             # 添加单元数据，去掉前缀 'cell|'
#             grid.cell_data[key] = np.array(values[new_cells_ptr])
#         else:
#             print(f"警告: 未知的数据前缀 {key}，将忽略该数据。")
    
#     # --- 5. 写入文件 ---
#     grid.save(file_path, binary=True)


# def write_point_cloud_to_vtk(data: dict, write_file_path):
#     # dict :{'node|pos'         : position}
#     #       {'node|sth'         : node_value}

#     grid = vtk.vtkUnstructuredGrid()

#     # process node
#     points = data["node|pos"]
#     points_vtk = vtk.vtkPoints()
#     [points_vtk.InsertNextPoint(point) for point in points]
#     grid.SetPoints(points_vtk)
#     point_data = grid.GetPointData()
#     for key in data.keys():
#         if not key.startswith("node"):
#             continue
#         if key == "node|pos":
#             continue
#         array_data = data[key]
#         vtk_data_array = vtk.vtkFloatArray()
#         k = (
#             1
#             if type(array_data[0]) is np.float64 or type(array_data[0]) is np.float32
#             else len(array_data[0])
#         )
#         vtk_data_array.SetNumberOfComponents(k)

#         if k == 1:
#             [vtk_data_array.InsertNextTuple([value]) for value in array_data]
#         else:
#             [vtk_data_array.InsertNextTuple(value) for value in array_data]

#         vtk_data_array.SetName(key)
#         point_data.AddArray(vtk_data_array)

#     # 将网格保存为 VTU 文件
#     writer = vtk.vtkXMLUnstructuredGridWriter()
#     writer.SetFileName(write_file_path)
#     writer.SetInputData(grid)
#     writer.Write()


# def write_vtu_file_2D_poly_to_tri(mesh_pos, cells, cells_index, point_data_dict, file_path):
#     """
#     写入VTU文件，支持flat格式的cells和cells_index
    
#     参数:
#     - mesh_pos: 顶点坐标 [N, 3]
#     - cells: flat形式的顶点索引 [M,] - 包含所有单元的顶点索引
#     - cells_index: 单元索引 [M,] - 标记每个顶点属于哪个单元 [0,0,0,1,1,1,1,2,2,2,2,...]
#     - point_data_dict: 顶点数据字典
#     - file_path: 输出文件路径
#     """
#     # 创建UnstructuredGrid对象
#     unstructured_grid = vtk.vtkUnstructuredGrid()

#     # 设置点（顶点）数据将在后面处理，因为我们可能需要添加质心点

#     # 从flat格式重构单元数据
#     if isinstance(cells, np.ndarray):
#         cells = cells.astype(int)
#     if isinstance(cells_index, np.ndarray):
#         cells_index = cells_index.astype(int)
    
#     # 找到所有唯一的单元ID
#     unique_cell_ids = np.unique(cells_index)
    
#     # 为了添加质心点，我们需要扩展顶点列表
#     extended_points = vtk.vtkPoints()
#     # 先复制原有顶点
#     for pos in mesh_pos:
#         extended_points.InsertNextPoint(pos)
    
#     current_point_id = len(mesh_pos)  # 下一个可用的点ID
    
#     # 为每个单元构建顶点列表
#     for cell_id in unique_cell_ids:
#         # 找到属于当前单元的所有顶点
#         cell_vertex_mask = (cells_index == cell_id)
#         cell_vertices = cells[cell_vertex_mask]
#         num_vertices = len(cell_vertices)
        
#         if num_vertices == 3:
#             # 三角形单元，直接添加
#             cell_id_list = vtk.vtkIdList()
#             for point_id in cell_vertices:
#                 cell_id_list.InsertNextId(int(point_id))
#             unstructured_grid.InsertNextCell(vtk.VTK_TRIANGLE, cell_id_list)
            
#         elif num_vertices == 4:
#             # 四边形单元，保留为四边形
#             cell_id_list = vtk.vtkIdList()
#             for point_id in cell_vertices:
#                 cell_id_list.InsertNextId(int(point_id))
#             unstructured_grid.InsertNextCell(vtk.VTK_QUAD, cell_id_list)
            
#         else:
#             # polygon单元，拆分为三角形
#             # 计算质心
#             cell_coords = mesh_pos[cell_vertices]
#             centroid = np.mean(cell_coords, axis=0)
            
#             # 添加质心点
#             centroid_id = current_point_id
#             extended_points.InsertNextPoint(centroid)
#             current_point_id += 1
            
#             # 将polygon拆分为三角形：每相邻两个顶点与质心构成一个三角形
#             for i in range(num_vertices):
#                 next_i = (i + 1) % num_vertices
                
#                 # 创建三角形：当前顶点 -> 下一个顶点 -> 质心
#                 triangle = vtk.vtkIdList()
#                 triangle.InsertNextId(int(cell_vertices[i]))
#                 triangle.InsertNextId(int(cell_vertices[next_i]))
#                 triangle.InsertNextId(centroid_id)
                
#                 unstructured_grid.InsertNextCell(vtk.VTK_TRIANGLE, triangle)
    
#     # 更新网格的点数据
#     unstructured_grid.SetPoints(extended_points)

#     # 设置点数据（例如法向量或其他数据）
#     if point_data_dict is not None:
#         # 计算需要为质心插值的数据
#         num_original_points = len(mesh_pos)
#         num_total_points = extended_points.GetNumberOfPoints()
        
#         for name, data_array in point_data_dict.items():
#             vtk_data_array = vtk.vtkDoubleArray()
            
#             # 处理标量和向量数据
#             if len(data_array.shape) == 1:
#                 # 标量数据
#                 vtk_data_array.SetNumberOfComponents(1)
                
#                 # 为质心插值数据
#                 extended_data = np.zeros(num_total_points)
#                 extended_data[:num_original_points] = data_array
                
#                 # 为每个质心计算插值值
#                 current_point_id = num_original_points
#                 for cell_id in unique_cell_ids:
#                     cell_vertex_mask = (cells_index == cell_id)
#                     cell_vertices = cells[cell_vertex_mask]
#                     num_vertices = len(cell_vertices)
                    
#                     if num_vertices > 4:  # 只有polygon需要质心
#                         # 使用该polygon顶点的平均值作为质心的值
#                         centroid_value = np.mean(data_array[cell_vertices])
#                         extended_data[current_point_id] = centroid_value
#                         current_point_id += 1
                
#                 # 插入数据
#                 for data in extended_data:
#                     vtk_data_array.InsertNextValue(float(data))
                    
#             else:
#                 # 向量数据
#                 vtk_data_array.SetNumberOfComponents(data_array.shape[1] if len(data_array.shape) > 1 else 1)
                
#                 # 为质心插值数据
#                 extended_data = np.zeros((num_total_points, data_array.shape[1]))
#                 extended_data[:num_original_points] = data_array
                
#                 # 为每个质心计算插值值
#                 current_point_id = num_original_points
#                 for cell_id in unique_cell_ids:
#                     cell_vertex_mask = (cells_index == cell_id)
#                     cell_vertices = cells[cell_vertex_mask]
#                     num_vertices = len(cell_vertices)
                    
#                     if num_vertices > 4:  # 只有polygon需要质心
#                         # 使用该polygon顶点的平均值作为质心的值
#                         centroid_value = np.mean(data_array[cell_vertices], axis=0)
#                         extended_data[current_point_id] = centroid_value
#                         current_point_id += 1
                
#                 # 插入数据
#                 for data in extended_data:
#                     if hasattr(data, '__len__'):
#                         vtk_data_array.InsertNextTuple(data)
#                     else:
#                         vtk_data_array.InsertNextValue(float(data))
                        
#             vtk_data_array.SetName(name)
#             unstructured_grid.GetPointData().AddArray(vtk_data_array)

#     # 写入VTU文件
#     writer = vtk.vtkXMLUnstructuredGridWriter()
#     writer.SetFileName(file_path)
#     writer.SetInputData(unstructured_grid)
#     writer.Write()


# def write_vtu_file_2D_quad(mesh_pos, cells, point_data_dict, file_path):
#     # 创建UnstructuredGrid对象
#     unstructured_grid = vtk.vtkUnstructuredGrid()

#     # 设置点（顶点）数据
#     points = vtk.vtkPoints()
#     for pos in mesh_pos:
#         points.InsertNextPoint(pos)
#     unstructured_grid.SetPoints(points)

#     # 设置单元（四边形面片）数据
#     for cell in cells:
#         if len(cell) == 4:  # 确保每个单元都是四边形
#             cell_id_list = vtk.vtkIdList()
#             for point_id in cell:
#                 cell_id_list.InsertNextId(point_id)
#             unstructured_grid.InsertNextCell(vtk.VTK_QUAD, cell_id_list)
#         else:
#             print(f"Warning: Skipping cell {cell} because it does not have 4 vertices.")

#     # 设置点数据（例如法向量或其他数据）
#     if point_data_dict is not None:
#         for name, data_array in point_data_dict.items():
#             vtk_data_array = vtk.vtkDoubleArray()
#             vtk_data_array.SetNumberOfComponents(
#                 len(data_array[0])
#             )  # 根据数据维度设置组件数
#             vtk_data_array.SetName(name)  # 使用字典的键作为数据数组的名称
#             for data in data_array:
#                 vtk_data_array.InsertNextTuple(data)
#             unstructured_grid.GetPointData().AddArray(vtk_data_array)

#     # 写入VTU文件
#     writer = vtk.vtkXMLUnstructuredGridWriter()
#     writer.SetFileName(file_path)
#     writer.SetInputData(unstructured_grid)
#     writer.Write()

#     print("VTU has been saved to:", file_path)


# def write_vtu_file_2D_quad_subprocess(
#     vis_pos, graph_node_valid, pred_np, target_np, logger, epoch, current_files_name
# ):
#     """
#     子进程中调用的函数，用于调用 write_vtu_file_2D_quad
#     """
#     write_vtu_file_2D_quad(
#         mesh_pos=vis_pos,
#         cells=graph_node_valid.face.mT.cpu().numpy(),
#         point_data_dict={
#             "node|u": pred_np[:, 0:1],
#             "node|v": pred_np[:, 1:2],
#             "node|p": pred_np[:, 2:3],
#             "node|nut": pred_np[:, 3:4],
#             "node|u_true": target_np[:, 0:1],
#             "node|v_true": target_np[:, 1:2],
#             "node|p_true": target_np[:, 2:3],
#             "node|nut_true": target_np[:, 3:4],
#         },
#         file_path=f"{logger.valid_visualization}/valid{epoch}_{current_files_name}.vtu",
#     )
#     exit(0)


# def write_vtp_file(mesh_pos, edge_index, output_filename):
#     # 创建 vtkPoints 对象并将顶点坐标添加进去
#     points = vtk.vtkPoints()
#     for pos in mesh_pos:
#         points.InsertNextPoint(pos[0], pos[1], 0)  # 假设 z = 0，创建二维点

#     # 创建 vtkCellArray 对象并将线条（edges）添加进去
#     lines = vtk.vtkCellArray()
#     for edge in edge_index.T:  # edge_index 形状为 [2, E]，需要转置以遍历每条边
#         line = vtk.vtkLine()
#         line.GetPointIds().SetId(0, edge[0])  # 边的第一个顶点
#         line.GetPointIds().SetId(1, edge[1])  # 边的第二个顶点
#         lines.InsertNextCell(line)

#     # 创建 vtkPolyData 对象
#     poly_data = vtk.vtkPolyData()
#     poly_data.SetPoints(points)
#     poly_data.SetLines(lines)

#     # 使用 vtkXMLPolyDataWriter 写入到 .vtp 文件
#     writer = vtk.vtkXMLPolyDataWriter()
#     writer.SetFileName(output_filename)
#     writer.SetInputData(poly_data)
#     writer.Write()
#     print(f"VTP file saved at: {output_filename}")


# def export_time_series_vtu(mesh_pos, pv_cells_node, pv_cells_type, time_series_data_dict,
#                           output_dir, base_filename="time_series", dt=1.0):
#     """
#     Export time series VTU files for visualization in ParaView.

#     This function creates multiple VTU files (one for each time step) and a PVD file
#     that describes the time series for ParaView animation.

#     Args:
#         mesh_pos: Tensor of node positions [N_nodes, 3].
#         pv_cells_node: PyVista format cell connectivity.
#         pv_cells_type: PyVista format cell types.
#         time_series_data_dict (dict): Dictionary containing time series data.
#                                      Keys should start with 'node|' or 'cell|'.
#                                      Values should have shape [time_steps, N_cells/N_nodes, channels].
#                                      For example: [500, 5000, 3] means 500 time steps.
#         output_dir (str): Directory to save the time series files.
#         base_filename (str): Base name for the output files. Default: "time_series".
#         dt (float): Physical time step size. Default: 1.0.
#                    Time values will be calculated as t = time_step_index * dt.

#     Returns:
#         str: Path to the generated PVD file.
#     """

#     # Create output directory
#     os.makedirs(output_dir, exist_ok=True)

#     # Convert tensor data to numpy arrays
#     points = mesh_pos.cpu().numpy() if isinstance(mesh_pos, torch.Tensor) else np.asarray(mesh_pos)
#     cells = pv_cells_node.cpu().numpy() if isinstance(pv_cells_node, torch.Tensor) else np.asarray(pv_cells_node)
#     cell_types = pv_cells_type.cpu().numpy() if isinstance(pv_cells_type, torch.Tensor) else np.asarray(pv_cells_type)

#     # Ensure points are 3D
#     if points.shape[1] == 2:
#         points = np.c_[points, np.zeros(points.shape[0])]

#     # Determine number of time steps from the first data array
#     if not time_series_data_dict:
#         raise ValueError("time_series_data_dict cannot be empty")

#     first_key = list(time_series_data_dict.keys())[0]
#     first_data = time_series_data_dict[first_key]

#     # Convert to numpy if needed
#     if isinstance(first_data, torch.Tensor):
#         first_data = first_data.cpu().numpy()
#     first_data = np.asarray(first_data)

#     # Check data dimensions
#     if len(first_data.shape) < 2:
#         raise ValueError(f"Data for key '{first_key}' must have at least 2 dimensions [time_steps, N_cells/N_nodes, ...]")

#     num_time_steps = first_data.shape[0]

#     # Calculate physical time values based on dt
#     time_values = [i * dt for i in range(num_time_steps)]

#     # List to store VTU file information for PVD
#     vtu_files = []

#     # Generate VTU files for each time step
#     for t in range(num_time_steps):
#         # Create filename for this time step
#         vtu_filename = f"{base_filename}_t{t:04d}.vtu"
#         vtu_filepath = os.path.join(output_dir, vtu_filename)

#         # Create PyVista UnstructuredGrid
#         grid = pv.UnstructuredGrid(cells, cell_types, points)

#         # Add data for this time step
#         for key, time_series_data in time_series_data_dict.items():
#             if time_series_data is None:
#                 continue

#             # Convert to numpy if needed
#             numpy_data = time_series_data.cpu().numpy() if isinstance(time_series_data, torch.Tensor) else np.asarray(time_series_data)

#             # Check dimensions
#             if len(numpy_data.shape) < 2:
#                 print(f"Warning: Skipping key '{key}' - data must have at least 2 dimensions")
#                 continue

#             if numpy_data.shape[0] != num_time_steps:
#                 print(f"Warning: Skipping key '{key}' - first dimension ({numpy_data.shape[0]}) doesn't match number of time steps ({num_time_steps})")
#                 continue

#             # Extract data for current time step
#             if len(numpy_data.shape) == 2:
#                 # Shape: [time_steps, N_cells/N_nodes]
#                 current_data = numpy_data[t, :]
#             elif len(numpy_data.shape) == 3:
#                 # Shape: [time_steps, N_cells/N_nodes, channels]
#                 current_data = numpy_data[t, :, :]
#             else:
#                 print(f"Warning: Skipping key '{key}' - unsupported data shape {numpy_data.shape}")
#                 continue

#             # Add to grid based on prefix
#             if key.startswith("node|"):
#                 grid.point_data[key] = current_data
#             elif key.startswith("cell|"):
#                 grid.cell_data[key] = current_data
#             else:
#                 print(f"Warning: Unknown data prefix for key '{key}', treating as point data")
#                 grid.point_data[key] = current_data

#         # Save VTU file
#         try:
#             grid.save(vtu_filepath)
#             vtu_files.append((vtu_filename, time_values[t]))
#             print(f"Saved time step {t}: {vtu_filepath}")
#         except Exception as e:
#             print(f"Error saving time step {t}: {e}")
#             return None

#     # Create PVD file for time series
#     pvd_filename = f"{base_filename}.pvd"
#     pvd_filepath = os.path.join(output_dir, pvd_filename)

#     try:
#         _create_pvd_file(pvd_filepath, vtu_files)
#         print(f"Created PVD file: {pvd_filepath}")
#         print(f"Time series exported successfully! Open {pvd_filepath} in ParaView to visualize the animation.")
#         return pvd_filepath
#     except Exception as e:
#         print(f"Error creating PVD file: {e}")
#         return None


# def export_time_series_vtu_simple(mesh_pos, pv_cells_node, pv_cells_type, time_series_data_dict,
#                                  output_dir, base_filename="time_series", dt=1.0):
#     """
#     Simplified time series export function that uses the existing export_full_mesh_vtu function.

#     This is a wrapper around export_full_mesh_vtu that handles time series data by calling
#     the existing function for each time step and creating a PVD file.

#     Args:
#         mesh_pos: Tensor of node positions.
#         pv_cells_node: PyVista format cell connectivity.
#         pv_cells_type: PyVista format cell types.
#         time_series_data_dict (dict): Dictionary containing time series data.
#                                      Keys should start with 'node|' or 'cell|'.
#                                      Values should have shape [time_steps, N_cells/N_nodes, channels].
#                                      For example: [500, 5000, 3] means 500 time steps.
#         output_dir (str): Directory to save the time series files.
#         base_filename (str): Base name for the output files. Default: "time_series".
#         dt (float): Physical time step size. Default: 1.0.
#                    Time values will be calculated as t = time_step_index * dt.
#                    For example, if dt=0.01, then time_step_0 -> t=0.0, time_step_1 -> t=0.01, etc.

#     Returns:
#         str: Path to the generated PVD file.
#     """

#     # Create output directory
#     os.makedirs(output_dir, exist_ok=True)

#     # Determine number of time steps
#     if not time_series_data_dict:
#         raise ValueError("time_series_data_dict cannot be empty")

#     first_key = list(time_series_data_dict.keys())[0]
#     first_data = time_series_data_dict[first_key]

#     # Convert to numpy if needed
#     if isinstance(first_data, torch.Tensor):
#         first_data = first_data.cpu().numpy()
#     first_data = np.asarray(first_data)

#     if len(first_data.shape) < 2:
#         raise ValueError(f"Data must have at least 2 dimensions [time_steps, N_cells/N_nodes, ...]")

#     num_time_steps = first_data.shape[0]

#     # Calculate physical time values based on dt
#     time_values = [i * dt for i in range(num_time_steps)]

#     # List to store VTU file information for PVD
#     vtu_files = []

#     # Generate VTU files for each time step
#     for t in range(num_time_steps):
#         # Create filename for this time step
#         vtu_filename = f"{base_filename}_t{t:04d}.vtu"
#         vtu_filepath = os.path.join(output_dir, vtu_filename)

#         # Prepare data dict for current time step
#         current_data_dict = {}
#         for key, time_series_data in time_series_data_dict.items():
#             if time_series_data is None:
#                 continue

#             # Convert to numpy if needed
#             numpy_data = time_series_data.cpu().numpy() if isinstance(time_series_data, torch.Tensor) else np.asarray(time_series_data)

#             # Check dimensions and extract current time step
#             if len(numpy_data.shape) == 2:
#                 # Shape: [time_steps, N_cells/N_nodes]
#                 current_data_dict[key] = numpy_data[t, :]
#             elif len(numpy_data.shape) == 3:
#                 # Shape: [time_steps, N_cells/N_nodes, channels]
#                 current_data_dict[key] = numpy_data[t, :, :]
#             else:
#                 print(f"Warning: Skipping key '{key}' - unsupported data shape {numpy_data.shape}")
#                 continue

#         # Use existing export_full_mesh_vtu function
#         success = export_full_mesh_vtu(
#             mesh_pos=mesh_pos,
#             pv_cells_node=pv_cells_node,
#             pv_cells_type=pv_cells_type,
#             save_file_path=vtu_filepath,
#             data_dict=current_data_dict
#         )

#         if success:
#             vtu_files.append((vtu_filename, time_values[t]))
#             print(f"Saved time step {t}: {vtu_filepath}")
#         else:
#             print(f"Error saving time step {t}: {vtu_filepath}")
#             return None

#     # Create PVD file for time series
#     pvd_filename = f"{base_filename}.pvd"
#     pvd_filepath = os.path.join(output_dir, pvd_filename)

#     try:
#         _create_pvd_file(pvd_filepath, vtu_files)
#         print(f"Created PVD file: {pvd_filepath}")
#         print(f"Time series exported successfully! Open {pvd_filepath} in ParaView to visualize the animation.")
#         return pvd_filepath
#     except Exception as e:
#         print(f"Error creating PVD file: {e}")
#         return None


# def _create_pvd_file(pvd_filepath, vtu_files):
#     """
#     Create a PVD file that describes the time series for ParaView.

#     Args:
#         pvd_filepath (str): Path to the PVD file to create.
#         vtu_files (list): List of tuples (vtu_filename, time_value).
#     """
#     with open(pvd_filepath, 'w') as f:
#         f.write('<?xml version="1.0"?>\n')
#         f.write('<VTKFile type="Collection" version="0.1" byte_order="LittleEndian">\n')
#         f.write('  <Collection>\n')

#         for vtu_filename, time_value in vtu_files:
#             f.write(f'    <DataSet timestep="{time_value}" group="" part="0" file="{vtu_filename}"/>\n')

#         f.write('  </Collection>\n')
#         f.write('</VTKFile>\n')


# def example_time_series_export():
#     """
#     Example function showing how to use the time series export functions.

#     This example creates dummy time series data and exports it to VTK format.
#     """
#     import torch

#     # Example mesh data (simple 2D quad mesh)
#     mesh_pos = torch.tensor([
#         [0.0, 0.0, 0.0],
#         [1.0, 0.0, 0.0],
#         [1.0, 1.0, 0.0],
#         [0.0, 1.0, 0.0],
#         [2.0, 0.0, 0.0],
#         [2.0, 1.0, 0.0]
#     ], dtype=torch.float32)

#     # Example cell connectivity (2 quads)
#     pv_cells_node = torch.tensor([4, 0, 1, 2, 3, 4, 1, 4, 5, 2], dtype=torch.int32)
#     pv_cells_type = torch.tensor([pv.CellType.QUAD, pv.CellType.QUAD], dtype=torch.int32)

#     # Example time series data
#     num_time_steps = 10
#     num_nodes = mesh_pos.shape[0]
#     num_cells = 2

#     # Create dummy time series data
#     time_series_data = {
#         'node|velocity_u': torch.sin(torch.linspace(0, 2*np.pi, num_time_steps).unsqueeze(1) +
#                                    torch.linspace(0, np.pi, num_nodes).unsqueeze(0)),  # [time_steps, num_nodes]
#         'node|velocity_v': torch.cos(torch.linspace(0, 2*np.pi, num_time_steps).unsqueeze(1) +
#                                    torch.linspace(0, np.pi, num_nodes).unsqueeze(0)),  # [time_steps, num_nodes]
#         'node|pressure': torch.sin(torch.linspace(0, 4*np.pi, num_time_steps).unsqueeze(1)) * \
#                         torch.ones(num_time_steps, num_nodes),  # [time_steps, num_nodes]
#         'cell|temperature': torch.linspace(0, 100, num_time_steps).unsqueeze(1) * \
#                            torch.ones(num_time_steps, num_cells)  # [time_steps, num_cells]
#     }

#     # Physical time step size
#     dt = 0.1  # Each time step represents 0.1 time units

#     # Export time series
#     output_dir = "./time_series_output"
#     pvd_file = export_time_series_vtu_simple(
#         mesh_pos=mesh_pos,
#         pv_cells_node=pv_cells_node,
#         pv_cells_type=pv_cells_type,
#         time_series_data_dict=time_series_data,
#         output_dir=output_dir,
#         base_filename="flow_simulation",
#         dt=dt
#     )

#     if pvd_file:
#         print(f"\nSuccess! Time series exported to: {pvd_file}")
#         print("To visualize:")
#         print("1. Open ParaView")
#         print(f"2. Load the file: {pvd_file}")
#         print("3. Click 'Apply' in the Properties panel")
#         print("4. Use the play button in the toolbar to animate through time steps")
#         print("5. You can color by different variables (velocity_u, velocity_v, pressure, temperature)")


if __name__ == "__main__":
    # 示例调用
    h5_file_path = (
        "/path/to/datasets/test.h5"
    )

    # Uncomment the line below to run the time series export example
    # example_time_series_export()
