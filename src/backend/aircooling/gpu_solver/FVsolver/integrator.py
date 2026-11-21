"""
Integrator factory module for creating appropriate FV solvers.

This module provides a clean factory pattern for instantiating different finite volume
solvers based on configuration, with proper type hints for IDE support.
"""

from typing import Any, Iterator, Literal, Optional, Tuple
import torch
from FVsolver.applications.base_solver import BaseSolver


GraphReduction = Literal["mean", "sum"]
_COMPACT_NEW_STATE_SOLVERS = frozenset({"rhocoext", "rhoco_segment_csr", "rhocoinviscompt", "rhocoviscompt"})


def sum_time_loss(
    loss_by_time: torch.Tensor,
    *,
    graph_reduction: GraphReduction = "mean",
) -> torch.Tensor:
    """Reduce an explicit FVM time loss to the scalar used by optimizers.

    The backend contract is ``loss_by_time[T, B]`` where ``T`` is the physical
    time axis and ``B`` is the independent graph/case batch.  Physical-time
    objectives are additive, so this helper always sums ``T``.  The independent
    graph batch is averaged by default, preserving the repository's historical
    mini-batch scaling, or may be summed explicitly for callers that need that
    convention.

    Keeping this reduction outside the applications is intentional: FVM
    applications must not silently erase the time axis before GNN+FVM or ODIL
    has chosen its objective.
    """

    if not isinstance(loss_by_time, torch.Tensor):
        raise TypeError(
            "loss_by_time must be a torch.Tensor with shape [T,B], "
            f"got {type(loss_by_time).__name__}"
        )
    if loss_by_time.ndim != 2:
        raise ValueError(
            "loss_by_time must have shape [T,B], "
            f"got {tuple(loss_by_time.shape)}"
        )
    if loss_by_time.shape[0] < 1 or loss_by_time.shape[1] < 1:
        raise ValueError(
            "loss_by_time must contain at least one time step and one graph, "
            f"got {tuple(loss_by_time.shape)}"
        )

    loss_per_graph = loss_by_time.sum(dim=0)
    if graph_reduction == "mean":
        return loss_per_graph.mean()
    if graph_reduction == "sum":
        return loss_per_graph.sum()
    raise ValueError(
        "graph_reduction must be either 'mean' or 'sum', "
        f"got {graph_reduction!r}"
    )


class Integrator:
    """
    Factory class for creating and managing finite volume solvers.

    This class uses a factory pattern to instantiate the appropriate solver
    based on the solver type specified in the configuration. The solver instance
    is stored with proper type annotation (BaseSolver), which enables:

    1. IDE autocomplete and type inference
    2. "Go to Definition" navigation to method implementations
    3. Type checking and validation

    Example usage:
        >>> integrator = Integrator(params)
        >>> integrator.register_properties(fv_graph)
        >>> loss, solution = integrator(phi_old, phi_new)
    """

    def __init__(self, params: Any) -> None:
        """
        Initialize the integrator with the appropriate solver implementation.

        Args:
            params: Configuration object containing solver type and parameters.
                   Must have params.fvconfig["solver"]["type"] defined.

        Raises:
            ValueError: If the solver type is not recognized.
        """
        self.params = params
        solver_config = self.params.fvconfig["solver"]
        self.solver_type = solver_config["type"]
        self.turbulence = solver_config.get("turbulence", "default")

        # Create solver instance using factory pattern
        self._solver: BaseSolver = self._create_solver(self.solver_type)
        self._last_time_steps = 1
        self._last_input_ndim = 2

    def _create_solver(self, solver_type: str) -> BaseSolver:
        """Instantiate the discretisation named by the case's ``[solver] type``.

        The name-to-module table lives in ``FVsolver.applications.SOLVER_MODULES``
        so that "which discretisations exist" is one list a config check can read,
        rather than a chain of ``elif`` only Python can evaluate.  Replaced a
        seven-branch if/elif.
        """
        from FVsolver.applications import create_solver
        return create_solver(solver_type, self.params)

    # Delegate all solver methods with proper type hints

    def register_properties(self, fv_graph: Any, force_register: bool = False) -> None:
        """
        Register geometric and physical properties from the FV graph.

        Delegates to the underlying solver's register_properties method.

        Args:
            fv_graph: Finite volume graph containing mesh geometry and physics parameters
            force_register: If True, force re-registration even if already registered
        """
        self._solver.register_properties(fv_graph, force_register=force_register)

    def __call__(
        self,
        phi_old_cpd_cell: torch.Tensor,
        phi_new_cpd_cell: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Execute one or more physical time-step residuals in one forward pass.

        Args:
            phi_old_cpd_cell: Previous states, either ``[N_cpd,C]`` for one
                time step or ``[T,N_cpd,C]`` for a vectorized time window.
            phi_new_cpd_cell: Current states with the same rank, time extent,
                and channel count as ``phi_old``.  Compressible ``rhoco``
                solvers also retain their historical compact non-cyclic-cell
                input layout; those solvers expand the current state to full
                CPD internally.

        Returns:
            ``(loss_by_time, phi_new_smoothed_cpd_cell)``.  The loss always has
            shape ``[T,B]`` and deliberately remains un-reduced along time.
            The state preserves the input rank and normally the current-state
            shape.  A compact ``rhoco`` current state returns the expanded full
            CPD shape of ``phi_old``.  Use :func:`sum_time_loss` at the GNN+FVM
            or ODIL objective boundary to obtain a scalar.
        """
        self._validate_state_pair(phi_old_cpd_cell, phi_new_cpd_cell)
        self._last_input_ndim = phi_new_cpd_cell.ndim
        self._last_time_steps = (
            int(phi_new_cpd_cell.shape[0]) if phi_new_cpd_cell.ndim == 3 else 1
        )

        loss_per_graph, state = self._solver(
            phi_old_cpd_cell, phi_new_cpd_cell
        )
        loss_by_time = self._normalize_loss_shape(loss_per_graph)
        expected_state_shape = self._expected_state_shape(
            phi_old_cpd_cell, phi_new_cpd_cell
        )
        if state.shape != expected_state_shape:
            raise RuntimeError(
                f"{self.solver_type} returned state shape {tuple(state.shape)} "
                f"for expected shape {tuple(expected_state_shape)}"
            )
        return loss_by_time, state

    def _validate_state_pair(
        self,
        phi_old_cpd_cell: torch.Tensor,
        phi_new_cpd_cell: torch.Tensor,
    ) -> None:
        if not isinstance(phi_old_cpd_cell, torch.Tensor) or not isinstance(
            phi_new_cpd_cell, torch.Tensor
        ):
            raise TypeError("phi_old_cpd_cell and phi_new_cpd_cell must be tensors")
        if phi_old_cpd_cell.ndim not in (2, 3):
            raise ValueError(
                "FVM states must have shape [N,C] or [T,N,C], "
                f"got old={tuple(phi_old_cpd_cell.shape)}"
            )
        if phi_new_cpd_cell.ndim != phi_old_cpd_cell.ndim:
            raise ValueError(
                "Old and new FVM states must have the same rank, "
                f"got {phi_old_cpd_cell.ndim} and {phi_new_cpd_cell.ndim}"
            )
        if (
            phi_new_cpd_cell.shape[:-2] != phi_old_cpd_cell.shape[:-2]
            or phi_new_cpd_cell.shape[-1] != phi_old_cpd_cell.shape[-1]
        ):
            raise ValueError(
                "Old and new FVM states must have identical leading/time "
                "dimensions and channel counts, "
                f"got {tuple(phi_old_cpd_cell.shape)} and "
                f"{tuple(phi_new_cpd_cell.shape)}"
            )
        if self.solver_type in _COMPACT_NEW_STATE_SOLVERS:
            num_cpd_cells = int(self._solver.num_cpd_cells)
            mask = self._solver.mask_non_cyclic_cpd_cell
            if not isinstance(mask, torch.Tensor) or mask.dtype != torch.bool:
                raise RuntimeError(
                    f"{self.solver_type}.mask_non_cyclic_cpd_cell must be a "
                    "boolean tensor before integration"
                )
            compact_cells = int(mask.count_nonzero().item())
            old_cells = int(phi_old_cpd_cell.shape[-2])
            new_cells = int(phi_new_cpd_cell.shape[-2])
            if (
                old_cells != num_cpd_cells
                or mask.numel() != num_cpd_cells
                or new_cells not in (num_cpd_cells, compact_cells)
            ):
                raise ValueError(
                    f"{self.solver_type} requires a full old state with "
                    f"{num_cpd_cells} cells and a new state with either "
                    f"{num_cpd_cells} full or exactly {compact_cells} "
                    f"non-cyclic cells, got old/new shapes "
                    f"{tuple(phi_old_cpd_cell.shape)} and "
                    f"{tuple(phi_new_cpd_cell.shape)}"
                )
        elif phi_new_cpd_cell.shape[-2] != phi_old_cpd_cell.shape[-2]:
            raise ValueError(
                "Old and new FVM states must have identical cell counts "
                f"for {self.solver_type}, got "
                f"{phi_old_cpd_cell.shape[-2]} and "
                f"{phi_new_cpd_cell.shape[-2]}"
            )
        if phi_new_cpd_cell.ndim == 3 and phi_new_cpd_cell.shape[0] < 1:
            raise ValueError("The explicit time axis T must be >= 1")

    def _expected_state_shape(
        self,
        phi_old_cpd_cell: torch.Tensor,
        phi_new_cpd_cell: torch.Tensor,
    ) -> torch.Size:
        """Return the public state shape promised by the selected backend."""

        if (
            self.solver_type in _COMPACT_NEW_STATE_SOLVERS
            and phi_new_cpd_cell.shape[-2] != phi_old_cpd_cell.shape[-2]
        ):
            return phi_old_cpd_cell.shape
        return phi_new_cpd_cell.shape

    def _normalize_loss_shape(self, loss_per_graph: torch.Tensor) -> torch.Tensor:
        if not isinstance(loss_per_graph, torch.Tensor):
            raise TypeError(
                f"{self.solver_type} must return a tensor loss, "
                f"got {type(loss_per_graph).__name__}"
            )
        num_graphs = int(self._solver.num_graphs)
        if self._last_input_ndim == 2:
            expected = (num_graphs,)
            if tuple(loss_per_graph.shape) != expected:
                raise RuntimeError(
                    f"{self.solver_type} must return per-graph loss {expected} "
                    f"for rank-2 state input, got {tuple(loss_per_graph.shape)}"
                )
            return loss_per_graph.unsqueeze(0)

        expected = (self._last_time_steps, num_graphs)
        if tuple(loss_per_graph.shape) != expected:
            raise RuntimeError(
                f"{self.solver_type} must preserve [T,B] loss shape {expected}, "
                f"got {tuple(loss_per_graph.shape)}"
            )
        return loss_per_graph

    def enforce_bcs_only(
        self,
        phi_old_cpd_cell: torch.Tensor,
        phi_new_cpd_cell: torch.Tensor,
    ) -> torch.Tensor:
        """
        Apply boundary conditions to ``phi_new`` without residual computation.

        Delegates to the underlying solver's :meth:`enforce_bcs_only`. This
        standalone entry point lets the data-driven training path project
        network predictions onto the BC manifold without invoking the
        residual assembly or populating loss component bookkeeping on the
        solver.

        Args:
            phi_old_cpd_cell: ``[N_cpd,C]`` or ``[T,N_cpd,C]`` old states.
            phi_new_cpd_cell: Current states with matching shape.

        Returns:
            BC-enforced solution with the same shape as ``phi_new_cpd_cell``.
        """
        self._validate_state_pair(phi_old_cpd_cell, phi_new_cpd_cell)
        result = self._solver.enforce_bcs_only(
            phi_old_cpd_cell=phi_old_cpd_cell,
            phi_new_cpd_cell=phi_new_cpd_cell,
        )
        if result.shape != phi_new_cpd_cell.shape:
            raise RuntimeError(
                f"{self.solver_type}.enforce_bcs_only returned "
                f"{tuple(result.shape)}, expected {tuple(phi_new_cpd_cell.shape)}"
            )
        return result

    def _component_with_time_axis(self, component: Any) -> Any:
        """Expose separated residuals as [T,B,...] through Integrator."""

        if not isinstance(component, torch.Tensor):
            return component
        num_graphs = int(self._solver.num_graphs)
        if self._last_input_ndim == 2:
            if component.ndim < 1 or component.shape[0] != num_graphs:
                raise RuntimeError(
                    "Separated FVM residual must start with graph axis B; "
                    f"got {tuple(component.shape)} for B={num_graphs}"
                )
            return component.unsqueeze(0)
        if (
            component.ndim < 2
            or component.shape[0] != self._last_time_steps
            or component.shape[1] != num_graphs
        ):
            raise RuntimeError(
                "Time-batched separated FVM residual must start with [T,B]; "
                f"got {tuple(component.shape)}, expected "
                f"T={self._last_time_steps}, B={num_graphs}"
            )
        return component

    def get_separated_losses(self) -> Tuple[Any, ...]:
        """Return solver loss components with an explicit leading time axis."""

        components = self._solver.get_separated_losses()
        if components is None:
            return tuple()
        return tuple(self._component_with_time_axis(value) for value in components)

    def iter_named_residuals(self) -> Iterator[Tuple[str, Any]]:
        """Yield named residuals whose tensor shapes start with ``[T,B]``."""

        for name, component in self._solver.iter_named_residuals():
            yield name, self._component_with_time_axis(component)

    def __getattr__(self, name: str) -> Any:
        """
        Delegate attribute access to the underlying solver.

        This allows accessing solver-specific attributes and methods directly
        through the Integrator instance while maintaining the factory pattern.

        Args:
            name: Attribute name to access

        Returns:
            The requested attribute from the underlying solver

        Raises:
            AttributeError: If the attribute doesn't exist on the solver
        """
        # Avoid infinite recursion for special attributes
        if name.startswith('_'):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

        # Delegate to the underlying solver
        return getattr(self._solver, name)
