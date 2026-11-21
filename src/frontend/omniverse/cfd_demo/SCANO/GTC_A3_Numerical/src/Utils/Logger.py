"""
统一的Logger系统 - 支持分布式训练、文件日志、TensorBoard、CSV记录

功能特性：
1. 分布式训练支持：自动显示Rank信息
2. 文件信息追踪：显示module:function:line
3. 多种输出方式：Console + File + TensorBoard + CSV
4. 简洁的API：对外暴露Logger类

使用示例：
    # 在训练入口文件（推荐）
    from Utils.Logger import Logger
    logger = Logger(
        name="TransFVGN",
        head="Logger",
        params=config,
        use_tensorboard=True,
        rank=0  # 分布式训练的rank
    )
    logger.info("Training started")
    logger.log_scalar("loss", 0.001, step=100)

    # 在其他模块
    import logging
    logger = logging.getLogger(__name__)
    logger.info("Loading mesh...")  # 自动输出到console和文件
"""

import os
import sys
import datetime as dt
from natsort import natsorted
import json
import shutil
import fnmatch
import csv
import logging
import yaml
from typing import Dict, Any, Optional, Union, List, Tuple

try:
    from torch.utils.tensorboard import SummaryWriter
    TENSORBOARD_AVAILABLE = True
except ImportError:
    TENSORBOARD_AVAILABLE = False

DEFAULT_SKIP_DIRS = [
    '__pycache__',
    '.ipynb_checkpoints',
    '.git',
    '.idea',
    '.vscode',
    'Logger',   # avoid recursive backup of previous logs
    'dataset',  # datasets can be large; configs already capture paths
]

DEFAULT_FILE_WHITELIST = [
    "*.py",
    "*.sh",
    "*.yaml",
    "*.json",
]


# ============================================================================
# LOGGER CLASS - 实验管理 + 日志记录
# ============================================================================

class Logger:
    """
    训练日志记录器（实验管理 + 全局logging配置）

    功能：
    - 配置全局模块级logging系统（支持分布式训练的rank信息）
    - 使用Python logging包记录日志（支持多级别）
    - 保存和加载模型状态
    - 记录残差数据到CSV（带缓冲区）
    - TensorBoard支持（可选）
    - 可选：复制源代码到实验目录

    日志格式：
    - Rank 0 (主进程): YYYY-MM-DD HH:MM:SS | LEVEL | module:function:line - message
    - Rank N (其他进程): [Rank N] YYYY-MM-DD HH:MM:SS | LEVEL | module:function:line - message

    使用示例：
        # 在训练入口文件
        logger = Logger(
            name="TransFVGN",
            head="Logger",
            params=config,
            use_tensorboard=True,
            rank=0
        )
        logger.info("Training started")
        logger.log_scalar("loss", 0.001, step=100)

        # 在其他模块
        import logging
        logger = logging.getLogger(__name__)
        logger.info("Loading mesh...")  # 自动输出到console和文件
    """

    def __init__(
        self,
        name: str,
        head: str = "Logger",
        datetime: Optional[str] = None,
        use_csv: bool = False,
        use_tensorboard: bool = True,
        params: Optional[Any] = None,
        saving_path: Optional[str] = None,
        copy_code: bool = False,
        backup_paths: Optional[List[str]] = None,
        skip_dirs: Optional[List[str]] = None,
        seed: Optional[int] = None,
        log_level: str = "INFO",
        rank: int = 0,
    ):
        """
        初始化Logger

        Args:
            name: 实验名称（例如模型名）
            head: 根目录名（默认"Logger"）
            datetime: 指定时间戳（默认当前时间）
            use_csv: 是否使用CSV记录（保留参数，向后兼容）
            use_tensorboard: 是否使用TensorBoard记录
            params: 参数对象（用于保存配置）
            saving_path: 指定保存路径（默认自动生成）
            copy_code: 是否复制源代码
            seed: 随机种子（用于创建子目录）
            log_level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            rank: 分布式训练的进程rank (默认0，主进程)
        """
        self.head = head
        self.name = name
        self.params = params
        self.rank = rank
        self.log_item = {}
        self.use_tensorboard = use_tensorboard and TENSORBOARD_AVAILABLE
        self.backup_paths = backup_paths
        self.skip_dirs = skip_dirs if skip_dirs is not None else DEFAULT_SKIP_DIRS.copy()

        # 设置时间戳
        current_time = dt.datetime.now()
        self.datetime = datetime if datetime else current_time.strftime("%Y-%m-%d-%H-%M-%S")

        # 检查系统时间是否合理（防止时间未同步）
        if not datetime:  # 只在自动生成时间戳时检查
            current_year = current_time.year
            if current_year > 2025 or current_year < 2020:
                logging.getLogger(__name__).warning(
                    "⚠️  WARNING: System time may be incorrect! Current year: %s", current_year
                )
                logging.getLogger(__name__).warning(
                    "   Please check system time with 'date' command and sync with NTP if needed."
                )
                logging.getLogger(__name__).warning("   Current timestamp: %s", self.datetime)

        # 设置保存路径
        if saving_path is not None:
            self.saving_path = saving_path
        else:
            # 如果head不是绝对路径,保持为相对路径(相对于当前工作目录)
            # 这样可以确保从任何位置运行脚本时,Logger都会在正确的位置创建
            self.saving_path = os.path.join(head, name, self.datetime)

        os.makedirs(self.saving_path, exist_ok=True)

        # 配置logging系统（先配置logging，这样后续操作可以记录日志）
        self._setup_logging(log_level, rank)

        # 复制源代码（如果需要）
        if copy_code and self.rank == 0:
            self._copy_source_code(seed)

        # 初始化残差记录系统
        self._init_residual_logging()

        # TensorBoard writer初始化
        self.tensorboard_writer = None
        if self.use_tensorboard:
            self._setup_tensorboard()

    # ==================== Logging代理方法（向后兼容 + 便捷使用） ====================

    def debug(self, msg, *args, **kwargs):
        """记录DEBUG级别日志"""
        self.logger.debug(msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        """记录INFO级别日志"""
        self.logger.info(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        """记录WARNING级别日志"""
        self.logger.warning(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        """记录ERROR级别日志"""
        self.logger.error(msg, *args, **kwargs)

    def critical(self, msg, *args, **kwargs):
        """记录CRITICAL级别日志"""
        self.logger.critical(msg, *args, **kwargs)

    def _setup_logging(self, log_level: str, rank: int):
        """
        配置全局logging系统，使所有模块都能使用logging

        特性：
        1. 配置根logger（全局生效）
        2. 所有模块使用 logging.getLogger(__name__) 都会自动输出到console和文件
        3. 自动抑制第三方库的verbose日志
        4. 支持分布式训练的rank信息

        统一格式: [Rank X] YYYY-MM-DD HH:MM:SS - module_name - LEVEL - message

        Args:
            log_level: 日志级别字符串 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            rank: 分布式训练的进程rank
        """
        numeric_level = getattr(logging, log_level.upper(), logging.INFO)

        # ========== 配置全局根logger ==========
        root_logger = logging.getLogger()
        root_logger.setLevel(numeric_level)

        # 清除已有的handlers（避免重复）
        if root_logger.handlers:
            root_logger.handlers.clear()

        # 统一格式: [Rank X] date - module - level - message
        formatter = logging.Formatter(
            f'[Rank {rank}] %(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

        # File handler
        log_file = os.path.join(self.saving_path, 'training.log')
        stdstream_capture_path = os.environ.get("GTC_STDSTREAM_LOG_PATH")
        if not stdstream_capture_path or os.path.abspath(stdstream_capture_path) != os.path.abspath(log_file):
            file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
            file_handler.setLevel(numeric_level)
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)

        # ========== 抑制第三方库的verbose日志 ==========
        third_party_libs = ['matplotlib', 'PIL', 'h5py', 'urllib3', 'numba', 'torch', 'tensorboard']
        for lib in third_party_libs:
            logging.getLogger(lib).setLevel(logging.WARNING)

        # ========== 创建当前实例的logger（用于向后兼容） ==========
        logger_name = f"{self.head}.{self.name}"
        self.logger = logging.getLogger(logger_name)

        # 记录初始化信息
        self.logger.info(f"Logger initialized at level {log_level}")
        self.logger.info(f"Saving path: {self.saving_path}")
        self.logger.info(f"Log file: {log_file}")
        if rank > 0:
            self.logger.info(f"Distributed training rank: {rank}")
        self.logger.info("=" * 60)

    def _get_project_root(self) -> str:
        """Return project root path based on current file location"""
        src_utils_dir = os.path.dirname(os.path.abspath(__file__))
        src_dir = os.path.dirname(src_utils_dir)
        return os.path.dirname(src_dir)

    def _deduplicate_paths(self, paths: List[str]) -> List[str]:
        """Return list with absolute, de-duplicated paths while preserving order"""
        seen = set()
        unique_paths = []
        for path in paths:
            abs_path = os.path.abspath(path)
            if abs_path in seen:
                continue
            seen.add(abs_path)
            unique_paths.append(abs_path)
        return unique_paths

    def _copy_source_code(self, seed: Optional[int] = None):
        """
        复制源代码到实验目录（保留文件夹结构，只复制白名单中的文件类型）

        Args:
            seed: 随机种子（如果提供，创建seed子目录）
        """
        if seed is not None:
            os.makedirs(os.path.join(self.saving_path, f"seed_{seed}"), exist_ok=True)

        # Determine project root (src/Utils/Logger.py -> src/Utils -> src -> project_root)
        project_root = self._get_project_root()
        src_dir = os.path.join(project_root, "src")

        # Auto-include commonly used code roots to keep backups runnable
        auto_paths = self._collect_auto_backup_paths(project_root, src_dir)

        # Merge user-provided paths with auto paths, keeping order and uniqueness
        provided_paths = self.backup_paths if self.backup_paths else []
        paths_to_backup = self._deduplicate_paths(provided_paths + auto_paths)

        # 白名单：只复制这些文件类型
        file_whitelist = list(DEFAULT_FILE_WHITELIST)

        for path in paths_to_backup:
            abs_path = os.path.abspath(path)

            if not os.path.exists(abs_path):
                self.logger.warning(f"Skip backup for missing path: {abs_path}")
                continue
            
            # Calculate relative path from project root to preserve structure
            try:
                rel_path = os.path.relpath(abs_path, project_root)
            except ValueError:
                # Fallback for paths on different drives etc.
                rel_path = os.path.basename(abs_path)
            
            # If path is outside project root (starts with ..), just use basename
            if rel_path.startswith(".."):
                target_dir = os.path.join(self.saving_path, os.path.basename(abs_path))
            else:
                target_dir = os.path.join(self.saving_path, rel_path)

            self._copy_files_with_whitelist(abs_path, target_dir, file_whitelist)
            
        self.logger.info(f"Code backup completed. Paths: {paths_to_backup}")
        self.logger.info(f"Backup location: {self.saving_path}")
        
        # Post-process configuration files
        self._post_process_configs()

    def _post_process_configs(self):
        """
        Post-process backed-up configuration files to make them self-contained.
        - Convert dataset paths to absolute paths
        - Set checkpoint path to the local best.state
        """
        # Gather yaml configs under the backup directory
        config_files = [
            os.path.join(root, fname)
            for root, _, files in os.walk(self.saving_path)
            for fname in files
            if fname.endswith(".yaml")
        ]
        
        if not config_files:
            return

        project_root = self._get_project_root()

        def _to_abs(path_value: Optional[str]) -> Tuple[Optional[str], bool]:
            """Convert relative path to absolute based on project root"""
            if not path_value or os.path.isabs(path_value):
                return path_value, False
            abs_path = os.path.abspath(os.path.join(project_root, path_value))
            return abs_path, True

        for config_path in config_files:
            try:
                with open(config_path, 'r') as f:
                    config = yaml.safe_load(f)

                if not isinstance(config, dict):
                    self.logger.warning(f"Skip config post-process (unexpected format): {config_path}")
                    continue

                modified = False
                
                # 1. Resolve dataset_path to absolute path
                dataset_config = config.get('dataset_config', {})
                if isinstance(dataset_config, dict):
                    if 'data_root' in dataset_config:
                        new_path, changed = _to_abs(dataset_config['data_root'])
                        if changed:
                            dataset_config['data_root'] = new_path
                            config['dataset_config'] = dataset_config
                            modified = True
                else:
                    self.logger.warning(f"dataset_config is not a dict in {config_path}")

                # Check training config
                training_cfg = config.get('training_config', {})
                if isinstance(training_cfg, dict):
                    if 'dataset_path' in training_cfg:
                        new_path, changed = _to_abs(training_cfg['dataset_path'])
                        if changed:
                            training_cfg['dataset_path'] = new_path
                            config['training_config'] = training_cfg
                            modified = True
                else:
                    self.logger.warning(f"training_config is not a dict in {config_path}")

                # Check inference config
                if 'inference_config' in config:
                    inference_cfg = config.get('inference_config', {}) or {}
                    if isinstance(inference_cfg, dict):
                        # dataset_path
                        if 'dataset_path' in inference_cfg:
                            ds_path = inference_cfg['dataset_path']
                            new_path, changed = _to_abs(ds_path)
                            if changed:
                                inference_cfg['dataset_path'] = new_path
                                modified = True
                        
                        # meta_json_path
                        if 'meta_json_path' in inference_cfg:
                            meta_path = inference_cfg['meta_json_path']
                            new_path, changed = _to_abs(meta_path)
                            if changed:
                                inference_cfg['meta_json_path'] = new_path
                                modified = True

                        # keep config dict reference updated
                        config['inference_config'] = inference_cfg
                                
                        # 2. Set checkpoint_path to local best.state
                        # We assume the standard structure: {saving_path}/states/best.state
                        # This path should be absolute for the inference script to find it easily
                        best_state_path = os.path.join(self.saving_path, "states", "best.state")
                        if inference_cfg.get('checkpoint_path') != best_state_path:
                            inference_cfg['checkpoint_path'] = best_state_path
                            config['inference_config'] = inference_cfg
                            modified = True
                    else:
                        self.logger.warning(f"inference_config is not a dict in {config_path}")
                
                if modified:
                    with open(config_path, 'w') as f:
                        # Preserve original key order for readability
                        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
                    self.logger.info(f"Smart config backup: Updated {config_path}")
                    
            except Exception as e:
                self.logger.warning(f"Failed to post-process config {config_path}: {e}")

    def _collect_auto_backup_paths(self, project_root: str, src_dir: str) -> List[str]:
        """
        Collect default backup paths:
        - src
        - all case folders under cases/ (excluding hidden/Logger)
        - their configs
        - top-level configs (if any)
        """
        paths = [src_dir]

        cases_root = os.path.join(project_root, "cases")
        if os.path.isdir(cases_root):
            for item in sorted(os.listdir(cases_root)):
                case_dir = os.path.join(cases_root, item)
                if not os.path.isdir(case_dir):
                    continue
                if item.startswith('.') or item == 'Logger':
                    continue
                paths.append(case_dir)
                case_configs = os.path.join(case_dir, "configs")
                if os.path.isdir(case_configs):
                    paths.append(case_configs)

        top_configs = os.path.join(project_root, "configs")
        if os.path.isdir(top_configs):
            paths.append(top_configs)

        return paths

    def _copy_files_with_whitelist(self, src_dir: str, dst_dir: str, whitelist: list):
        """
        根据白名单递归复制文件（保留原始文件夹结构）

        类似.gitignore的白名单机制：
        - 只复制匹配白名单模式的文件（如 *.py, *.sh）
        - 递归遍历所有子目录
        - 保留原始的文件夹结构
        - 除白名单中的文件外，其他文件都不复制

        Args:
            src_dir: 源目录
            dst_dir: 目标目录
            whitelist: 白名单列表（支持glob模式，如 ["*.py", "*.sh"]）
        """
        if not os.path.exists(dst_dir):
            os.makedirs(dst_dir, exist_ok=True)

        for item in os.listdir(src_dir):
            src_path = os.path.join(src_dir, item)

            # 递归处理子目录
            if os.path.isdir(src_path):
                if self._should_skip_dir(item, src_path):
                    continue

                # 递归复制子目录（保留文件夹结构）
                dst_subdir = os.path.join(dst_dir, item)
                self._copy_files_with_whitelist(src_path, dst_subdir, whitelist)

                # 如果子目录为空（没有匹配的文件），删除它
                if os.path.exists(dst_subdir) and not os.listdir(dst_subdir):
                    os.rmdir(dst_subdir)

            # 复制文件（只复制匹配白名单的文件）
            elif os.path.isfile(src_path):
                for pattern in whitelist:
                    if fnmatch.fnmatch(item, pattern):
                        shutil.copy2(src_path, os.path.join(dst_dir, item))
                        break

    def _should_skip_dir(self, name: str, abs_path: str) -> bool:
        """Return True if directory should not be traversed for backup"""
        if name.startswith('.') or name in ('__pycache__',):
            return True
        if self.skip_dirs and name in self.skip_dirs:
            return True
        # Avoid recursion into the saving path itself
        try:
            if os.path.samefile(abs_path, self.saving_path):
                return True
        except FileNotFoundError:
            pass
        return False

    def _init_residual_logging(self):
        """初始化残差记录系统"""
        self.residual_buffer = []
        self.residual_buffer_size = 1000
        self.residual_csv_path = os.path.join(self.saving_path, "residuals.csv")
        self.residual_headers = None
        self.residual_headers_written = False

    def _setup_tensorboard(self):
        """Setup TensorBoard writer"""
        if not TENSORBOARD_AVAILABLE:
            self.logger.warning("Warning: TensorBoard not available, skipping TensorBoard setup")
            return

        tensorboard_dir = f"{self.saving_path}/tensorboard"
        os.makedirs(tensorboard_dir, exist_ok=True)
        self.tensorboard_writer = SummaryWriter(tensorboard_dir)
        self.logger.info("✅ TensorBoard logging enabled: %s", tensorboard_dir)

    def log_scalar(self, tag: str, value: Union[int, float], step: int):
        """
        Log a scalar value to TensorBoard

        Args:
            tag: The tag name for the scalar
            value: The scalar value to log
            step: The step/iteration number
        """
        if self.tensorboard_writer:
            self.tensorboard_writer.add_scalar(tag, value, step)

    def log_scalars(self, main_tag: str, tag_scalar_dict: Dict[str, Union[int, float]], step: int):
        """
        Log multiple scalars to TensorBoard

        Args:
            main_tag: The main tag name
            tag_scalar_dict: Dictionary of tag-value pairs
            step: The step/iteration number
        """
        if self.tensorboard_writer:
            self.tensorboard_writer.add_scalars(main_tag, tag_scalar_dict, step)

    def log_metrics(self, metrics: Dict[str, Union[int, float]], step: int, prefix: str = ""):
        """
        Log multiple metrics to TensorBoard with optional prefix

        Args:
            metrics: Dictionary of metric name-value pairs
            step: The step/iteration number
            prefix: Optional prefix for metric names
        """
        if not self.tensorboard_writer:
            return

        for key, value in metrics.items():
            if isinstance(value, (int, float)):
                tag = f"{prefix}/{key}" if prefix else key
                self.tensorboard_writer.add_scalar(tag, value, step)

    def log_histogram(self, tag: str, values, step: int):
        """
        Log a histogram to TensorBoard

        Args:
            tag: The tag name for the histogram
            values: The values to create histogram from
            step: The step/iteration number
        """
        if self.tensorboard_writer:
            self.tensorboard_writer.add_histogram(tag, values, step)

    def flush_tensorboard(self):
        """Flush TensorBoard writer"""
        if self.tensorboard_writer:
            self.tensorboard_writer.flush()

    def close_tensorboard(self):
        """Close TensorBoard writer"""
        if self.tensorboard_writer:
            self.tensorboard_writer.close()
            self.tensorboard_writer = None

    def save_state(self, model, optimizer, scheduler, index="final"):
        """
        saves state of model and optimizer
        :model: model to save (if list: save multiple models)
        :optimizer: optimizer (if list: save multiple optimizers)
        :index: index of state to save (e.g. specific epoch)
        """
        os.makedirs(self.saving_path + "/states", exist_ok=True)
        path = self.saving_path + "/states"

        # Save parameters if available
        params_dict = {}
        if self.params is not None:
            if hasattr(self.params, '__dict__'):
                params_dict = vars(self.params)
            elif isinstance(self.params, dict):
                params_dict = self.params
            else:
                # Try to convert to dict if possible
                try:
                    params_dict = dict(self.params)
                except (TypeError, ValueError):
                    params_dict = {"params": str(self.params)}

        with open(path + "/commandline_args.json", "wt") as f:
            json.dump(params_dict, f, indent=4, ensure_ascii=False)

        # Save model using appropriate method
        checkpoint_path = path + "/{}.state".format(index)

        if hasattr(model, 'save_model'):
            # Use Simulator's save_model method
            model.save_model(checkpoint_path, optimizer=optimizer, scheduler=scheduler)
        elif hasattr(model, 'save_checkpoint'):
            # Use legacy save_checkpoint method
            model.save_checkpoint(checkpoint_path, optimizer, scheduler)
        else:
            # Fallback: save using torch.save
            import torch
            checkpoint = {
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict() if optimizer else None,
                'scheduler_state_dict': scheduler.state_dict() if scheduler else None,
            }
            torch.save(checkpoint, checkpoint_path)

        return checkpoint_path

    def load_state(
        self,
        model,
        optimizer,
        scheduler,
        datetime=None,
        index=None,
        device=None,
    ):
        """
        loads state of model and optimizer
        :model: model to load (if list: load multiple models)
        :optimizer: optimizer to load (if list: load multiple optimizers; if None: don't load)
        :datetime: date and time from run to load (if None: take latest folder)
        :index: index of state to load (e.g. specific epoch) (if None: take latest index)
        :continue_datetime: flag whether to continue on this run. Default: False
        :return: datetime, index (helpful, if datetime / index wasn't given)
        """

        if datetime is None:
            for _, dirs, _ in os.walk("{}/{}/".format(self.head, self.name)):
                datetime = sorted(dirs)[-1]
                if datetime == self.datetime:
                    datetime = sorted(dirs)[-2]
                break

        if index is None:
            for _, _, files in os.walk(
                "{}/{}/{}/states/".format(self.head, self.name, datetime)
            ):
                index = os.path.splitext(natsorted(files)[-1])[0]
                break
            
        self.saving_path = "{}/{}/{}".format(self.head, self.name, datetime)
        
        path = "{}/{}/{}/states/{}.state".format(self.head, self.name, datetime, index)

        model.load_checkpoint(
            optimizer=optimizer, scheduler=scheduler, ckpdir=path, device=device
        )

        return datetime, index

    def load_logger(self, datetime=None, load=False, saving_path=None):
        """
        copy older tensorboard logger to new dir
        :datetime: date and time from run to load (if None: take latest folder)
        """

        if datetime is None:
            for _, dirs, _ in os.walk("{}/{}/".format(self.head, self.name)):
                datetime = sorted(dirs)[-1]
                if datetime == self.datetime:
                    datetime = sorted(dirs)[-2]
                break
     
        if load:
            cwd = os.getcwd()
            path = "{}/{0}/{1}/tensorboard/".format(self.head, self.name, datetime)
            for _, _, files in os.walk(path):
                for file in files:
                    older_tensorboard_n = file
                    older_tensorboard = path + older_tensorboard_n

                    newer_tensorboard = (
                        cwd
                        + "/{0}/{1}/{2}/tensorboard/".format(
                            self.head, self.name, self.datetime
                        )
                        + older_tensorboard_n
                    )
                    shutil.copyfile(older_tensorboard, newer_tensorboard)
                break

            if os.path.exists(newer_tensorboard):
                self.logger.info(
                    "older tensorboard aleady been copied to %s",
                    newer_tensorboard,
                )

    def log_residuals(self, **kwargs):
        """
        记录残差数据到缓冲区，当缓冲区满时写入CSV文件
        支持任意key-value对的写入

        Args:
            **kwargs: 任意数量的键值对，例如:
                iteration=100, loss_total=0.001, loss_momentum_L2=0.002, etc.

        Example:
            logger.log_residuals(iteration=100, loss_total=0.001, loss_momentum_L2=0.002, loss_cont_L2=0.003)
        """
        if not kwargs:
            return

        # 第一次调用时确定表头顺序
        if self.residual_headers is None:
            # 确保iteration/x作为第一列
            if 'iteration' in kwargs:
                self.residual_headers = ['iteration'] + [k for k in sorted(kwargs.keys()) if k != 'iteration']
            elif 'x' in kwargs:
                self.residual_headers = ['x'] + [k for k in sorted(kwargs.keys()) if k != 'x']
            else:
                self.residual_headers = sorted(kwargs.keys())

        # 按照表头顺序创建数据行
        row_data = []
        for header in self.residual_headers:
            if header in kwargs:
                row_data.append(kwargs[header])
            else:
                row_data.append(0.0)  # 缺失值用0填充

        self.residual_buffer.append(row_data)

        # 当缓冲区达到指定大小时，写入文件
        if len(self.residual_buffer) >= self.residual_buffer_size:
            self._flush_residual_buffer()

    def _flush_residual_buffer(self):
        """将缓冲区数据写入CSV文件并清空缓冲区"""
        if not self.residual_buffer or self.residual_headers is None:
            return

        # 确保保存目录存在
        os.makedirs(os.path.dirname(self.residual_csv_path), exist_ok=True)

        # 写入CSV文件
        mode = 'w' if not self.residual_headers_written else 'a'
        with open(self.residual_csv_path, mode, newline='') as csvfile:
            writer = csv.writer(csvfile)

            # 如果是第一次写入，先写入表头
            if not self.residual_headers_written:
                writer.writerow(self.residual_headers)
                self.residual_headers_written = True

            # 写入所有缓冲的数据
            writer.writerows(self.residual_buffer)

        # 记录写入的行数
        written_rows = len(self.residual_buffer)
        # 清空缓冲区
        self.residual_buffer.clear()
        self.logger.info("✅ 残差数据已写入: %s (共%s行)", self.residual_csv_path, written_rows)

    def finalize_residuals(self):
        """完成残差记录，将剩余缓冲区数据写入文件"""
        if self.residual_buffer:
            buffer_size = len(self.residual_buffer)
            self._flush_residual_buffer()
            self.logger.info("✅ 最终残差数据已保存到: %s (最后%s行)", self.residual_csv_path, buffer_size)

    def cleanup(self):
        """清理资源，关闭所有writer"""
        self.finalize_residuals()
        self.close_tensorboard()
        self.logger.info("✅ Logger cleanup completed for %s", self.name)

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.cleanup()
