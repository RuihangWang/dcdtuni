"""
Parametric utilities shared by the boundary condition implementations.
This module provides the per-case parameter samplers used to resolve
$variable_name references, and the value-function evaluator (Functionbase)
that turns a TOML value spec into a boundary field tensor.
The abstract BC interfaces themselves live in BCbase.
"""

import builtins
import math
import torch
import types
from typing import Optional, Union, Dict, Any, List
import random
import copy
import logging

logger = logging.getLogger(__name__)


def _compile_expression(text: str, names: dict):
    """A config-file BC expression compiled for ``eval``, refused if it uses any name ``names`` does not provide.

    The check is STATIC: every name and attribute the expression reads (``co_names``) must be one
    of ``names`` (x, y, z, pi and the listed torch functions), and nested code (lambdas, comprehensions) is
    refused, so no builtin, no dunder and no attribute walk is reachable from the expression text.  The frame
    then runs with the REAL builtins, because C code inside torch needs them: a C-level ``PyImport_Import``
    reads ``__import__`` from the calling frame's builtins, and with the empty dict this used before, the tall
    cavity's piecewise wall-temperature expression raised ``KeyError: '__import__'`` while the case loaded
    (a C-level import inside torch reads ``__import__``).  The check leaves the values unchanged."""
    code = compile(text, "<bc expression>", "eval")
    if any(isinstance(const, types.CodeType) for const in code.co_consts):
        raise ValueError(f"expression {text!r}: nested code (lambda / comprehension) is not allowed")
    unknown = sorted(set(code.co_names) - set(names))
    if unknown:
        raise ValueError(f"expression {text!r} uses {unknown}; allowed names: {sorted(names)}")
    return code


class ParameterSampler:
    """
    参数采样器：管理单个参数的无放回采样池。

    支持的参数类型:
    - arithmetic: 等差数列采样，val=[start, step, end]
    - choices: 从给定列表中采样
    - constant: 固定值（不采样）

    scope:
    - global: 同一个reset周期内所有位置返回相同的值
    - local: 每次调用返回不同的值（每个位置独立采样）
    """

    def __init__(self, name: str, spec: dict, seed: int = None):
        self.name = name
        self.spec = copy.deepcopy(spec)
        self.value_type = spec.get('value_type', 'constant')
        self.scope = spec.get('scope', 'global')
        self._rng = random.Random(seed)

        self._pool: List = []
        self._current_global_value = None
        self._global_value_set = False

        self._init_pool()

    def _init_pool(self):
        """根据 spec 初始化采样池。"""
        if self.value_type == 'arithmetic':
            val = self.spec.get('val', [])
            if isinstance(val, (list, tuple)) and len(val) == 3:
                start, step, end = val
                if step == 0:
                    self._pool = [start]
                else:
                    n = int((end - start) / step) + 1
                    self._pool = [start + i * step for i in range(n)]
                    if abs(self._pool[-1] - end) > 1e-12:
                        self._pool.append(end)
            else:
                logger.warning(f"Parameter '{self.name}': arithmetic expects val=[start, step, end], got {val}")
                self._pool = [val] if val else []

        elif self.value_type == 'choices':
            # Accept both spec['val'] (preferred, consistent with the
            # arithmetic/constant branches above) and legacy spec['choices'].
            # Older TOMLs may still use the `choices` key; new ones should use
            # `val` for consistency so all parameter types look the same:
            #   nu_sweep = {value_type = "choices",    val = [0.01, 0.0025, ...]}
            #   nu_sweep = {value_type = "arithmetic", val = [0.005, 0.0001, 0.01]}
            #   nu_sweep = {value_type = "constant",   val = 0.01}
            val = self.spec.get('val', self.spec.get('choices', []))
            self._pool = list(val) if isinstance(val, (list, tuple)) else [val]

        elif self.value_type in ('constant', 'uniform'):
            self._pool = []

        else:
            logger.warning(f"Parameter '{self.name}': unknown value_type '{self.value_type}', treating as constant")
            self._pool = []

        if self._pool:
            self._rng.shuffle(self._pool)
            logger.debug(f"Parameter '{self.name}' initialized with pool size {len(self._pool)}")

    def _refill_pool(self):
        """当池空时重新填充并打乱。"""
        self._init_pool()
        if not self._pool:
            raise ValueError(f"Parameter '{self.name}' has empty pool after refill")

    def sample(self) -> Any:
        """采样一个值。"""
        if self.value_type in ('constant', 'uniform'):
            return self.spec.get('val')

        if self.scope == 'global':
            if self._global_value_set:
                return self._current_global_value
            if not self._pool:
                self._refill_pool()
            self._current_global_value = self._pool.pop()
            self._global_value_set = True
            logger.info(f"Parameter '{self.name}' (global): sampled value {self._current_global_value}")
            return self._current_global_value

        if not self._pool:
            self._refill_pool()
        value = self._pool.pop()
        logger.debug(f"Parameter '{self.name}' (local): sampled value {value}")
        return value

    def reset(self):
        """重置采样器状态，下次采样会取新值。"""
        self._global_value_set = False
        self._current_global_value = None
        logger.debug(f"Parameter '{self.name}' reset")

    def get_current_value(self) -> Any:
        """获取当前已采样的值（不触发新采样）。用于调试。"""
        return self._current_global_value


class ParametricManager:
    """
    参数化管理器：管理一个 case 的所有参数采样器。

    用法:
    1. 从 TOML 的 [parameter] 部分注册参数
    2. 通过 get_value(name) 获取采样值
    3. 在 reset_env 时调用 reset_all() 重置所有参数

    BC 类在 __init__ 中通过 resolve_value() 方法解析 $variable_name 引用。
    """

    def __init__(self, seed: int = None):
        self._samplers: Dict[str, ParameterSampler] = {}
        self._seed = seed

    def register_from_toml(self, parameter_dict: dict):
        """从 TOML 的 [parameter] 部分注册所有参数。"""
        for name, spec in parameter_dict.items():
            if isinstance(spec, dict):
                self._samplers[name] = ParameterSampler(name, spec, self._seed)
                logger.info(f"Registered parameter '{name}': {spec}")
            else:
                self._samplers[name] = ParameterSampler(
                    name, {'value_type': 'constant', 'val': spec}, self._seed
                )

    def has_parameter(self, name: str) -> bool:
        """检查参数是否已注册。"""
        return name in self._samplers

    def get_value(self, name: str) -> Any:
        """获取参数的采样值。"""
        if name not in self._samplers:
            raise KeyError(f"Parameter '{name}' not registered")
        return self._samplers[name].sample()

    def reset_all(self):
        """重置所有参数采样器。"""
        for sampler in self._samplers.values():
            sampler.reset()
        logger.info(f"All {len(self._samplers)} parameters reset")

    def override_value(self, name: str, value: Any) -> None:
        """Force a parameter to a specific value, bypassing the random pool.

        Used by the multi-slot pool loader to inject per-case explicit
        values (e.g., the Reynolds number derived from a case_name) so
        that each slot's BC / init_phi / theta_PDE reflect that case's
        actual physics rather than a random draw from the arithmetic pool.

        After calling override_value(name, v):
          * All subsequent `get_value(name)` calls return `v`
          * All subsequent `resolve_value("$name")` calls return `v`
          * The sampler is coerced to value_type='constant'
          * `reset()` is called on the existing sampler to clear any
            previously cached global value.

        If the parameter was not previously registered, it is registered
        as a new constant with the given value.
        """
        if name in self._samplers:
            sampler = self._samplers[name]
            sampler.reset()  # clear cached global value
            sampler.value_type = "constant"
            sampler.spec = {"value_type": "constant", "val": value}
            # For constant/uniform samplers, sample() reads spec['val'] each call
            # so simply updating the spec is sufficient. Belt-and-suspenders: clear pool.
            sampler._pool = []
            sampler._global_value_set = False
            sampler._current_global_value = value
            logger.info(f"Parameter '{name}' override -> {value}")
        else:
            self.register_from_toml({name: {"value_type": "constant", "val": value}})
            logger.info(f"Parameter '{name}' registered as constant -> {value}")

    def resolve_value(self, value: Any) -> Any:
        """
        递归解析值中的 $variable_name 引用。

        这是 BC 类在 __init__ 中应该调用的方法。
        支持:
        - 字符串: "$inlet_velocity" -> 采样值
        - 列表: ["$inlet_velocity", 0.0, 0.0] -> [采样值, 0.0, 0.0]
        - 字典: 递归解析所有值
        """
        if isinstance(value, str):
            if value.startswith('$'):
                param_name = value[1:]
                return self.get_value(param_name)
            return value

        elif isinstance(value, (list, tuple)):
            resolved = [self.resolve_value(v) for v in value]
            return tuple(resolved) if isinstance(value, tuple) else resolved

        elif isinstance(value, dict):
            return {k: self.resolve_value(v) for k, v in value.items()}

        return value

    def get_status(self) -> dict:
        """返回所有参数的当前状态。"""
        return {
            name: {
                'value_type': s.value_type,
                'scope': s.scope,
                'current_value': s.get_current_value(),
                'pool_size': len(s._pool),
            }
            for name, s in self._samplers.items()
        }


# Boundary values built from Python scalars are run constants: a patch's prescribed
# value, its element count and the target device do not change between residual
# evaluations. Rebuilding one costs a pageable host-to-device copy on every call, which
# is both wasted work and a *synchronisation* -- and a sync inside the captured region
# makes
# CUDA graph capture fail outright (cudaErrorStreamCaptureInvalidated).
#
# The cache is keyed on content rather than held per instance because several call
# sites (the flow-rate and freestream BCs in setU, the fixed-value p_rgh init) build a
# throwaway Functionbase on every call, so a per-instance cache would never hit.
#
# Entries are only ever READ by callers -- every use is either a slice assignment
# ``field[patch] = value_tensor`` or a torch.where operand -- so handing back the same
# tensor object is safe. Do not mutate a returned tensor in place.
# Bounded so a parametric sweep that samples a fresh value every case (the DataCenter
# flow rates, say) cannot grow it without limit; steady cases hit the same few keys
# forever and never evict -- provided the bound holds every patch's values: a data-centre hall carries thousands
# of patches (dcdtuni Base201: 4,804, ~3 values each), and at 512 the FIFO evicted every entry before its next use,
# so each boundary pass rebuilt (and copied to the device) every value tensor again (27 s of the Warp backend's
# set-up on Base201 L1, 2026-11-18).  The tensors are patch-sized, so the bound is the boundary's size in memory.
_VALUE_TENSOR_CACHE: Dict[Any, torch.Tensor] = {}
_VALUE_TENSOR_CACHE_MAX = 65536


def clear_value_tensor_cache():
    """Drop cached boundary value tensors, e.g. when switching mesh or device."""
    _VALUE_TENSOR_CACHE.clear()


def _cache_store(key, tensor):
    if len(_VALUE_TENSOR_CACHE) >= _VALUE_TENSOR_CACHE_MAX:
        # Plain FIFO eviction; dicts preserve insertion order.
        _VALUE_TENSOR_CACHE.pop(next(iter(_VALUE_TENSOR_CACHE)))
    _VALUE_TENSOR_CACHE[key] = tensor
    return tensor


def _as_int(n):
    """Patch bounds arrive as 0-dim CPU tensors; int() on those is free."""
    return int(n) if torch.is_tensor(n) else int(n)


class Functionbase:
    def __init__ (self, value_type, value, num_element, channel_dim,):
        self.value_type = value_type
        self.value = value
        self.num_element = num_element
        self.channel_dim = channel_dim

    def _cache_key(self, tag, device):
        # repr() gives a content-based key for the float/list/dict value specs used
        # here, so a parametric override (which rewrites self.value between steps)
        # naturally misses the cache and rebuilds.
        return (tag, self.value_type, repr(self.value),
                _as_int(self.num_element), int(self.channel_dim), str(device))

    def uniform(self,**kwargs):

        device = kwargs["device"]

        key = self._cache_key("uniform", device)
        cached = _VALUE_TENSOR_CACHE.get(key)
        if cached is not None:
            return cached

        # Convert value to tensor
        if isinstance(self.value, (list, tuple)) and len(self.value) == self.channel_dim:
            value_tensor = torch.tensor(
                self.value, dtype=torch.float32, device=device
            ).view(-1,self.channel_dim).repeat(_as_int(self.num_element),1)
        elif isinstance(self.value, (float, int)):
            value_tensor = torch.tensor(
                self.value, dtype=torch.float32, device=device
            ).view(-1,self.channel_dim).repeat(_as_int(self.num_element),1)
        else:
            raise ValueError(f"Invalid value format for velocity: {self.value}, expect in [value, value, value] or [value]")

        return _cache_store(key, value_tensor)

    def constant(self,**kwargs):

        device = kwargs["device"]

        key = self._cache_key("constant", device)
        cached = _VALUE_TENSOR_CACHE.get(key)
        if cached is not None:
            return cached

        # Convert value to tensor
        if isinstance(self.value, (float, int)):
            value_tensor = torch.tensor(self.value,device=device)
        else:
            raise ValueError(f"Invalid value format for velocity: {self.value}, expect in [value, value, value] or [value]")

        return _cache_store(key, value_tensor)

    def _from(self, **kwargs):
        para = kwargs["para"]
        
        if isinstance(self.value,List):
            # 找到应用可变参数的位置
            for local_val in self.value:
                if isinstance(local_val,str):
                    para
    
    def nonuniform(self,**kwargs):
        raise NotImplementedError

    def parabolic(self, **kwargs):
        """
        Compute parabolic velocity profile based on y coordinates.

        Formula: U(y) = Umax * (1 - 4*y^2/H^2)
        where:
            - Umax = 1.5 * Umean
            - Umean = Re * mu / (rho * D)

        Required kwargs:
            - device: torch device
            - cpd_cell_pos: cell position tensor [N, 3]
            - fvconfig: FV configuration dict (for rho, mu)

        Value dict should contain:
            - Re: Reynolds number
            - H: channel height (Y-direction span)
            - D: characteristic diameter
            - y_center: Y-coordinate of channel center (default: 0.0)

        rho and mu are automatically obtained from fvconfig.theta_pde
        """
        device = kwargs.get("device", "cpu")
        cpd_cell_pos = kwargs.get("cpd_cell_pos", None)
        fvconfig = kwargs.get("fvconfig", None)

        if cpd_cell_pos is None:
            logger.warning("parabolic: cpd_cell_pos not provided, falling back to uniform zero")
            return torch.zeros((self.num_element, self.channel_dim), dtype=torch.float32, device=device)

        # Extract parameters from value dict
        if not isinstance(self.value, dict):
            raise ValueError(f"parabolic value_type requires dict with Re, H, D, etc. Got: {self.value}")

        Re = self.value.get("Re", 100.0)
        H = self.value.get("H", 0.41)
        D = self.value.get("D", 0.1)
        y_center = self.value.get("y_center", 0.0)

        # Get rho and mu from fvconfig (preferred) or fallback to value dict
        if fvconfig is not None:
            theta_pde = fvconfig.get("theta_pde", {})
            rho_cfg = theta_pde.get("rho", {"val": 1.0})
            mu_cfg = theta_pde.get("mu", {"val": 0.001})
            rho = rho_cfg.get("val", rho_cfg) if isinstance(rho_cfg, dict) else rho_cfg
            mu = mu_cfg.get("val", mu_cfg) if isinstance(mu_cfg, dict) else mu_cfg
        else:
            # Fallback to value dict (backward compatibility)
            rho = self.value.get("rho", 1.0)
            mu = self.value.get("mu", 0.001)
            logger.warning("parabolic: fvconfig not provided, using rho/mu from value dict")

        # Compute mean and max velocity from Re
        # Re = rho * Umean * D / mu => Umean = Re * mu / (rho * D)
        Umean = Re * mu / (rho * D)
        Umax = 1.5 * Umean  # For 2D parabolic profile

        # Get Y coordinates
        # The profile is a function of mesh geometry and fixed physical parameters, so
        # it too is a run constant; key on the position buffer's identity so a genuinely
        # different mesh rebuilds it.
        n_elem = _as_int(self.num_element)
        key = self._cache_key("parabolic", device) + (
            cpd_cell_pos.data_ptr(), tuple(cpd_cell_pos.shape), Umax, H, y_center,
        )
        cached = _VALUE_TENSOR_CACHE.get(key)
        if cached is not None:
            return cached

        y_coords = cpd_cell_pos[:n_elem, 1]

        # Compute parabolic profile
        y_offset = y_coords - y_center
        H2 = H * H
        Ux = Umax * (1.0 - 4.0 * y_offset * y_offset / H2)
        Ux = torch.clamp(Ux, min=0.0)  # Ensure non-negative velocity

        # Create velocity tensor [N, 3] with flow in X direction
        velocity = torch.zeros((n_elem, self.channel_dim), dtype=torch.float32, device=device)
        velocity[:, 0] = Ux.to(device)

        return _cache_store(key, velocity)

    def table(self,**kwargs):
        raise NotImplementedError
    
    def sin(self,**kwargs):
        raise NotImplementedError
    
    def expression(self, **kwargs):
        """Per-face values from an analytic expression of the face position.

        ``val`` is one string per channel (a list of ``channel_dim`` strings, or a
        single string for a scalar) in the variables ``x``, ``y``, ``z`` -- the
        boundary (ghost) positions of this patch, i.e. its face centres -- with
        ``pi`` and the torch functions ``sin cos tan exp log sqrt tanh abs
        minimum maximum where`` available, e.g. a regularised cavity lid::

            value = {value_type = "expression",
                     val = ["256*x**2*(1-x)**2*z**2*(1-z)**2", "0", "0"]}

        Used to compare against OpenFOAM's exprFixedValue on the same
        faces. Needs ``cpd_cell_pos`` (the patch's slice) from the caller.
        """
        device = kwargs.get("device", "cpu")
        pos = kwargs.get("cpd_cell_pos", None)
        if pos is None:
            raise ValueError("expression value_type needs cpd_cell_pos for its patch")
        exprs = self.value if isinstance(self.value, (list, tuple)) else [self.value]
        if len(exprs) != self.channel_dim:
            raise ValueError(f"expression needs {self.channel_dim} component strings, got {exprs!r}")
        n_elem = _as_int(self.num_element)
        key = self._cache_key("expression", device) + (pos.data_ptr(), tuple(pos.shape))
        cached = _VALUE_TENSOR_CACHE.get(key)
        if cached is not None:
            return cached
        p = pos[..., :n_elem, :].to(device=device, dtype=torch.float64)
        names = {"x": p[..., 0], "y": p[..., 1], "z": p[..., 2], "pi": math.pi,
                 "sin": torch.sin, "cos": torch.cos, "tan": torch.tan, "exp": torch.exp,
                 "log": torch.log, "sqrt": torch.sqrt, "tanh": torch.tanh, "abs": torch.abs,
                 "minimum": torch.minimum, "maximum": torch.maximum, "where": torch.where}
        cols = []
        for e in exprs:
            # config-file expression: names whitelisted statically, real builtins for torch's C code
            v = eval(_compile_expression(str(e), names), {"__builtins__": builtins.__dict__}, names)
            v = torch.as_tensor(v, dtype=torch.float64, device=device)
            cols.append(v.expand(p.shape[:-1]) if v.dim() == 0 else v)
        return _cache_store(key, torch.stack(cols, dim=-1))
    
    def tensor(self, **kwargs):
        """A torch tensor value (opt-in, 2026-11-07, Pipeline.CoupledNewton.dc_adjoint): the value is used AS IS, keeping
        its autograd graph, so a boundary value can be a design parameter the residual is differentiated against.  A
        scalar spec (num_element 1, channel 1: flow rates) returns a 0-d tensor like constant; otherwise the value
        is broadcast to [num_element, channel_dim] like uniform.  Never cached."""
        v = self.value.to(kwargs["device"])
        n, c = _as_int(self.num_element), int(self.channel_dim)
        if n == 1 and c == 1:
            return v.reshape(())
        return v.reshape(1, c).expand(n, c)

    def eval(self, **kwargs):

        if self.value_type=="uniform":
            return self.uniform(**kwargs)
        elif self.value_type=="nonuniform":
            raise NotImplementedError
        elif self.value_type=="constant":
            return self.constant(**kwargs)
        elif self.value_type=="from":
            return self._from(**kwargs)
        elif self.value_type=="table":
            raise NotImplementedError
        elif self.value_type=="sin":
            raise NotImplementedError
        elif self.value_type=="expression":
            return self.expression(**kwargs)
        elif self.value_type=="parabolic":
            return self.parabolic(**kwargs)
        elif self.value_type=="tensor":
            return self.tensor(**kwargs)
        else:
            raise ValueError(f"Unknown value_type: {self.value_type}")
