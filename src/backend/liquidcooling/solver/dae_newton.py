"""Device-resident implicit Newton roots for one acausal F(t,y,ydot,theta).

Steady and backward Euler share the exact same physical residual. Backward
solves the converged root's adjoint, including history-state/input derivatives;
Newton iteration traces are not retained. Fixed smooth active regimes and first
derivatives only. No diagonal regularization or unconverged-root gradients.
Component/residual algebra and batched Jacobian VJPs run in PyTorch kernels.
Only nonlinear-iteration convergence decisions transfer scalar norms to Python;
there are no per-unknown Python loops or NumPy/SciPy linear solves. The caller
owns device, precision and CPU threading policy.
"""
import math
from dataclasses import dataclass
from typing import Callable

import torch
from torch import Tensor
from torch.autograd.function import once_differentiable

from backend.liquidcooling.solver import dae_sparse


@dataclass
class DAEProblem:
    residual: Callable[[Tensor,Tensor,Tensor,Tensor],Tensor]
    differential_mask: Tensor
    state_scale: Tensor
    residual_scale: Tensor
    name: str = 'liquid-dae'
    parameter_validator: Callable[[Tensor],None] | None = None
    state_validator: Callable[[Tensor,Tensor],None] | None = None
    # First rows/states form a closed hydraulic subsystem; thermal rows may
    # depend on it, but hydraulic rows must not depend on thermal states.
    linear_partition: int | None = None

    def validate(self, state):
        n=state.numel()
        if state.ndim!=1 or state.dtype not in (torch.float32,torch.float64):
            raise ValueError('DAE states must be float32 or float64 vectors')
        if self.differential_mask.shape!=(n,) or self.differential_mask.dtype!=torch.bool or self.differential_mask.device!=state.device:
            raise ValueError('DAE differential mask must match the state vector')
        for scale in (self.state_scale,self.residual_scale):
            if scale.shape!=(n,) or scale.device!=state.device or scale.dtype!=state.dtype or not bool(torch.isfinite(scale).all() and (scale>0).all()):
                raise ValueError('DAE scales must be positive, finite and match the state')
        if not bool(torch.isfinite(state).all()):
            raise ValueError('DAE state must be finite')
        if self.linear_partition is not None and (
            type(self.linear_partition) is not int or not 0 < self.linear_partition < n
        ):
            raise ValueError('DAE block partition must split the state vector')


@dataclass(frozen=True)
class NewtonOptions:
    max_iterations: int = 40
    atol: float = 1e-10
    rtol: float = 1e-10
    max_backtracks: int = 18
    # Dense remains the reliable default up to 4000 unknowns. 'sparse'
    # (dae_sparse.py: coloured JVPs + sparse LU, the same factorisation for
    # the adjoint) is what larger networks use. The opt-in GMRES and exact
    # hydraulic/thermal block paths require workload-specific evidence.
    linear_solver: str = 'dense'
    linear_max_iterations: int = 64
    linear_atol: float = 1e-10
    linear_rtol: float = 1e-8
    # 'pgmres': restarted right-preconditioned GMRES. Krylov budget =
    # pgmres_restart x pgmres_max_cycles iterations; each iteration costs
    # 1 + poly_degree operator applications (polynomial preconditioner,
    # rebuilt per linear solve). poly_degree=0 disables preconditioning.
    pgmres_restart: int = 30
    pgmres_max_cycles: int = 8
    poly_degree: int = 8


@dataclass(frozen=True)
class NewtonReport:
    iterations: int
    scaled_residual_inf: float
    backtracks: int


def _gmres(matvec, rhs: Tensor, options: NewtonOptions) -> Tensor:
    """Device-resident Arnoldi solve of A x = rhs using only PyTorch matvecs.

    The Krylov iteration is scalar-controlled, but each operator application,
    projection and small least-squares solve stays on rhs.device. An explicit
    residual check prevents a small Hessenberg estimate from hiding a bad
    linear step. No component-wise Python loop or dense DAE Jacobian is built.
    """
    if rhs.ndim != 1:
        raise ValueError('GMRES rhs must be a vector')
    if rhs.numel() == 0:
        return torch.zeros_like(rhs)
    beta = torch.linalg.vector_norm(rhs)
    tolerance = options.linear_atol + options.linear_rtol * float(beta)
    if float(beta) <= tolerance:
        return torch.zeros_like(rhs)
    steps = min(rhs.numel(), options.linear_max_iterations)
    basis = [rhs / beta]
    hessenberg = rhs.new_zeros((steps + 1, steps))
    best_residual = float(beta)
    for j in range(steps):
        q = torch.stack(basis, dim=1)
        w = matvec(basis[j]).detach()
        if w.shape != rhs.shape or not bool(torch.isfinite(w).all()):
            raise RuntimeError('non-finite or malformed DAE GMRES matvec')
        # Two batched Gram-Schmidt passes keep the basis usable for stiff
        # thermal/hydraulic rows without a per-unknown Python loop.
        projection = q.T @ w
        w = w - q @ projection
        correction = q.T @ w
        w = w - q @ correction
        hessenberg[:j + 1, j] = projection + correction
        next_norm = torch.linalg.vector_norm(w)
        hessenberg[j + 1, j] = next_norm
        small = hessenberg[:j + 2, :j + 1]
        target = rhs.new_zeros(j + 2)
        target[0] = beta
        coefficients = torch.linalg.lstsq(small, target).solution
        candidate = q @ coefficients
        estimated = float(torch.linalg.vector_norm(small @ coefficients - target))
        if estimated <= tolerance:
            best_residual = float(torch.linalg.vector_norm(matvec(candidate).detach() - rhs))
            if best_residual <= tolerance:
                return candidate.detach()
        if j + 1 == steps or float(next_norm) <= torch.finfo(rhs.dtype).eps * float(beta):
            break
        basis.append(w / next_norm)
    if best_residual > tolerance:
        best_residual = float(torch.linalg.vector_norm(matvec(candidate).detach() - rhs))
    raise RuntimeError(
        f'matrix-free DAE GMRES did not converge in {j + 1} iterations; '
        f'linear residual {best_residual:.6g}, tolerance {tolerance:.6g}'
    )


def _givens(f: float, g: float):
    """LAPACK dlartg semantics: [[c,s],[-s,c]] @ [f,g] == [r,0].

    Host float64 scalars; math.hypot supplies the overflow-safe scaling.
    Algorithm as in the Pro6000 reference, independently written.
    """
    if g == 0.0:
        return 1.0, 0.0, f
    if f == 0.0:
        return 0.0, math.copysign(1.0, g), abs(g)
    length = math.hypot(f, g)
    r = math.copysign(length, f)
    return abs(f) / length, g / r, r


def _harmonic_values(hbar: Tensor) -> Tensor:
    """Harmonic Ritz values of a degree-step Arnoldi run, i.e. the roots of
    its GMRES residual polynomial (Morgan 2002 eq. 2.3 eigenproblem).
    Host float64; construction-time only, never in the Krylov hot loop."""
    m = hbar.shape[1]
    square = hbar[:m, :m]
    last = torch.zeros(m, dtype=torch.float64)
    last[m - 1] = 1.0
    weight = float(hbar[m, m - 1]) ** 2
    shifted = square + weight * torch.outer(
        torch.linalg.solve(square.transpose(0, 1), last), last)
    return torch.linalg.eigvals(shifted)


def _polynomial_preconditioner(operator, size: int, degree: int, *, device, dtype, seed: int = 0):
    """p(A) with A p(A) = I - phi_d(A), phi_d the GMRES(d) residual
    polynomial, applied in <= degree operator calls as a fixed linear map.

    Algorithm per the Pro6000 reference (SparseSolver/gmres.py), independently
    implemented. Three choices there are measurements, honored here:
      * the generating vector is RANDOM, never the rhs (built from b the
        polynomial stagnates — measured 13x there);
      * conjugate roots compose into REAL quadratic factors (2 operator calls
        per pair, 1 per real root — complex arithmetic would silently double
        the budget);
      * degree is capped: flat past 8 by measurement, refused past 100 where
        the product form loses ordering invariance.
    Construction runs once per linear solve (degree Arnoldi steps, two-pass
    CGS, host float64 Hessenberg); the fixed-length application loop is the
    only part the Krylov iteration sees. The algebraic gate
    A p(v) == v - (GMRES(d) residual on v) is pinned in
    test_dae_preconditioned_gmres.py — it is the only check that catches a
    wrong root set (a wrong set is still a linear degree-matvec map).
    """
    if degree < 1:
        raise ValueError('polynomial preconditioner degree must be >= 1')
    if degree > 100:
        raise ValueError('polynomial degree above 100 is unstable in product form')
    generator = torch.Generator(device='cpu').manual_seed(seed)
    start = torch.randn(size, generator=generator, dtype=torch.float64).to(device=device, dtype=dtype)
    basis = torch.empty((degree + 1, size), device=device, dtype=dtype)
    hbar = torch.zeros((degree + 1, degree), dtype=torch.float64)
    basis[0] = start / torch.linalg.vector_norm(start)
    eps = float(torch.finfo(dtype).eps)
    for column in range(degree):
        w = operator(basis[column]).detach()
        length = float(torch.linalg.vector_norm(w))
        stored = basis[: column + 1]
        for _ in range(2):  # two-pass classical Gram-Schmidt
            coefficients = stored @ w
            w = w - coefficients @ stored
            hbar[: column + 1, column] += coefficients.to('cpu', torch.float64)
        height = float(torch.linalg.vector_norm(w))
        hbar[column + 1, column] = height
        if height <= eps * max(length, eps):
            # Krylov space closed early: the polynomial is exact on it
            hbar = hbar[: column + 2, : column + 1]
            break
        basis[column + 1] = w / height
    del basis
    roots = _harmonic_values(hbar)
    pairs, singles, spent = [], [], set()
    for index in range(roots.numel()):
        if index in spent:
            continue
        value = roots[index]
        spent.add(index)
        inverse = 1.0 / value
        if abs(float(value.imag)) <= 1.0e-14 * (1.0 + float(value.abs())):
            singles.append(float(inverse.real))
            continue
        partner = int(torch.argmin((roots - value.conj()).abs()).item())
        spent.add(partner)
        pairs.append((2.0 * float(inverse.real), float(abs(inverse) ** 2)))

    def apply(vector: Tensor) -> Tensor:
        image = torch.zeros_like(vector)
        for weight, square in pairs:
            residual = vector - operator(image)
            image = image + weight * residual - square * operator(residual)
        for inverse_root in singles:
            image = image + inverse_root * (vector - operator(image))
        return image

    apply.probe = start / torch.linalg.vector_norm(start)
    apply.matvecs_per_call = 2 * len(pairs) + len(singles)
    return apply


def _pgmres(matvec, rhs: Tensor, options: NewtonOptions):
    """Right-preconditioned restarted GMRES for A x = rhs using only matvecs.

    Shape decisions follow the Pro6000 reference where it is measured:
      * the Krylov budget is the PRODUCT restart x cycles (raising one alone
        is not a single-variable experiment — that mistake is documented
        there twice);
      * the gain is the WINDOW, not the budget (equal-budget wide-vs-narrow
        measured 0.68 vs 1.00 there), so restart defaults wide (30) with a
        modest cycle count;
      * Hessenberg/Givens live on the HOST in float64 with one batched
        transfer per iteration — on CUDA a per-scalar device rotation sweep
        would be a kernel-launch storm; this is a documented deviation from
        the S2 plan's 'device-resident scalars' wording, taken because the
        reference measured this shape at 900k unknowns;
      * every cycle ends on a freshly computed true residual b - A P(y);
        the recurrence is never trusted across a restart;
      * float64 accumulation for the Hessenberg least squares whatever the
        basis dtype (its condition number bounds the attainable reduction).
    A degree-`options.poly_degree` polynomial preconditioner (rebuilt per
    call — the operator changes every Newton step) acts on the right:
    x = P(y), Krylov iterates on A P. Returns (x, info); x is detached —
    the iteration is not differentiable; the adjoint path calls this with
    the transpose matvec and its own freshly built polynomial.
    """
    if rhs.ndim != 1:
        raise ValueError('pgmres rhs must be a vector')
    n = rhs.numel()
    if n == 0:
        return torch.zeros_like(rhs), {'matvecs': 0, 'cycles': 0, 'converged': True,
                                       'true_residual': 0.0, 'budget_matvecs': 0}
    device, dtype = rhs.device, rhs.dtype
    restart = max(1, min(int(options.pgmres_restart), n))
    cycles = max(1, int(options.pgmres_max_cycles))
    degree = int(options.poly_degree)
    beta = float(torch.linalg.vector_norm(rhs))
    tolerance = options.linear_atol + options.linear_rtol * beta
    if beta <= tolerance:
        return torch.zeros_like(rhs), {'matvecs': 0, 'cycles': 0, 'converged': True,
                                       'true_residual': beta, 'budget_matvecs': 0}
    if degree > 0:
        precond = _polynomial_preconditioner(matvec, n, degree, device=device, dtype=dtype)
        per_iteration = 1 + precond.matvecs_per_call
        construction = degree
    else:
        def precond(vector):
            return vector
        per_iteration = 1
        construction = 0
    eps64 = float(torch.finfo(torch.float64).eps)
    y = torch.zeros(n, device=device, dtype=dtype)  # preconditioned coordinates
    residual = rhs.clone()
    residual_norm = beta
    matvecs = construction
    history = []
    used_cycles = 0
    breakdown = False
    basis = torch.empty((restart + 1, n), device=device, dtype=dtype)
    for _cycle in range(cycles):
        used_cycles += 1
        basis[0] = residual / residual_norm
        rotated = [0.0] * (restart + 1)
        rotated[0] = residual_norm
        cosine = [0.0] * restart
        sine = [0.0] * restart
        triangle = torch.zeros((restart + 1, restart), dtype=torch.float64)
        column = 0
        for column in range(restart):
            w = matvec(precond(basis[column])).detach()
            matvecs += per_iteration
            length = float(torch.linalg.vector_norm(w))
            stored = basis[: column + 1]
            coefficients = stored @ w
            w = w - coefficients @ stored
            correction = stored @ w
            w = w - correction @ stored
            coefficients = coefficients + correction
            height = float(torch.linalg.vector_norm(w))
            entries = coefficients.to('cpu', torch.float64).tolist()
            entries.append(height)
            if height <= eps64 * max(length, eps64):
                entries[column + 1] = 0.0
                breakdown = True
            else:
                basis[column + 1] = w / height
            for j in range(column):
                c, s = cosine[j], sine[j]
                upper, lower = entries[j], entries[j + 1]
                entries[j] = c * upper + s * lower
                entries[j + 1] = c * lower - s * upper
            c, s, magnitude = _givens(entries[column], entries[column + 1])
            cosine[column], sine[column] = c, s
            entries[column], entries[column + 1] = magnitude, 0.0
            triangle[: column + 2, column] = torch.tensor(entries, dtype=torch.float64)
            rotated[column + 1] = -s * rotated[column]
            rotated[column] = c * rotated[column]
            history.append(abs(rotated[column + 1]) / beta)
            if breakdown:
                break
        size = column + 1
        upper_triangle = triangle[:size, :size]
        target = torch.tensor(rotated[:size], dtype=torch.float64)
        if float(upper_triangle[size - 1, size - 1]) == 0.0:
            # pseudo-solve: a zero pivot can only be the last one (breakdown)
            target[size - 1] = 0.0
            upper_triangle = upper_triangle.clone()
            upper_triangle[size - 1, size - 1] = 1.0
        coordinates = torch.linalg.solve_triangular(
            upper_triangle, target.reshape(size, 1), upper=True).reshape(size)
        y = y + coordinates.to(device=device, dtype=dtype) @ basis[:size]
        residual = rhs - matvec(precond(y)).detach()
        matvecs += per_iteration
        residual_norm = float(torch.linalg.vector_norm(residual))
        if not math.isfinite(residual_norm):
            raise RuntimeError('non-finite pgmres residual')
        if residual_norm <= tolerance or breakdown:
            break
    info = {
        'matvecs': matvecs, 'cycles': used_cycles, 'restart': restart,
        'degree': degree, 'per_iteration_matvecs': per_iteration,
        'budget_matvecs': construction + cycles * restart * per_iteration,
        'converged': residual_norm <= tolerance,
        'true_residual': residual_norm, 'tolerance': tolerance,
        'history_last': history[-1] if history else None,
    }
    if residual_norm > tolerance:
        raise RuntimeError(
            f'preconditioned DAE GMRES did not converge in {matvecs} matvecs '
            f'({used_cycles} cycles x restart {restart}, degree {degree}); '
            f'true residual {residual_norm:.6g}, tolerance {tolerance:.6g}')
    return precond(y).detach(), info



def _block_jacobians(scaled, z: Tensor, split: int) -> tuple[Tensor, Tensor]:
    """Build only the two diagonal Jacobian blocks and reject false topology.

    The upper-right structural zero is checked at every Newton/adjoint root.
    This opt-in contract prevents a later temperature-dependent hydraulic law
    from silently receiving an invalid triangular linear solve.
    """
    hydraulic = z[:split].detach()
    thermal = z[split:].detach()
    def joined(h, t):
        return scaled(torch.cat((h, t)))
    upper_right = torch.autograd.functional.jacobian(
        lambda t: joined(hydraulic, t)[:split], thermal, vectorize=True,
    )
    if bool((upper_right != 0).any()):
        raise ValueError('declared DAE block is not block lower triangular')
    h = torch.autograd.functional.jacobian(
        lambda x: joined(x, thermal)[:split], hydraulic, vectorize=True,
    )
    t = torch.autograd.functional.jacobian(
        lambda x: joined(hydraulic, x)[split:], thermal, vectorize=True,
    )
    return h, t


def _solve_block(matrix: Tensor, rhs: Tensor, name: str) -> Tensor:
    solution, info = torch.linalg.solve_ex(matrix, rhs, check_errors=False)
    if bool(info != 0) or not bool(torch.isfinite(solution).all()):
        raise RuntimeError(f'singular or non-finite DAE {name} block Jacobian')
    return solution.detach()


def _block_direction(scaled, z: Tensor, rhs: Tensor, split: int) -> Tensor:
    hydraulic, thermal = _block_jacobians(scaled, z, split)
    delta_h = _solve_block(hydraulic, rhs[:split], 'hydraulic')
    fixed_t = z[split:].detach()
    cross_delta = torch.func.jvp(
        lambda h: scaled(torch.cat((h, fixed_t)))[split:],
        (z[:split].detach(),), (delta_h,),
    )[1].detach()
    delta_t = _solve_block(thermal, rhs[split:] - cross_delta, 'thermal')
    return torch.cat((delta_h, delta_t))


def _block_adjoint(scaled, z: Tensor, residual: Tensor,
                   rhs: Tensor, split: int) -> tuple[Tensor, Tensor]:
    hydraulic, thermal = _block_jacobians(scaled, z, split)
    dual_t = _solve_block(thermal.T, rhs[split:], 'thermal adjoint')
    cross_dual = torch.autograd.grad(
        residual[split:], z, grad_outputs=dual_t, retain_graph=True,
    )[0][:split].detach()
    dual_h = _solve_block(hydraulic.T, rhs[:split] - cross_dual, 'hydraulic adjoint')
    error = torch.cat((
        hydraulic.T @ dual_h + cross_dual - rhs[:split],
        thermal.T @ dual_t - rhs[split:],
    ))
    return torch.cat((dual_h, dual_t)), error


def _dense_jacobian(residual, state):
    """Complete first derivative without the legacy CPU batched-VJP path.

    The complete matrix is retained for the original dense solve/checks.
    CUDA, small states and non-vector inputs retain the original API.
    This derivative is deliberately detached, as create_graph=False was.
    """
    if state.device.type == 'cpu' and state.ndim == 1 and state.numel() > 64:
        with torch.enable_grad():
            return torch.func.jacfwd(residual)(state.detach()).detach()
    return torch.autograd.functional.jacobian(residual,state,vectorize=True)


def _newton(residual, guess, parameters, state_scale, residual_scale, options, linear_partition):
    if (options.max_iterations<1 or options.max_backtracks<1 or options.atol<=0 or options.rtol<0
        or options.linear_solver not in ('dense','sparse','gmres','pgmres','block')
        or options.linear_max_iterations<1 or options.linear_atol<=0 or options.linear_rtol<0):
        raise ValueError('invalid Newton options')
    if options.linear_solver=='block' and linear_partition is None:
        raise ValueError('block DAE solver requires a declared linear_partition')
    z=guess.detach()/state_scale
    params=tuple(p.detach() for p in parameters)
    if not all(bool(torch.isfinite(p).all()) for p in params):
        raise ValueError('non-finite DAE parameter')
    def scaled(value):
        r=residual(value*state_scale,*params)/residual_scale
        if r.shape!=guess.shape:
            raise ValueError('DAE residual must be square and match the state')
        return r
    initial=None;backtracks=0
    with torch.enable_grad():
        for iteration in range(options.max_iterations+1):
            z=z.detach().requires_grad_(True)
            # Value-only call: r feeds norms/merit and the detached GMRES rhs;
            # the Jacobian path recomputes its own graph, so no backward ever
            # runs through this call. no_grad lets routing rule 3 (see
            # compile_support.py) use compiled kernels on this hot path.
            with torch.no_grad():
                r=scaled(z)
            if not bool(torch.isfinite(r).all()):
                raise RuntimeError('non-finite DAE residual')
            norm=float(r.detach().abs().max())
            if initial is None:initial=norm
            tolerance=options.atol+options.rtol*initial
            if norm<=tolerance:
                return (z*state_scale).detach(),NewtonReport(iteration,norm,backtracks)
            if iteration==options.max_iterations:break
            if options.linear_solver=='dense':
                jacobian=_dense_jacobian(scaled,z)
                direction,info=torch.linalg.solve_ex(jacobian,-r,check_errors=False)
                direction=direction.detach()
                if bool(info!=0):
                    raise RuntimeError('singular DAE Newton Jacobian; inspect constraints and reference states')
            elif options.linear_solver=='gmres':
                direction=_gmres(
                    lambda vector: torch.func.jvp(scaled,(z,),(vector,))[1],
                    -r.detach(),options,
                )
            elif options.linear_solver=='pgmres':
                direction,_=_pgmres(
                    lambda vector: torch.func.jvp(scaled,(z,),(vector,))[1],
                    -r.detach(),options,
                )
            elif options.linear_solver=='sparse':
                owner,kind=dae_sparse.cache_identity(residual)
                direction=dae_sparse.direction(owner,scaled,z.detach(),-r.detach(),kind)
            else:
                direction=_block_direction(scaled,z,-r.detach(),linear_partition)
            if not bool(torch.isfinite(direction).all()):
                raise RuntimeError('non-finite DAE Newton direction')
            merit=float(r.detach().square().sum())
            accepted=False
            for trial in range(options.max_backtracks):
                damping=2.**(-trial)
                candidate=z.detach()+damping*direction
                with torch.no_grad():  # value-only merit check; routing rule 3
                    candidate_r=scaled(candidate).detach()
                if bool(torch.isfinite(candidate_r).all()) and (
                    float(candidate_r.square().sum()) <= (1-1e-4*damping)*merit
                    or float(candidate_r.abs().max())<=tolerance
                ):
                    z=candidate;backtracks+=trial;accepted=True;break
            if not accepted:
                raise RuntimeError(f'DAE Newton line search failed, scaled residual {norm:.6g}')
    raise RuntimeError(f'DAE Newton did not converge in {options.max_iterations} iterations; scaled residual {norm:.6g}')


class _ImplicitRoot(torch.autograd.Function):
    @staticmethod
    def forward(ctx, residual, state_scale, residual_scale, options, linear_partition, guess, *parameters):
        y,report=_newton(residual,guess,parameters,state_scale,residual_scale,options,linear_partition)
        ctx.residual=residual;ctx.state_scale=state_scale;ctx.residual_scale=residual_scale
        ctx.options=options;ctx.linear_partition=linear_partition
        ctx.save_for_backward(y,*parameters)
        stats=y.new_tensor([report.iterations,report.scaled_residual_inf,report.backtracks])
        ctx.mark_non_differentiable(stats)
        return y,stats

    @staticmethod
    @once_differentiable
    def backward(ctx,grad_y,grad_stats):
        y,*parameters=ctx.saved_tensors
        values=[p.detach().requires_grad_(True) for p in parameters]
        with torch.enable_grad():
            z=(y/ctx.state_scale).detach().requires_grad_(True)
            def scaled(value):return ctx.residual(value*ctx.state_scale,*values)/ctx.residual_scale
            r=scaled(z)
            rhs=grad_y*ctx.state_scale
            if ctx.options.linear_solver=='dense':
                jacobian=_dense_jacobian(scaled,z)
                dual,info=torch.linalg.solve_ex(jacobian.T,rhs,check_errors=False)
                if bool(info!=0):raise RuntimeError('singular DAE adjoint Jacobian')
                error=jacobian.T@dual-rhs
            elif ctx.options.linear_solver=='gmres':
                def transpose_matvec(vector):
                    return torch.autograd.grad(r,z,grad_outputs=vector,retain_graph=True)[0]
                dual=_gmres(transpose_matvec,rhs.detach(),ctx.options)
                error=transpose_matvec(dual).detach()-rhs
            elif ctx.options.linear_solver=='pgmres':
                # M1 stage: the transpose operator's Krylov space STAGNATES
                # under the same polynomial family (measured on NV 1604:
                # adjoint plateau 0.0044 @ 6983 matvecs / 25 cycles — budget
                # is not the binding constraint; evidence in docs/benchmarks/
                # dae-pgmres-nv-20260928/). Until M2 supplies a transpose
                # preconditioner the adjoint uses the exact dense Jacobian:
                # one build per step, and the gradient is independent of the
                # forward linear path because the root is unique. Documented
                # in the pgmres receipt, never silent.
                jacobian=_dense_jacobian(scaled,z)
                dual,info=torch.linalg.solve_ex(jacobian.T,rhs,check_errors=False)
                if bool(info!=0):raise RuntimeError('singular DAE adjoint Jacobian')
                error=jacobian.T@dual-rhs
            elif ctx.options.linear_solver=='sparse':
                owner,kind=dae_sparse.cache_identity(ctx.residual)
                dual,error=dae_sparse.adjoint(owner,scaled,z,r,rhs.detach(),kind)
            else:
                dual,error=_block_adjoint(scaled,z,r,rhs.detach(),ctx.linear_partition)
            linear_tolerance=max(1e-8,128*torch.finfo(y.dtype).eps)
            if not bool(torch.isfinite(dual).all()) or float(error.abs().max())>linear_tolerance*(1+float(rhs.abs().max())):
                raise RuntimeError('DAE adjoint linear solve failed its residual check')
            grads=torch.autograd.grad(r,values,grad_outputs=-dual.detach(),allow_unused=True)
        grads=[torch.zeros_like(p) if g is None else g for p,g in zip(parameters,grads)]
        # The converged root is independent of its numerical warm-start guess.
        # Physical history/initial data enter as explicit parameters below.
        return (None,None,None,None,None,None,*grads)


def _tensor(value,like):
    if isinstance(value,Tensor) and (value.device!=like.device or value.dtype!=like.dtype):
        raise ValueError('DAE input tensors must share the state device and dtype; move them once before solving')
    result=torch.as_tensor(value,dtype=like.dtype,device=like.device)
    if not bool(torch.isfinite(result).all()):
        raise ValueError('DAE inputs must be finite tensors')
    return result


def _options(options,like):
    # Float32 has a different attainable residual floor. Explicit user options
    # are never silently relaxed, and all MMS reference cases use float64.
    return options or (NewtonOptions(atol=1e-6,rtol=1e-6) if like.dtype==torch.float32 else NewtonOptions())


def _root(problem,residual,guess,parameters,options):
    problem.validate(guess)
    # The sparse path caches its Jacobian pattern on the equation graph behind
    # the problem; the closures built per solve are not stable keys.
    try:residual._dae_sparse_owner=getattr(problem.residual,'__self__',None)
    except AttributeError:pass
    y,stats=_ImplicitRoot.apply(residual,problem.state_scale,problem.residual_scale,
                               _options(options,guess),problem.linear_partition,guess,*parameters)
    return y,NewtonReport(int(stats[0]),float(stats[1]),int(stats[2]))


def _admit_parameters(problem,theta):
    if problem.parameter_validator is not None:problem.parameter_validator(theta)


def _accept_state(problem,y,theta):
    if problem.state_validator is not None:problem.state_validator(y,theta)


def solve_steady(problem:DAEProblem,guess:Tensor,parameters:Tensor,*,time=0.,options=None,conserved=None):
    """Solve F(t,y,0,theta)=0 directly; no long-transient approximation."""
    guess=_tensor(guess,problem.state_scale)
    theta,t=_tensor(parameters,guess),_tensor(time,guess)
    _admit_parameters(problem,theta)
    def residual(y,theta,t):return problem.residual(t,y,torch.zeros_like(y),theta)
    if conserved is None:
        y,report=_root(problem,residual,guess,(theta,t),options)
        _accept_state(problem,y,theta)
        return y,report
    # A disconnected insulated storage can retain arbitrary energy at steady
    # state. Select its conserved value explicitly; do not add heat leakage to F.
    # This interface requires state and neutral residual rows to share indices.
    mask,reference=conserved
    reference=_tensor(reference,guess)
    if mask.shape!=guess.shape or mask.dtype!=torch.bool or mask.device!=guess.device or reference.shape!=guess.shape or bool((mask & ~problem.differential_mask).any()):
        raise ValueError('invalid conserved-storage selection')
    def constrained(y,theta,t,reference):
        original=residual(y,theta,t)
        invariant=(y-reference)*problem.residual_scale/problem.state_scale
        return torch.where(mask,invariant,original)
    y,report=_root(problem,constrained,guess,(theta,t,reference),options)
    original=residual(y,theta,t)/problem.residual_scale
    tolerance=10*(_options(options,guess).atol+_options(options,guess).rtol)
    if not bool(torch.isfinite(original).all()) or float(original.detach().abs().max())>tolerance:
        raise RuntimeError('conserved-state selection cannot satisfy the original DAE steady residual; a steady state may not exist')
    _accept_state(problem,y,theta)
    return y,report


def backward_euler_step(problem:DAEProblem,previous:Tensor,parameters:Tensor,*,time,dt,options=None):
    """Advance one accepted step, differentiable in theta, history, dt and time."""
    previous=_tensor(previous,problem.state_scale)
    theta,t,delta=(_tensor(value,previous) for value in (parameters,time,dt))
    _admit_parameters(problem,theta)
    if delta.ndim!=0 or not bool(delta>0):raise ValueError('dt must be a positive scalar')
    def residual(y,old,theta,t,delta):
        ydot=torch.where(problem.differential_mask,(y-old)/delta,torch.zeros_like(y))
        return problem.residual(t,y,ydot,theta)
    y,report=_root(problem,residual,previous,(previous,theta,t,delta),options)
    _accept_state(problem,y,theta)
    return y,report


def consistent_initialize(problem:DAEProblem,guess:Tensor,parameters:Tensor,*,time=0.,options=None):
    """Keep differential storage states; solve algebraic values and storage rates."""
    guess=_tensor(guess,problem.state_scale)
    theta,t=_tensor(parameters,guess),_tensor(time,guess)
    _admit_parameters(problem,theta)
    problem.validate(guess)
    mask=problem.differential_mask
    initial=torch.where(mask,torch.zeros_like(guess),guess)
    def residual(unknown,given,theta,t):
        y=torch.where(mask,given,unknown)
        ydot=torch.where(mask,unknown,torch.zeros_like(unknown))
        return problem.residual(t,y,ydot,theta)
    unknown,info=_root(problem,residual,initial,(guess,theta,t),options)
    y=torch.where(mask,guess,unknown)
    _accept_state(problem,y,theta)
    return y,torch.where(mask,unknown,torch.zeros_like(unknown)),info
