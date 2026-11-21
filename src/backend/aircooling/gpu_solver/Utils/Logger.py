import os
import datetime as dt
import json
import shutil
import fnmatch
import csv
import logging
import sys


class Logger:
    """
    训练日志记录器（实验管理 + 全局logging配置）

    功能：
    - 配置全局模块级logging系统（使所有模块都能用logging.getLogger(__name__)）
    - 使用Python logging包记录日志（支持多级别）
    - 保存和加载模型状态
    - 记录残差数据到CSV（带缓冲区）
    - 可选：复制源代码到实验目录

    使用示例：
        # 在入口文件（train_with_fvm.py等）
        logger = Logger(name="experiment", params=params, copy_code=True)
        logger.info("Training started")  # 使用代理方法

        # 在其他模块（FVdomain/Load_mesh.py等）
        import logging
        logger = logging.getLogger(__name__)
        logger.info("Loading mesh...")  # 自动输出到console和文件
    """

    def __init__(
        self,
        name,
        head="Logger",
        datetime=None,
        use_csv=False,
        params=None,
        saving_path=None,
        copy_code=False,
        seed=None,
        log_level="INFO",
    ):
        """
        初始化Logger

        Args:
            name: 实验名称（例如模型名）
            head: 根目录名（默认"Logger"）
            datetime: 指定时间戳（默认当前时间）
            use_csv: 是否使用CSV记录（保留参数，向后兼容）
            params: 参数对象（用于保存配置）
            saving_path: 指定保存路径（默认自动生成）
            copy_code: 是否复制源代码
            seed: 随机种子（用于创建子目录）
            log_level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        """
        self.head = head
        self.name = name
        self.params = params

        # 设置时间戳
        self.datetime = datetime if datetime else dt.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")

        # 设置保存路径
        if saving_path is not None:
            self.saving_path = saving_path
        else:
            self.saving_path = os.path.join(os.getcwd(), head, name, self.datetime)

        os.makedirs(self.saving_path, exist_ok=True)

        # 配置logging系统（先配置logging，这样后续操作可以记录日志）
        self._setup_logging(log_level)

        # 复制源代码（如果需要）
        if copy_code:
            self._copy_source_code(seed)

        # 一页纸 run card：几周后回看时，不必翻代码就知道这次跑的是什么
        self._write_run_card()

        # 初始化残差记录系统
        self._init_residual_logging()

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
    
    def _setup_logging(self, log_level):
        """
        配置全局logging系统，使所有模块都能使用logging

        特性：
        1. 配置根logger（全局生效）
        2. 所有模块使用 logging.getLogger(__name__) 都会自动输出到console和文件
        3. 自动抑制第三方库的verbose日志

        Args:
            log_level: 日志级别字符串 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        """
        numeric_level = getattr(logging, log_level.upper(), logging.INFO)

        # ========== 配置全局根logger ==========
        root_logger = logging.getLogger()
        root_logger.setLevel(numeric_level)

        # 清除已有的handlers（避免重复）
        if root_logger.handlers:
            root_logger.handlers.clear()

        # 统一的格式化器（显示模块名，便于调试）
        formatter = logging.Formatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

        # File handler
        log_file = os.path.join(self.saving_path, 'training.log')
        file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        # ========== 抑制第三方库的verbose日志 ==========
        third_party_libs = ['matplotlib', 'PIL', 'h5py', 'urllib3', 'numba']
        for lib in third_party_libs:
            logging.getLogger(lib).setLevel(logging.WARNING)

        # ========== 创建当前实例的logger（用于向后兼容） ==========
        logger_name = f"{self.head}.{self.name}"
        self.logger = logging.getLogger(logger_name)

        # 记录初始化信息
        self.logger.info(f"Logger initialized at level {log_level}")
        self.logger.info(f"Saving path: {self.saving_path}")
        self.logger.info(f"Log file: {log_file}")
        self.logger.info("="*60)

    def _write_run_card(self):
        """RUN_CARD.md: what this run is, in one screen.

        The source snapshot and commandline_args.json already hold everything,
        but neither answers "which commit, which machine, what effective batch"
        without digging.  A long run outlives the memory of how it was launched.
        """
        import subprocess, socket, sys, datetime as _dt

        def _git(*a):
            try:
                return subprocess.run(
                    ["git", *a], cwd=os.path.dirname(os.path.dirname(__file__)),
                    capture_output=True, text=True, timeout=10,
                ).stdout.strip() or "(unavailable)"
            except Exception:
                return "(unavailable)"

        p = vars(self.params) if self.params is not None else {}
        ws = int(os.environ.get("WORLD_SIZE", 1))
        bs = p.get("batch_size", "?")
        dirty = _git("status", "--porcelain")
        rows = [
            ("commit", _git("rev-parse", "HEAD")),
            ("commit subject", _git("log", "-1", "--format=%s")),
            ("working tree", "DIRTY -- source/ is the ground truth" if dirty else "clean"),
            ("host", socket.gethostname()),
            ("launched", _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            ("cwd", os.getcwd()),
            ("python", sys.version.split()[0]),
            ("torch", __import__("torch").__version__),
            # The entries announce this with print(), which a launcher's stdout
            # redirect swallows, so training.log never records whether the run
            # was compiled -- and a compiled and an eager run are not the same
            # measurement.
            ("torch.compile", "DISABLED (TORCH_COMPILE_DISABLE=1)"
                if os.environ.get("TORCH_COMPILE_DISABLE") == "1"
                or os.environ.get("TORCHDYNAMO_DISABLE") == "1"
                else "enabled"),
            ("inductor cache", os.environ.get("TORCHINDUCTOR_CACHE_DIR", "(default)")),
            ("PYTHONNOUSERSITE", os.environ.get("PYTHONNOUSERSITE", "unset -- user site-packages can shadow the env")),
            ("CUDA_VISIBLE_DEVICES", os.environ.get("CUDA_VISIBLE_DEVICES", "(all)")),
            ("world_size", ws),
            ("batch_size/rank", bs),
            ("effective batch", ws * bs if isinstance(bs, int) else "?"),
            ("dataset_size (global)", p.get("dataset_size_global", p.get("dataset_size"))),
            ("dataset_size/rank", p.get("dataset_size")),
            ("net", p.get("net")),
            ("hidden_size", p.get("hidden_size")),
            ("attn_processor_num", p.get("attn_processor_num")),
            ("fourier_num_freqs", p.get("fourier_num_freqs")),
            ("average_sequence_length", p.get("average_sequence_length")),
            ("n_epochs", p.get("n_epochs")),
            ("lr / optimizer", f"{p.get('lr')} / {p.get('optimizer_type')}"),
            ("loss_mode", p.get("loss_mode")),
            ("dataset_dir", p.get("dataset_dir")),
        ]
        try:
            with open(os.path.join(self.saving_path, "RUN_CARD.md"), "w") as f:
                f.write(f"# {os.path.basename(self.saving_path)}\n\n")
                f.write("| | |\n|---|---|\n")
                for k, v in rows:
                    f.write(f"| {k} | `{v}` |\n")
                if dirty:
                    f.write("\n## uncommitted at launch\n\n```\n" + dirty + "\n```\n")
        except Exception as e:  # never let bookkeeping kill a run
            self.logger.warning("RUN_CARD.md not written: %s", e)

    def _copy_source_code(self, seed=None):
        """
        复制源代码到实验目录

        Args:
            seed: 随机种子（如果提供，创建seed子目录）
        """
        if seed is not None:
            os.makedirs(os.path.join(self.saving_path, f"seed_{seed}"), exist_ok=True)

        # 源代码路径（src目录）
        source_dir = os.path.dirname(os.path.dirname(__file__))
        target_dir = os.path.join(self.saving_path, "source")
        os.makedirs(target_dir, exist_ok=True)

        # 白名单：允许复制的文件和目录。
        # 条目按 src/ 下的顶层目录名精确匹配（见下面 _copy_files_with_whitelist
        # 的 `item in whitelist`），因此一次目录搬迁会静默地把一个包踢出快照 ——
        # 不报错，只是几周后翻 source/ 时才发现代码没了。
        # 例如 ODIL 位于 src/Pipeline/ 之下，白名单里写的是 "Pipeline" 而不是 "ODIL"；
        # 这样五个 pipeline 包
        # （CoupledNewton / ODIL / PCNO / NeuralOperator / MMS）随父目录一起进快照。
        whitelist = [
            "*.py", "*.txt", "*.ipynb",
            "BC_encoder", "Parse_mesh", "NNmodels",
            "FVsolver", "FVdomain", "Utils", "Post_process", "Pipeline", "entries",
        ]

        self._copy_files_with_whitelist(source_dir, target_dir, whitelist)
        self.logger.info(f"Source code copied to {target_dir}")

    def _copy_files_with_whitelist(self, src_dir, dst_dir, whitelist):
        """
        根据白名单复制文件和目录

        Args:
            src_dir: 源目录
            dst_dir: 目标目录
            whitelist: 白名单列表（支持glob模式）
        """
        if not os.path.exists(dst_dir):
            os.makedirs(dst_dir)

        for item in os.listdir(src_dir):
            src_path = os.path.join(src_dir, item)

            # 复制目录
            if os.path.isdir(src_path) and item in whitelist:
                shutil.copytree(src_path, os.path.join(dst_dir, item), dirs_exist_ok=True)

            # 复制文件（匹配模式）
            elif os.path.isfile(src_path):
                for pattern in whitelist:
                    if fnmatch.fnmatch(item, pattern):
                        shutil.copy2(src_path, os.path.join(dst_dir, item))
                        break

    def _init_residual_logging(self):
        """初始化残差记录系统"""
        self.residual_buffer = []
        self.residual_buffer_size = 1000
        self.residual_csv_path = os.path.join(self.saving_path, "residuals.csv")
        self.residual_headers = None
        self.residual_headers_written = False
        self.residual_rows_written = 0

    # ==================== 模型状态管理 ====================

    def save_state(self, model, optimizer, scheduler, index="final"):
        """
        保存模型和优化器状态

        Args:
            model: 模型对象
            optimizer: 优化器对象
            scheduler: 学习率调度器
            index: 保存索引（例如epoch数）

        Returns:
            str: 保存的文件路径
        """
        states_dir = os.path.join(self.saving_path, "states")
        os.makedirs(states_dir, exist_ok=True)

        # 保存参数配置
        config_path = os.path.join(states_dir, "commandline_args.json")
        with open(config_path, "wt") as f:
            json.dump(vars(self.params), f, indent=4, ensure_ascii=False)

        # 保存模型状态
        state_path = os.path.join(states_dir, f"{index}.state")
        model.save_checkpoint(state_path, optimizer, scheduler)

        self.logger.info(f"Model state saved to {state_path}")
        return state_path

    def load_state(self, model, optimizer, scheduler, datetime=None, index=None, device=None):
        """
        加载模型和优化器状态

        Args:
            model: 模型对象
            optimizer: 优化器对象（可以为None）
            scheduler: 学习率调度器（可以为None）
            datetime: 指定运行的时间戳（None则使用最新）
            index: 指定加载的索引（None则使用最新）
            device: 设备（cuda/cpu）

        Returns:
            tuple: (datetime, index) 加载的时间戳和索引
        """
        # 查找datetime
        if datetime is None:
            runs_dir = os.path.join(self.head, self.name)
            if os.path.exists(runs_dir):
                dirs = sorted(os.listdir(runs_dir))
                # 排除当前运行
                dirs = [d for d in dirs if d != self.datetime]
                if dirs:
                    datetime = dirs[-1]
                else:
                    raise ValueError(f"No previous runs found in {runs_dir}")
            else:
                raise ValueError(f"Runs directory not found: {runs_dir}")

        # 查找index
        if index is None:
            states_dir = os.path.join(self.head, self.name, datetime, "states")
            if os.path.exists(states_dir):
                files = [f for f in os.listdir(states_dir) if f.endswith('.state')]
                if files:
                    # Newest by mtime, not by name.  Stems are `epoch % 3`, so
                    # the highest-numbered file is routinely the OLDEST on disk
                    # and natsorted picked it.
                    index = os.path.splitext(max(
                        files, key=lambda f: os.path.getmtime(os.path.join(states_dir, f))
                    ))[0]
                else:
                    raise ValueError(f"No state files found in {states_dir}")
            else:
                raise ValueError(f"States directory not found: {states_dir}")

        # 加载状态
        state_path = os.path.join(self.head, self.name, datetime, "states", f"{index}.state")
        model.load_checkpoint(optimizer=optimizer, scheduler=scheduler, ckpdir=state_path, device=device)

        self.logger.info(f"Model state loaded from {state_path}")
        return datetime, index

    def load_logger(self, datetime=None):
        """
        查找指定或最新的运行时间戳

        Note: 保留此方法用于向后兼容，实际上只查找datetime

        Args:
            datetime: 指定时间戳（None则使用最新）

        Returns:
            str: 找到的datetime
        """
        if datetime is None:
            runs_dir = os.path.join(self.head, self.name)
            if os.path.exists(runs_dir):
                dirs = sorted(os.listdir(runs_dir))
                # 排除当前运行
                dirs = [d for d in dirs if d != self.datetime]
                if dirs:
                    datetime = dirs[-1]

        return datetime

    # ==================== 残差记录 ====================

    def log_residuals(self, **kwargs):
        """
        记录残差数据到缓冲区（满时自动写入CSV）

        Args:
            **kwargs: 任意键值对，例如:
                iteration=100, loss_total=0.001, loss_momentum=0.002

        Example:
            logger.log_residuals(
                iteration=100,
                loss_total=0.001,
                loss_momentum=0.002,
                loss_continuity=0.003
            )
        """
        if not kwargs:
            return

        # 第一次调用时确定表头
        if self.residual_headers is None:
            # 确保iteration/x作为第一列
            if 'iteration' in kwargs:
                self.residual_headers = ['iteration'] + sorted([k for k in kwargs.keys() if k != 'iteration'])
            elif 'x' in kwargs:
                self.residual_headers = ['x'] + sorted([k for k in kwargs.keys() if k != 'x'])
            else:
                self.residual_headers = sorted(kwargs.keys())

        # 按表头顺序创建数据行
        row_data = [kwargs.get(h, 0.0) for h in self.residual_headers]
        self.residual_buffer.append(row_data)

        # 缓冲区满时写入
        if len(self.residual_buffer) >= self.residual_buffer_size:
            self._flush_residual_buffer()

    def _flush_residual_buffer(self):
        """将缓冲区数据写入CSV文件"""
        if not self.residual_buffer or self.residual_headers is None:
            return

        # 写入模式：第一次写入用'w'，后续追加用'a'
        mode = 'w' if not self.residual_headers_written else 'a'

        with open(self.residual_csv_path, mode, newline='') as f:
            writer = csv.writer(f)

            # 第一次写入表头
            if not self.residual_headers_written:
                writer.writerow(self.residual_headers)
                self.residual_headers_written = True

            # 写入数据
            writer.writerows(self.residual_buffer)

        written_rows = len(self.residual_buffer)
        self.residual_buffer.clear()
        self.residual_rows_written += written_rows
        self.logger.debug(
            "Residuals flushed: %d new rows (%d total) written to %s",
            written_rows,
            self.residual_rows_written,
            self.residual_csv_path,
        )

    def finalize_residuals(self):
        """完成残差记录，写入剩余数据"""
        if self.residual_buffer:
            self._flush_residual_buffer()
        if self.residual_rows_written:
            self.logger.info(
                "Residuals finalized: %d total rows saved to %s",
                self.residual_rows_written,
                self.residual_csv_path,
            )
