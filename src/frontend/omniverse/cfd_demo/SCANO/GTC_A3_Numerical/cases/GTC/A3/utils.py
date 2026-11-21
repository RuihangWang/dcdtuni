#!/usr/bin/env python3
"""
Hangang Case Utilities

提供 Hangang 案例特定的工具函数, 包括边界patch解析等。
"""

import os
import h5py
import json
from typing import List, Optional
import logging
logger = logging.getLogger(__name__)


def parse_boundary_patches_from_h5(h5_path: str) -> List[str]:
    """
    从H5文件解析Alibaba-Shanghai案例的驱动边界patches

    驱动边界包括：
    - acu_supply_*: 空调供风口
    - server_inlet_*: 服务器进风口
    - server_outlet_*: 服务器出风口

    Args:
        h5_path: H5文件路径

    Returns:
        驱动边界patch名称列表

    Raises:
        FileNotFoundError: H5文件不存在
        KeyError: H5文件中没有patch_list属性

    Example:
        >>> h5_path = "dataset/case1/h5/case1.h5"
        >>> patches = parse_boundary_patches_from_h5(h5_path)
        >>> print(f"找到 {len(patches)} 个驱动边界patches")
    """
    if not os.path.exists(h5_path):
        raise FileNotFoundError(f"H5文件不存在: {h5_path}")

    with h5py.File(h5_path, 'r') as hf:
        if 'patch_list' not in hf.attrs:
            raise KeyError(f"H5文件中没有 'patch_list' 属性: {h5_path}")

        # 读取patch_list
        patch_list_json = hf.attrs['patch_list']
        if isinstance(patch_list_json, bytes):
            patch_list_json = patch_list_json.decode('utf-8')

        patch_dict = json.loads(patch_list_json)

    # 提取所有patch名称
    all_patch_names = list(patch_dict.keys())

    # 筛选驱动边界patches
    acu_supply_patches = [p for p in all_patch_names if p.startswith('acu_supply')]
    server_inlet_patches = [p for p in all_patch_names if p.startswith('server_inlet')]
    server_outlet_patches = [p for p in all_patch_names if p.startswith('server_outlet')]

    # 合并所有驱动边界
    boundary_patches = acu_supply_patches + server_inlet_patches + server_outlet_patches

    logger.info(f"📋 从 {os.path.basename(h5_path)} 解析驱动边界patches:")
    logger.info(f"   - acu_supply: {len(acu_supply_patches)}")
    logger.info(f"   - server_inlet: {len(server_inlet_patches)}")
    logger.info(f"   - server_outlet: {len(server_outlet_patches)}")
    logger.info(f"   - 总计: {len(boundary_patches)}")

    return boundary_patches


def parse_boundary_patches_from_dataset(
    data_root: str,
    case_name: Optional[str] = None
) -> List[str]:
    """
    从数据集目录解析Alibaba-Shanghai案例的驱动边界patches

    自动查找数据集中的第一个H5文件并解析边界patches。
    假设所有case的边界patches命名规则相同。

    Args:
        data_root: 数据集根目录
        case_name: 可选的case名称，如果不指定则使用第一个找到的case

    Returns:
        驱动边界patch名称列表

    Raises:
        FileNotFoundError: 找不到H5文件

    Example:
        >>> data_root = "dataset/Aliabab-Shanghai-Calibration"
        >>> patches = parse_boundary_patches_from_dataset(data_root)
        >>> print(f"找到 {len(patches)} 个驱动边界patches")
    """
    if not os.path.exists(data_root):
        raise FileNotFoundError(f"数据集目录不存在: {data_root}")

    # 如果指定了case_name，直接使用
    if case_name:
        h5_path = os.path.join(data_root, case_name, "h5", f"{case_name}.h5")
        if not os.path.exists(h5_path):
            raise FileNotFoundError(f"H5文件不存在: {h5_path}")
        return parse_boundary_patches_from_h5(h5_path)

    # 否则查找第一个H5文件
    for case_dir in sorted(os.listdir(data_root)):
        case_path = os.path.join(data_root, case_dir)
        if not os.path.isdir(case_path):
            continue

        h5_dir = os.path.join(case_path, "h5")
        if not os.path.exists(h5_dir):
            continue

        h5_files = [f for f in os.listdir(h5_dir) if f.endswith('.h5')]
        if h5_files:
            h5_path = os.path.join(h5_dir, h5_files[0])
            logger.info(f"🔍 使用 {case_dir} 的H5文件解析边界patches")
            return parse_boundary_patches_from_h5(h5_path)

    raise FileNotFoundError(f"在 {data_root} 中找不到任何H5文件")


def get_boundary_patches(
    data_root: Optional[str] = None,
    h5_path: Optional[str] = None,
    case_name: Optional[str] = None
) -> List[str]:
    """
    获取Alibaba-Shanghai案例的驱动边界patches（便捷函数）

    根据提供的参数自动选择解析方式：
    - 如果提供 h5_path，直接从H5文件解析
    - 如果提供 data_root，从数据集目录解析

    Args:
        data_root: 数据集根目录（可选）
        h5_path: H5文件路径（可选）
        case_name: case名称（可选，配合data_root使用）

    Returns:
        驱动边界patch名称列表

    Raises:
        ValueError: 参数不足

    Example:
        >>> # 方式1: 从H5文件解析
        >>> patches = get_boundary_patches(h5_path="dataset/case1/h5/case1.h5")

        >>> # 方式2: 从数据集目录解析
        >>> patches = get_boundary_patches(data_root="dataset/Aliabab-Shanghai-Calibration")

        >>> # 方式3: 从数据集目录解析指定case
        >>> patches = get_boundary_patches(
        ...     data_root="dataset/Aliabab-Shanghai-Calibration",
        ...     case_name="simulation-24"
        ... )
    """
    if h5_path:
        return parse_boundary_patches_from_h5(h5_path)
    elif data_root:
        return parse_boundary_patches_from_dataset(data_root, case_name)
    else:
        raise ValueError("必须提供 h5_path 或 data_root 参数")


if __name__ == "__main__":
    """测试边界patch解析功能"""
    import sys

    # 测试1: 从数据集目录解析
    print("=" * 80)
    print("测试1: 从数据集目录解析边界patches")
    print("=" * 80)

    # 根据当前工作目录调整路径
    if os.path.basename(os.getcwd()) == "Alibaba-Shanghai":
        data_root = "dataset/Aliabab-Shanghai-Calibration"
    else:
        data_root = "cases/Alibaba-Shanghai/dataset/Aliabab-Shanghai-Calibration"
    if os.path.exists(data_root):
        try:
            patches = parse_boundary_patches_from_dataset(data_root)
            print(f"\n✅ 成功解析 {len(patches)} 个驱动边界patches")
            print(f"\n前10个patches:")
            for i, p in enumerate(patches[:10], 1):
                print(f"   {i}. {p}")
        except Exception as e:
            print(f"\n❌ 解析失败: {e}")
            sys.exit(1)
    else:
        print(f"\n⚠️  数据集目录不存在: {data_root}")

    # 测试2: 从指定case解析
    print("\n" + "=" * 80)
    print("测试2: 从指定case解析边界patches")
    print("=" * 80)

    if os.path.exists(data_root):
        try:
            patches = parse_boundary_patches_from_dataset(data_root, case_name="simulation-24")
            print(f"\n✅ 成功解析 {len(patches)} 个驱动边界patches")
        except Exception as e:
            print(f"\n❌ 解析失败: {e}")
            sys.exit(1)

    # 测试3: 使用便捷函数
    print("\n" + "=" * 80)
    print("测试3: 使用便捷函数 get_boundary_patches()")
    print("=" * 80)

    if os.path.exists(data_root):
        try:
            patches = get_boundary_patches(data_root=data_root)
            print(f"\n✅ 成功解析 {len(patches)} 个驱动边界patches")

            # 统计各类型数量
            acu_supply = [p for p in patches if p.startswith('acu_supply')]
            server_inlet = [p for p in patches if p.startswith('server_inlet')]
            server_outlet = [p for p in patches if p.startswith('server_outlet')]

            print(f"\n📊 边界类型统计:")
            print(f"   - acu_supply: {len(acu_supply)}")
            print(f"   - server_inlet: {len(server_inlet)}")
            print(f"   - server_outlet: {len(server_outlet)}")
            print(f"   - 总计: {len(patches)}")

            print(f"\n🎉 所有测试通过！")
        except Exception as e:
            print(f"\n❌ 解析失败: {e}")
            sys.exit(1)