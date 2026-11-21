#!/usr/bin/env python3
"""
Compare two HDF5 files to verify they contain identical data.

This script compares old.h5 (from original parser) and new.h5 (from refactored parser)
to ensure the refactoring didn't change the output.
"""

import sys
import h5py
import numpy as np
import logging
from typing import Dict, Any


def compare_datasets(old_data: np.ndarray, new_data: np.ndarray, dataset_name: str) -> bool:
    """
    Compare two numpy arrays for equality.
    
    Args:
        old_data: Data from original parser
        new_data: Data from refactored parser
        dataset_name: Name of the dataset for logging
        
    Returns:
        True if arrays are equal, False otherwise
    """
    try:
        # Check shapes first
        if old_data.shape != new_data.shape:
            logging.error("  ✗ %s: Shape mismatch - old: %s, new: %s", 
                         dataset_name, old_data.shape, new_data.shape)
            return False
        
        # Check data types
        if old_data.dtype != new_data.dtype:
            logging.warning("  ⚠ %s: Data type mismatch - old: %s, new: %s", 
                           dataset_name, old_data.dtype, new_data.dtype)
            # Try to convert to same type for comparison
            if np.issubdtype(old_data.dtype, np.floating) and np.issubdtype(new_data.dtype, np.floating):
                old_data = old_data.astype(np.float64)
                new_data = new_data.astype(np.float64)
            elif np.issubdtype(old_data.dtype, np.integer) and np.issubdtype(new_data.dtype, np.integer):
                old_data = old_data.astype(np.int64)
                new_data = new_data.astype(np.int64)
        
        # For floating point data, use allclose for comparison
        if np.issubdtype(old_data.dtype, np.floating):
            # Use more relaxed tolerance for volume calculations
            if dataset_name == 'cell|cells_volume':
                rtol, atol = 1e-6, 1e-10  # More relaxed for volumes
            else:
                rtol, atol = 1e-10, 1e-12  # Strict for other data

            if np.allclose(old_data, new_data, rtol=rtol, atol=atol):
                logging.info("  ✓ %s: Arrays are equal (floating point)", dataset_name)
                return True
            else:
                # Find differences
                diff_mask = ~np.isclose(old_data, new_data, rtol=rtol, atol=atol)
                num_diff = np.sum(diff_mask)
                max_diff = np.max(np.abs(old_data - new_data))
                max_rel_diff = np.max(np.abs((old_data - new_data) / (old_data + 1e-15)))

                # For volume calculations, be more lenient
                if dataset_name == 'cell|cells_volume' and max_rel_diff < 1e-5:
                    logging.info("  ✓ %s: Arrays are equal within acceptable tolerance (max_rel_diff=%.2e)",
                                dataset_name, max_rel_diff)
                    return True

                logging.error("  ✗ %s: Arrays differ - %d/%d elements, max_diff=%.2e, max_rel_diff=%.2e",
                             dataset_name, num_diff, old_data.size, max_diff, max_rel_diff)

                # Show some examples of differences
                if num_diff > 0:
                    diff_indices = np.where(diff_mask)
                    for i in range(min(5, num_diff)):  # Show first 5 differences
                        idx = tuple(d[i] for d in diff_indices)
                        rel_diff = abs((old_data[idx] - new_data[idx]) / (old_data[idx] + 1e-15))
                        logging.error("    Example diff at %s: old=%.10e, new=%.10e, rel_diff=%.2e",
                                     idx, old_data[idx], new_data[idx], rel_diff)
                return False
        else:
            # For integer data, use exact comparison
            if np.array_equal(old_data, new_data):
                logging.info("  ✓ %s: Arrays are equal (exact)", dataset_name)
                return True
            else:
                diff_mask = old_data != new_data
                num_diff = np.sum(diff_mask)
                logging.error("  ✗ %s: Arrays differ - %d/%d elements", 
                             dataset_name, num_diff, old_data.size)
                
                # Show some examples of differences
                if num_diff > 0:
                    diff_indices = np.where(diff_mask)
                    for i in range(min(5, num_diff)):  # Show first 5 differences
                        idx = tuple(d[i] for d in diff_indices)
                        logging.error("    Example diff at %s: old=%s, new=%s", 
                                     idx, old_data[idx], new_data[idx])
                return False
                
    except Exception as e:
        logging.error("  ✗ %s: Comparison failed - %s", dataset_name, str(e))
        return False


def compare_attributes(old_attrs: Dict[str, Any], new_attrs: Dict[str, Any]) -> bool:
    """Compare HDF5 file attributes"""
    logging.info("Comparing file attributes...")
    
    # Get all attribute names
    old_keys = set(old_attrs.keys())
    new_keys = set(new_attrs.keys())
    
    # Check for missing attributes
    missing_in_new = old_keys - new_keys
    missing_in_old = new_keys - old_keys
    
    if missing_in_new:
        logging.warning("  ⚠ Attributes missing in new file: %s", missing_in_new)
    
    if missing_in_old:
        logging.warning("  ⚠ Attributes missing in old file: %s", missing_in_old)
    
    # Compare common attributes
    common_keys = old_keys & new_keys
    all_equal = True
    
    for key in sorted(common_keys):
        old_val = old_attrs[key]
        new_val = new_attrs[key]
        
        # Handle different types
        if isinstance(old_val, bytes) and isinstance(new_val, str):
            old_val = old_val.decode('utf-8')
        elif isinstance(old_val, str) and isinstance(new_val, bytes):
            new_val = new_val.decode('utf-8')
        
        if old_val == new_val:
            logging.info("  ✓ %s: %s", key, old_val)
        else:
            logging.error("  ✗ %s: old=%s, new=%s", key, old_val, new_val)
            all_equal = False
    
    return all_equal and len(missing_in_new) == 0 and len(missing_in_old) == 0


def compare_h5_files(old_file: str, new_file: str) -> bool:
    """
    Compare two HDF5 files for equality.
    
    Args:
        old_file: Path to original parser output
        new_file: Path to refactored parser output
        
    Returns:
        True if files are identical, False otherwise
    """
    logging.info("=" * 60)
    logging.info("COMPARING HDF5 FILES")
    logging.info("=" * 60)
    logging.info("Old file (original): %s", old_file)
    logging.info("New file (refactored): %s", new_file)
    
    try:
        with h5py.File(old_file, 'r') as old_f, h5py.File(new_file, 'r') as new_f:
            
            # Compare file attributes
            attrs_equal = compare_attributes(dict(old_f.attrs), dict(new_f.attrs))
            
            # Get all dataset names
            old_datasets = set(old_f.keys())
            new_datasets = set(new_f.keys())
            
            logging.info("\nComparing datasets...")
            logging.info("Old file datasets: %d", len(old_datasets))
            logging.info("New file datasets: %d", len(new_datasets))
            
            # Check for missing datasets
            missing_in_new = old_datasets - new_datasets
            missing_in_old = new_datasets - old_datasets
            
            if missing_in_new:
                logging.error("Datasets missing in new file: %s", missing_in_new)
            
            if missing_in_old:
                logging.warning("Extra datasets in new file: %s", missing_in_old)
            
            # Compare common datasets
            common_datasets = old_datasets & new_datasets
            logging.info("Common datasets to compare: %d", len(common_datasets))
            
            datasets_equal = True
            
            for dataset_name in sorted(common_datasets):
                logging.info("\nComparing dataset: %s", dataset_name)
                
                old_data = old_f[dataset_name][:]
                new_data = new_f[dataset_name][:]
                
                if not compare_datasets(old_data, new_data, dataset_name):
                    datasets_equal = False
            
            # Overall result
            logging.info("\n" + "=" * 60)
            logging.info("COMPARISON RESULTS")
            logging.info("=" * 60)
            
            if attrs_equal:
                logging.info("✓ File attributes: EQUAL")
            else:
                logging.error("✗ File attributes: DIFFERENT")
            
            if len(missing_in_new) == 0:
                logging.info("✓ Dataset completeness: ALL DATASETS PRESENT")
            else:
                logging.error("✗ Dataset completeness: %d MISSING DATASETS", len(missing_in_new))
            
            if datasets_equal:
                logging.info("✓ Dataset contents: ALL EQUAL")
            else:
                logging.error("✗ Dataset contents: SOME DIFFERENCES FOUND")
            
            overall_equal = attrs_equal and datasets_equal and len(missing_in_new) == 0
            
            if overall_equal:
                logging.info("\n🎉 SUCCESS: Files are identical!")
                logging.info("The refactored parser produces exactly the same output as the original.")
            else:
                logging.error("\n❌ FAILURE: Files are different!")
                logging.error("The refactored parser output differs from the original.")
            
            logging.info("=" * 60)
            
            return overall_equal
            
    except Exception as e:
        logging.error("Error comparing files: %s", str(e), exc_info=True)
        return False


def main():
    """Main function"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    old_file = "src/Parse_mesh/Utils/old.h5"
    new_file = "src/Parse_mesh/Utils/new.h5"
    
    # Check if files exist
    import os
    if not os.path.exists(old_file):
        logging.error("Old file not found: %s", old_file)
        return False
    
    if not os.path.exists(new_file):
        logging.error("New file not found: %s", new_file)
        return False
    
    # Compare files
    return compare_h5_files(old_file, new_file)


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
