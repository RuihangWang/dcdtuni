"""Original storage-only kernels. Imported lazily ONLY for Kit CUDA conversion.

All integer nodes come from the shared CPP product. There is no interpolation,
voxelization, probing, slicing or physical solver in this carrier adapter.
"""
import warp as wp

@wp.kernel(enable_backward=False)
def store_nodes(volume:wp.uint64,values:wp.array(dtype=wp.float32),nx:int,ny:int):
 tid=wp.tid();i=tid%nx;j=(tid//nx)%ny;k=tid//(nx*ny)
 wp.volume_store_f(volume,i,j,k,values[tid])

@wp.kernel(enable_backward=False)
def read_nodes(volume:wp.uint64,values:wp.array(dtype=wp.float32),nx:int,ny:int):
 tid=wp.tid();i=tid%nx;j=(tid//nx)%ny;k=tid//(nx*ny)
 values[tid]=wp.volume_lookup_f(volume,i,j,k)
