#!/usr/bin/env python3
"""
ParaView Python script to convert VTU/VTK files to GLTF format
This script:
1. Reads a VTU/VTK file containing cell-centered data
2. Interpolates cell data to point data
3. Exports specified field as GLTF format with color mapping

Usage:
    python convert_vtu_to_gltf.py <input_file> <field_name> [output_file]

Examples:
    python convert_vtu_to_gltf.py data.vtu cell_Pressure
    python convert_vtu_to_gltf.py data.vtu cell_Velocity output.gltf
"""

import os
import sys
import argparse

# Import ParaView modules
try:
    import paraview.simple as pvs
except ImportError as e:
    print(f"Error importing ParaView: {e}")
    print("Make sure ParaView is properly installed and the Python environment is activated")
    sys.exit(1)

def convert_to_gltf(input_file_path, field_name, output_dir=None, slice_z=None):
    """
    Convert VTU/VTK file to GLTF format with specified field visualization

    Args:
        input_file_path (str): Path to input VTU/VTK file
        field_name (str): Name of the field to visualize
        output_dir (str): Output directory path (optional, defaults to input file directory)
        slice_z (float): Z-axis height for slicing (optional, if None no slicing is performed)

    Returns:
        str: Path to the generated GLTF file
    """

    # Check if input file exists
    if not os.path.exists(input_file_path):
        raise FileNotFoundError(f"Input file not found: {input_file_path}")

    print(f"Loading file: {input_file_path}")
    print(f"Target field: {field_name}")

    # Clear any existing pipeline
    try:
        sources = pvs.GetSources()
        if sources:
            for source in sources.values():
                pvs.Delete(source)
    except:
        pass

    # Determine file type and load accordingly
    file_ext = os.path.splitext(input_file_path)[1].lower()

    if file_ext == '.vtu':
        reader = pvs.XMLUnstructuredGridReader(FileName=[input_file_path])
    elif file_ext == '.vtk':
        reader = pvs.LegacyVTKReader(FileNames=[input_file_path])
    else:
        raise ValueError(f"Unsupported file format: {file_ext}. Only .vtu and .vtk are supported.")

    # Update the reader to load data
    reader.UpdatePipeline()

    # Get information about the data
    data_info = reader.GetDataInformation()
    print(f"Number of points: {data_info.GetNumberOfPoints()}")
    print(f"Number of cells: {data_info.GetNumberOfCells()}")

    # Get cell data arrays
    cell_data_info = data_info.GetCellDataInformation()
    print(f"Number of cell data arrays: {cell_data_info.GetNumberOfArrays()}")

    available_cell_arrays = []
    for i in range(cell_data_info.GetNumberOfArrays()):
        array_info = cell_data_info.GetArrayInformation(i)
        array_name = array_info.GetName()
        num_components = array_info.GetNumberOfComponents()
        available_cell_arrays.append(array_name)
        print(f"  Cell array: {array_name} (components: {num_components})")

    # Get point data arrays
    point_data_info = data_info.GetPointDataInformation()
    print(f"Number of point data arrays: {point_data_info.GetNumberOfArrays()}")

    available_point_arrays = []
    for i in range(point_data_info.GetNumberOfArrays()):
        array_info = point_data_info.GetArrayInformation(i)
        array_name = array_info.GetName()
        num_components = array_info.GetNumberOfComponents()
        available_point_arrays.append(array_name)
        print(f"  Point array: {array_name} (components: {num_components})")

    # Check if the specified field exists in cell data or point data
    field_found = False
    field_location = None
    current_source = reader

    # Check if field exists in cell data
    if field_name in available_cell_arrays:
        field_found = True
        field_location = "CELLS"
        print(f"Field '{field_name}' found in cell data")

        # Apply Cell Data to Point Data filter to interpolate cell data to points
        print("Interpolating cell data to point data...")
        cell_to_point = pvs.CellDatatoPointData(Input=reader)
        cell_to_point.UpdatePipeline()
        current_source = cell_to_point
        field_location = "POINTS"  # After interpolation, data is at points

    # Check if field exists in point data
    elif field_name in available_point_arrays:
        field_found = True
        field_location = "POINTS"
        print(f"Field '{field_name}' found in point data")
        current_source = reader

    # If field not found, raise error
    if not field_found:
        available_fields = available_cell_arrays + available_point_arrays
        raise ValueError(f"Field '{field_name}' not found in the data. Available fields: {available_fields}")

    print(f"Using field '{field_name}' at {field_location} for visualization")

    # Apply slicing if slice_z is specified
    if slice_z is not None:
        print(f"Applying Z-axis slice at height: {slice_z}")

        # Create a slice filter
        slice_filter = pvs.Slice(Input=current_source)
        slice_filter.SliceType = 'Plane'
        slice_filter.SliceType.Origin = [0.0, 0.0, slice_z]
        slice_filter.SliceType.Normal = [0.0, 0.0, 1.0]  # Z-normal
        slice_filter.UpdatePipeline()

        current_source = slice_filter
        print(f"Slice created at Z = {slice_z}")

        # Get information about the sliced data
        slice_info = slice_filter.GetDataInformation()
        print(f"Slice - Number of points: {slice_info.GetNumberOfPoints()}")
        print(f"Slice - Number of cells: {slice_info.GetNumberOfCells()}")
    else:
        print("No slicing applied - using full 3D data")

    # Set up the output file path
    base_name = os.path.splitext(os.path.basename(input_file_path))[0]

    # Determine the base output directory
    if output_dir is None:
        base_output_dir = os.path.dirname(input_file_path)
    else:
        base_output_dir = output_dir

    # Create output directory named after the input file
    output_folder = os.path.join(base_output_dir, base_name)
    os.makedirs(output_folder, exist_ok=True)

    # Generate the GLTF file path
    safe_field_name = field_name.replace('/', '_').replace('\\', '_')

    # Add slice information to filename if slicing is applied
    if slice_z is not None:
        slice_suffix = f"_slice_z{slice_z}"
        output_gltf_path = os.path.join(output_folder, f"{base_name}_{safe_field_name}{slice_suffix}.gltf")
    else:
        output_gltf_path = os.path.join(output_folder, f"{base_name}_{safe_field_name}.gltf")

    print(f"Exporting to GLTF: {output_gltf_path}")
    print(f"Output directory: {output_folder}")

    # Export to GLTF format
    try:
        # Create a render view for export
        render_view = pvs.CreateView('RenderView')

        # Create representation
        display = pvs.Show(current_source, render_view)

        # Set the specified field as the active scalar
        pvs.ColorBy(display, (field_location, field_name))

        # Get the color transfer function
        color_lut = pvs.GetColorTransferFunction(field_name)

        # Apply a nice color map (Cool to Warm)
        color_lut.ApplyPreset('Cool to Warm', True)

        # Rescale to data range
        display.RescaleTransferFunctionToDataRange(True, False)

        # Reset camera to show the entire dataset
        render_view.ResetCamera()

        # Export as GLTF
        pvs.ExportView(output_gltf_path, view=render_view)

        print(f"Successfully exported GLTF file: {output_gltf_path}")

        # Check file size
        if os.path.exists(output_gltf_path):
            file_size = os.path.getsize(output_gltf_path)
            print(f"File size: {file_size} bytes")

        return output_gltf_path

    except Exception as e:
        print(f"Error during GLTF export: {e}")
        print("Trying alternative export method...")

        # Alternative: Save as VTK first
        vtk_output = output_gltf_path.replace('.gltf', '.vtk')
        try:
            pvs.SaveData(vtk_output, current_source)
            print(f"Saved as VTK file: {vtk_output}")
            print("Note: You may need to use external tools to convert VTK to GLTF")
            return vtk_output
        except Exception as vtk_error:
            print(f"VTK export also failed: {vtk_error}")
            raise e

def main():
    """Main function to run the conversion"""
    parser = argparse.ArgumentParser(
        description="Convert VTU/VTK files to GLTF format with field visualization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python convert_vtu_to_gltf.py data.vtu cell_Pressure
  python convert_vtu_to_gltf.py data.vtu cell_Velocity /path/to/output
  python convert_vtu_to_gltf.py simulation.vtk Temperature
  python convert_vtu_to_gltf.py data.vtu cell_Pressure --slice-z 0.4
  python convert_vtu_to_gltf.py data.vtu cell_Velocity /path/to/output --slice-z 1.2
        """
    )

    parser.add_argument('input_file',
                        nargs='?',
                        type=str,
                        default="Ref_code/convert_vtu_to_gltf/STDCT_from_OF_0_0.vtu",
                        help='Path to input VTU or VTK file')

    parser.add_argument('field_name',
                        nargs='?',
                        type=str,
                        default="T",
                        help='Name of the field to visualize (e.g.,T, cell_Pressure, cell_Velocity)')

    parser.add_argument('output_dir',
                       nargs='?',
                       default=None,
                       help='Output directory path (optional, defaults to input file directory)')

    parser.add_argument('--slice-z',
                       type=float,
                       default=0.21,
                       help='Z-axis height for slicing (optional, creates 2D slice at specified Z height)')

    args = parser.parse_args()
    
    # Check if input file exists
    if not os.path.exists(args.input_file):
        print(f"Error: Input file not found: {args.input_file}")
        sys.exit(1)

    try:
        print("=" * 60)
        print("VTU/VTK to GLTF Conversion Tool")
        print("=" * 60)
        print(f"Input file: {args.input_file}")
        print(f"Field name: {args.field_name}")
        if args.output_dir:
            print(f"Output directory: {args.output_dir}")
        if args.slice_z is not None:
            print(f"Z-slice height: {args.slice_z}")
        print("-" * 60)

        # Convert to GLTF
        output_file = convert_to_gltf(args.input_file, args.field_name, args.output_dir, args.slice_z)

        print("-" * 60)
        print("Conversion completed successfully!")
        print(f"Output file: {output_file}")
        print("=" * 60)

    except Exception as e:
        print(f"Error during conversion: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()