"""
Omni-Auditor — Static Analysis Engine (analyzer.py)
===========================================================

This module implements the structural analysis pipeline:

1.  **DeepASTVisitor** — A recursive AST traverser that constructs a
    multi-scope Control Flow Graph (CFG) with basic-block granularity.
2.  **ControlFlowGraph** — A first-class directed graph representation
    of program control flow, preserving sequential, branching, looping,
    and exceptional edges.
3.  **SpectralGraphAnalyzer** — Converts the CFG into dense/sparse
    matrix representations and applies rigorous Spectral Graph Theory:

    *   Adjacency matrix ``A`` (directed and symmetrised undirected).
    *   Degree matrix ``D``.
    *   Combinatorial Graph Laplacian  ``L = D - A``.
    *   Symmetric Normalised Laplacian ``L_sym = I - D^{-1/2} A D^{-1/2}``
        (with safe pseudo-inverse handling for isolated vertices).
    *   Random-Walk Laplacian ``L_rw = I - D^{-1} A``.
    *   Complete eigen-decomposition for ``n <= DENSE_THRESHOLD``;
        shift-invert sparse solvers for larger graphs.
    *   Algebraic connectivity (Fiedler value), spectral gap,
        spectral radius, graph energy, Von-Neumann entropy,
        Rényi entropy of order-2, effective rank, and a
        spectral modularity estimate via the Fiedler vector.

4.  **Analyzer** — High-level orchestrator that emits a
    ``StructuralAnalysisResult`` containing vectorised NumPy arrays
    suitable for downstream statistical validation.

All public interfaces are strictly typed (``from __future__ import annotations``).
"""

from __future__ import annotations

import ast
import enum
import hashlib
import json
import logging
import shutil
import uuid
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.linalg import eigh
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.sparse.linalg import eigsh

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Threshold beyond which we switch from dense (``scipy.linalg.eigh``)
# to sparse shift-invert eigensolvers (``scipy.sparse.linalg.eigsh``).
_DENSE_EIGEN_THRESHOLD: int = 1024

# Rényi entropy order used for the spectral entropy probe.
_RENYI_ORDER: float = 2.0

# Numerical stability epsilon for degree pseudo-inverses.
_EPS: float = 1e-12

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


@enum.unique
class BlockType(enum.IntEnum):
    """Taxonomy of basic block roles in the CFG."""

    ENTRY = 0
    EXIT = 1
    BASIC = 2
    BRANCH = 3
    LOOP_HEADER = 4
    LOOP_BODY = 5
    EXCEPTION = 6
    FUNCTION_ENTRY = 7
    FUNCTION_EXIT = 8


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class BasicBlock:
    """A vertex in the Control Flow Graph.

    Attributes
    ----------
    uid:
        Immutable UUID-4 identifier.
    block_type:
        Semantic role of the block.
    statements:
        Ordered list of AST statement nodes contained in this block.
    predecessors:
        Set of predecessor block UIDs.
    successors:
        Set of successor block UIDs.
    """

    uid: str = field(default_factory=lambda: str(uuid.uuid4()))
    block_type: BlockType = BlockType.BASIC
    statements: list[ast.stmt] = field(default_factory=list)
    predecessors: set[str] = field(default_factory=set)
    successors: set[str] = field(default_factory=set)

    def add_statement(self, stmt: ast.stmt) -> None:
        """Append an AST statement to this block."""
        self.statements.append(stmt)

    @property
    def statement_count(self) -> int:
        return len(self.statements)


# ---------------------------------------------------------------------------
# Control Flow Graph
# ---------------------------------------------------------------------------


class ControlFlowGraph:
    """Directed graph representation of a single lexical scope.

    The graph is stored as an adjacency list (``successors`` / ``predecessors``
    inside each ``BasicBlock``) and as an explicit edge set for fast matrix
    materialisation.
    """

    def __init__(self, name: str = "<unnamed>") -> None:
        self.name: str = name
        self._blocks: dict[str, BasicBlock] = {}
        self._entry: BasicBlock = self._create_block(BlockType.ENTRY)
        self._exit: BasicBlock = self._create_block(BlockType.EXIT)
        self._edges: set[tuple[str, str]] = set()

    # -- internal helpers --------------------------------------------------

    def _create_block(self, block_type: BlockType) -> BasicBlock:
        block = BasicBlock(block_type=block_type)
        self._blocks[block.uid] = block
        return block

    # -- public API --------------------------------------------------------

    def create_block(self, block_type: BlockType = BlockType.BASIC) -> BasicBlock:
        """Allocate a new basic block of the requested type."""
        return self._create_block(block_type)

    @property
    def entry_block(self) -> BasicBlock:
        return self._entry

    @property
    def exit_block(self) -> BasicBlock:
        return self._exit

    @property
    def blocks(self) -> dict[str, BasicBlock]:
        """Read-only view of the block dictionary."""
        return self._blocks

    def add_edge(self, source: BasicBlock | str, target: BasicBlock | str) -> None:
        """Add a directed edge ``source -> target``.

        Idempotent: duplicate edges are ignored.
        """
        s_id = source.uid if isinstance(source, BasicBlock) else source
        t_id = target.uid if isinstance(target, BasicBlock) else target
        if s_id not in self._blocks or t_id not in self._blocks:
            raise ValueError("Source or target block does not belong to this CFG.")
        self._blocks[s_id].successors.add(t_id)
        self._blocks[t_id].predecessors.add(s_id)
        self._edges.add((s_id, t_id))

    @property
    def edge_list(self) -> list[tuple[str, str]]:
        """Return the edge list as ordered pairs of UIDs."""
        return list(self._edges)

    def get_index_mapping(self) -> dict[str, int]:
        """Return a deterministic ``uid -> matrix_index`` map.

        Because ``_blocks`` preserves insertion order (Python >=3.7),
        the entry block is guaranteed to be index ``0`` and the exit
        block index ``1`` if no other blocks were created before them.
        """
        return {uid: idx for idx, uid in enumerate(self._blocks)}

    def to_adjacency_matrix(self, symmetrize: bool = True) -> NDArray[np.float64]:
        """Materialise the adjacency matrix.

        Parameters
        ----------
        symmetrize:
            If ``True``, returns ``max(A, A.T)`` (undirected unweighted
            overlay).  Spectral clustering and Laplacian analysis require
            a symmetric matrix.
        """
        n = len(self._blocks)
        idx_map = self.get_index_mapping()
        A = np.zeros((n, n), dtype=np.float64)
        for s_id, t_id in self._edges:
            i, j = idx_map[s_id], idx_map[t_id]
            A[i, j] = 1.0
        if symmetrize:
            A = np.maximum(A, A.T)
        return A


# ---------------------------------------------------------------------------
# AST Visitor
# ---------------------------------------------------------------------------


class DeepASTVisitor(ast.NodeVisitor):
    """Constructs a ``ControlFlowGraph`` by recursive depth-first AST traversal.

    The visitor is scope-aware: every ``FunctionDef`` / ``AsyncFunctionDef``
    spawns a *nested* ``ControlFlowGraph`` that is stored in the
    ``_function_cfgs`` registry.  The parent scope treats the function
    definition as an opaque statement node.
    """

    def __init__(self, cfg: ControlFlowGraph) -> None:
        self.cfg: ControlFlowGraph = cfg
        self._current_block: BasicBlock = cfg.entry_block
        self._current_unreachable: bool = False
        self._loop_stack: list[tuple[BasicBlock, BasicBlock]] = []
        self._function_cfgs: dict[str, ControlFlowGraph] = {}

    # -- factory -----------------------------------------------------------

    @classmethod
    def build(
        cls, tree: ast.AST, name: str = "<module>"
    ) -> tuple[ControlFlowGraph, dict[str, ControlFlowGraph]]:
        """Parse an AST subtree and return the populated CFG plus function registry."""
        cfg = ControlFlowGraph(name=name)
        visitor = cls(cfg)
        visitor.visit(tree)
        # Conservatively link the final block to the scope exit.
        cfg.add_edge(visitor._current_block, cfg.exit_block)
        return cfg, visitor._function_cfgs

    # -- generic helpers ---------------------------------------------------

    def _make_key(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        """Generate a reproducible, collision-resistant key for a function."""
        line = getattr(node, "lineno", 0)
        col = getattr(node, "col_offset", 0)
        return f"{node.name}@{line}:{col}"

    def _visit_statement(self, node: ast.stmt) -> None:
        """Dispatch a single statement to the appropriate specialised handler."""
        if self._current_unreachable:
            return

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.visit_FunctionDef(node)
        elif isinstance(node, ast.ClassDef):
            self.visit_ClassDef(node)
        elif isinstance(node, ast.If):
            self.visit_If(node)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self.visit_For(node)
        elif isinstance(node, ast.While):
            self.visit_While(node)
        elif isinstance(node, ast.Try):
            self.visit_Try(node)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            self.visit_With(node)
        elif isinstance(node, ast.Break):
            self.visit_Break(node)
        elif isinstance(node, ast.Continue):
            self.visit_Continue(node)
        elif isinstance(node, ast.Return):
            self.visit_Return(node)
        elif isinstance(node, ast.Raise):
            self.visit_Raise(node)
        elif isinstance(node, (ast.Global, ast.Nonlocal, ast.Pass, ast.Expr)):
            # Statements with no intra-procedural control-flow side effects.
            self._current_block.add_statement(node)
        else:
            # Assignments, AugAssign, AnnAssign, Assert, Delete, Import, etc.
            self._current_block.add_statement(node)

    # -- top-level entry ---------------------------------------------------

    def visit_Module(self, node: ast.Module) -> None:  # noqa: N802
        for stmt in node.body:
            self._visit_statement(stmt)

    # -- compound statements -----------------------------------------------

    def visit_FunctionDef(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:  # noqa: N802
        # Opaque in the parent scope.
        self._current_block.add_statement(node)

        # Build an isolated CFG for the function body.
        inner_cfg = ControlFlowGraph(name=node.name)
        inner_visitor = DeepASTVisitor(inner_cfg)
        for stmt in node.body:
            inner_visitor._visit_statement(stmt)
        inner_cfg.add_edge(inner_visitor._current_block, inner_cfg.exit_block)
        self._function_cfgs[self._make_key(node)] = inner_cfg

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._current_block.add_statement(node)
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_cfg = ControlFlowGraph(name=f"{node.name}.{item.name}")
                method_visitor = DeepASTVisitor(method_cfg)
                for stmt in item.body:
                    method_visitor._visit_statement(stmt)
                method_cfg.add_edge(
                    method_visitor._current_block, method_cfg.exit_block
                )
                self._function_cfgs[self._make_key(item)] = method_cfg
            # Class-level assignments are executed at definition time;
            # we do not model them in the CFG to avoid cross-scope pollution.

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        header = self._current_block
        header.add_statement(node)
        header.block_type = BlockType.BRANCH

        then_block = self.cfg.create_block(BlockType.BASIC)
        self.cfg.add_edge(header, then_block)

        merge_block = self.cfg.create_block(BlockType.BASIC)

        if node.orelse:
            else_block = self.cfg.create_block(BlockType.BASIC)
            self.cfg.add_edge(header, else_block)
        else:
            else_block = None
            self.cfg.add_edge(header, merge_block)

        # Then branch
        self._current_block = then_block
        self._current_unreachable = False
        for stmt in node.body:
            self._visit_statement(stmt)
        self.cfg.add_edge(self._current_block, merge_block)

        # Else branch
        if else_block is not None:
            self._current_block = else_block
            self._current_unreachable = False
            for stmt in node.orelse:
                self._visit_statement(stmt)
            self.cfg.add_edge(self._current_block, merge_block)

        self._current_block = merge_block
        self._current_unreachable = False

    def visit_For(self, node: ast.For | ast.AsyncFor) -> None:  # noqa: N802
        header = self._current_block
        header.add_statement(node)
        header.block_type = BlockType.LOOP_HEADER

        body_block = self.cfg.create_block(BlockType.LOOP_BODY)
        self.cfg.add_edge(header, body_block)

        after_block = self.cfg.create_block(BlockType.BASIC)
        self.cfg.add_edge(header, after_block)

        self._loop_stack.append((header, after_block))

        self._current_block = body_block
        self._current_unreachable = False
        for stmt in node.body:
            self._visit_statement(stmt)
        # Back-edge
        self.cfg.add_edge(self._current_block, header)

        self._loop_stack.pop()

        if node.orelse:
            else_block = self.cfg.create_block(BlockType.BASIC)
            self.cfg.add_edge(after_block, else_block)
            self._current_block = else_block
            self._current_unreachable = False
            for stmt in node.orelse:
                self._visit_statement(stmt)
            merge_block = self.cfg.create_block(BlockType.BASIC)
            self.cfg.add_edge(self._current_block, merge_block)
            self._current_block = merge_block
            self._current_unreachable = False
        else:
            self._current_block = after_block
            self._current_unreachable = False

    def visit_While(self, node: ast.While) -> None:  # noqa: N802
        # Isomorphic to ``for`` from a CFG perspective.
        header = self._current_block
        header.add_statement(node)
        header.block_type = BlockType.LOOP_HEADER

        body_block = self.cfg.create_block(BlockType.LOOP_BODY)
        self.cfg.add_edge(header, body_block)

        after_block = self.cfg.create_block(BlockType.BASIC)
        self.cfg.add_edge(header, after_block)

        self._loop_stack.append((header, after_block))

        self._current_block = body_block
        self._current_unreachable = False
        for stmt in node.body:
            self._visit_statement(stmt)
        self.cfg.add_edge(self._current_block, header)

        self._loop_stack.pop()

        if node.orelse:
            else_block = self.cfg.create_block(BlockType.BASIC)
            self.cfg.add_edge(after_block, else_block)
            self._current_block = else_block
            self._current_unreachable = False
            for stmt in node.orelse:
                self._visit_statement(stmt)
            merge_block = self.cfg.create_block(BlockType.BASIC)
            self.cfg.add_edge(self._current_block, merge_block)
            self._current_block = merge_block
            self._current_unreachable = False
        else:
            self._current_block = after_block
            self._current_unreachable = False

    def visit_Try(self, node: ast.Try) -> None:  # noqa: N802
        # Conservative approximation: exceptions may propagate from the
        # *entry* of the try body to any handler.
        self._current_block.add_statement(node)

        try_block = self.cfg.create_block(BlockType.BASIC)
        self.cfg.add_edge(self._current_block, try_block)

        self._current_block = try_block
        self._current_unreachable = False
        for stmt in node.body:
            self._visit_statement(stmt)
        try_exit = self._current_block

        merge_block = self.cfg.create_block(BlockType.BASIC)
        self.cfg.add_edge(try_exit, merge_block)

        for handler in node.handlers:
            h_block = self.cfg.create_block(BlockType.EXCEPTION)
            self.cfg.add_edge(try_block, h_block)
            self._current_block = h_block
            self._current_unreachable = False
            for stmt in handler.body:
                self._visit_statement(stmt)
            self.cfg.add_edge(self._current_block, merge_block)

        if node.orelse:
            else_block = self.cfg.create_block(BlockType.BASIC)
            self.cfg.add_edge(try_exit, else_block)
            self._current_block = else_block
            self._current_unreachable = False
            for stmt in node.orelse:
                self._visit_statement(stmt)
            self.cfg.add_edge(self._current_block, merge_block)

        if node.finalbody:
            finally_block = self.cfg.create_block(BlockType.BASIC)
            self.cfg.add_edge(merge_block, finally_block)
            self._current_block = finally_block
            self._current_unreachable = False
            for stmt in node.finalbody:
                self._visit_statement(stmt)
            post_finally = self.cfg.create_block(BlockType.BASIC)
            self.cfg.add_edge(self._current_block, post_finally)
            self._current_block = post_finally
            self._current_unreachable = False
        else:
            self._current_block = merge_block
            self._current_unreachable = False

    def visit_With(self, node: ast.With | ast.AsyncWith) -> None:  # noqa: N802
        # ``with`` is modelled as straight-line code; the context manager
        # protocol is treated as a black box for intra-procedural CFGs.
        self._current_block.add_statement(node)
        for stmt in node.body:
            self._visit_statement(stmt)

    # -- terminators -------------------------------------------------------

    def visit_Break(self, node: ast.Break) -> None:  # noqa: N802
        if not self._loop_stack:
            raise SyntaxError("'break' outside loop")
        self._current_block.add_statement(node)
        _, break_target = self._loop_stack[-1]
        self.cfg.add_edge(self._current_block, break_target)
        self._current_block = self.cfg.create_block(BlockType.BASIC)
        self._current_unreachable = True

    def visit_Continue(self, node: ast.Continue) -> None:  # noqa: N802
        if not self._loop_stack:
            raise SyntaxError("'continue' outside loop")
        self._current_block.add_statement(node)
        continue_target, _ = self._loop_stack[-1]
        self.cfg.add_edge(self._current_block, continue_target)
        self._current_block = self.cfg.create_block(BlockType.BASIC)
        self._current_unreachable = True

    def visit_Return(self, node: ast.Return) -> None:  # noqa: N802
        self._current_block.add_statement(node)
        self.cfg.add_edge(self._current_block, self.cfg.exit_block)
        self._current_block = self.cfg.create_block(BlockType.BASIC)
        self._current_unreachable = True

    def visit_Raise(self, node: ast.Raise) -> None:  # noqa: N802
        self._current_block.add_statement(node)
        # Conservative: exception leaves the scope (no fine-grained handler
        # resolution at this stage).
        self.cfg.add_edge(self._current_block, self.cfg.exit_block)
        self._current_block = self.cfg.create_block(BlockType.BASIC)
        self._current_unreachable = True


# ---------------------------------------------------------------------------
# Spectral analysis
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpectralProfile:
    """Immutable container for the spectral decomposition of a CFG.

    Attributes
    ----------
    adjacency_directed:
        Directed adjacency matrix ``A_dir`` (n x n).
    adjacency_undirected:
        Symmetrised undirected adjacency ``A_und = max(A_dir, A_dir.T)``.
    degree_matrix:
        Diagonal degree matrix ``D`` of ``A_und``.
    laplacian_combinatorial:
        ``L = D - A_und``.
    laplacian_normalized:
        ``L_sym = I - D^{-1/2} A_und D^{-1/2}`` (zeros for isolated vertices).
    laplacian_random_walk:
        ``L_rw = I - D^{-1} A_und``.
    eigenvalues_combinatorial:
        Sorted eigenvalues ``λ_1 <= ... <= λ_n`` of ``L``.
    eigenvalues_normalized:
        Sorted eigenvalues of ``L_sym``.
    eigenvectors_combinatorial:
        Corresponding eigenvectors of ``L`` (column-major).
    fiedler_value:
        Algebraic connectivity ``λ_2``.
    spectral_gap:
        ``λ_2 - λ_1`` (for connected graphs ``λ_1 = 0``).
    algebraic_connectivity:
        Alias for ``λ_2``.
    spectral_radius:
        Largest eigenvalue ``λ_n`` of ``L``.
    von_neumann_entropy:
        Shannon entropy of the normalised Laplacian spectrum treated as a
        probability distribution: ``S = - sum p_i log(p_i)`` where
        ``p_i = λ_i / trace(L)``.
    renyi_entropy_2:
        Rényi entropy of order 2 of the same spectrum:
        ``H_2 = -log( sum p_i^2 )``.
    graph_energy:
        ``E(G) = sum_i |μ_i|`` where ``μ_i`` are the eigenvalues of the
        *adjacency* matrix (undirected).
    modularity_index:
        Spectral modularity estimate ``Q`` using the Fiedler vector bipartition.
    effective_rank:
        Effective rank of ``A_und`` via the Shannon entropy of its singular-value
        distribution: ``rank_eff = exp( - sum σ_i' log σ_i' )``.
    normalized_fiedler:
        ``λ_2 / λ_n`` (in [0, 1] for connected graphs).
    eigengap_ratio:
        ``(λ_3 - λ_2) / λ_2`` when defined; measures clusterability.
    spectral_discrepancy:
        Variance of consecutive eigenvalue gaps.
    feature_vector:
        Concatenation of the above scalar metrics into a 1-D vector for the
        downstream validator.
    """

    adjacency_directed: NDArray[np.float64]
    adjacency_undirected: NDArray[np.float64]
    degree_matrix: NDArray[np.float64]
    laplacian_combinatorial: NDArray[np.float64]
    laplacian_normalized: NDArray[np.float64]
    laplacian_random_walk: NDArray[np.float64]
    eigenvalues_combinatorial: NDArray[np.float64]
    eigenvalues_normalized: NDArray[np.float64]
    eigenvectors_combinatorial: NDArray[np.float64]
    fiedler_value: float
    spectral_gap: float
    algebraic_connectivity: float
    spectral_radius: float
    von_neumann_entropy: float
    renyi_entropy_2: float
    graph_energy: float
    modularity_index: float
    effective_rank: float
    normalized_fiedler: float
    eigengap_ratio: float
    spectral_discrepancy: float
    feature_vector: NDArray[np.float64]


class SpectralGraphAnalyzer:
    """Applies rigorous Spectral Graph Theory to a ``ControlFlowGraph``.

    The workflow follows the standard recipe:

    1. Materialise ``A_dir`` and symmetrise to ``A_und``.
    2. Compute degree matrix ``D``.
    3. Build combinatorial, normalised, and random-walk Laplacians.
    4. Compute eigen-decomposition (dense or sparse adaptive).
    5. Derive spectral invariants (Fiedler value, entropy, modularity, ...).
    """

    DENSE_THRESHOLD: int = _DENSE_EIGEN_THRESHOLD

    def __init__(self, cfg: ControlFlowGraph) -> None:
        self.cfg: ControlFlowGraph = cfg
        self._n: int = len(cfg.blocks)
        self._idx_map: dict[str, int] = cfg.get_index_mapping()

    # -- matrix construction -----------------------------------------------

    def _build_directed_adjacency(self) -> NDArray[np.float64]:
        n = self._n
        A = np.zeros((n, n), dtype=np.float64)
        for s_id, t_id in self.cfg.edge_list:
            i, j = self._idx_map[s_id], self._idx_map[t_id]
            A[i, j] = 1.0
        return A

    def _build_laplacians(self, A_und: NDArray[np.float64]) -> tuple[
        NDArray[np.float64],
        NDArray[np.float64],
        NDArray[np.float64],
        NDArray[np.float64],
    ]:
        n = A_und.shape[0]
        degrees = np.sum(A_und, axis=1)
        D = np.diag(degrees)
        L_c = D - A_und

        # Normalised Laplacian with safe pseudo-inverse for isolated vertices.
        d_inv_sqrt = np.zeros_like(degrees)
        mask = degrees > _EPS
        d_inv_sqrt[mask] = 1.0 / np.sqrt(degrees[mask])
        D_inv_sqrt = np.diag(d_inv_sqrt)
        L_n = np.eye(n) - D_inv_sqrt @ A_und @ D_inv_sqrt

        # Random-walk Laplacian
        d_inv = np.zeros_like(degrees)
        d_inv[mask] = 1.0 / degrees[mask]
        D_inv = np.diag(d_inv)
        L_rw = np.eye(n) - D_inv @ A_und

        return L_c, L_n, L_rw, D

    # -- eigen-decomposition -----------------------------------------------

    def _eigendecompose(
        self, M: NDArray[np.float64]
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """Return sorted eigenvalues and column eigenvectors of symmetric ``M``."""
        n = M.shape[0]
        if n == 0:
            return (
                np.array([], dtype=np.float64),
                np.zeros((0, 0), dtype=np.float64),
            )
        if n <= self.DENSE_THRESHOLD:
            eigvals, eigvecs = eigh(M)
            idx = np.argsort(eigvals)
            return eigvals[idx].astype(np.float64), eigvecs[:, idx].astype(np.float64)

        # Sparse path: compute smallest and largest eigenvalues separately.
        k_small = min(n - 1, 128)
        try:
            eigvals_s, eigvecs_s = eigsh(M, k=k_small, sigma=0.0, which="LM")
            idx_s = np.argsort(eigvals_s)
            eigvals_s = eigvals_s[idx_s]
            eigvecs_s = eigvecs_s[:, idx_s]
            return eigvals_s.astype(np.float64), eigvecs_s.astype(np.float64)
        except Exception as exc:  # pragma: no cover
            warnings.warn(
                f"Sparse eigensolver failed for '{self.cfg.name}' (n={n}): {exc}. "
                "Falling back to dense decomposition; memory usage may spike.",
                RuntimeWarning,
                stacklevel=3,
            )
            eigvals, eigvecs = eigh(M)
            idx = np.argsort(eigvals)
            return eigvals[idx].astype(np.float64), eigvecs[:, idx].astype(np.float64)

    # -- metric computation ------------------------------------------------

    def _compute_spectral_metrics(
        self,
        A_und: NDArray[np.float64],
        L_c: NDArray[np.float64],
        L_n: NDArray[np.float64],
        eig_c: NDArray[np.float64],
        eig_n: NDArray[np.float64],
        eigvecs_c: NDArray[np.float64],
    ) -> dict[str, float]:
        n = A_und.shape[0]
        m = float(np.sum(A_und) / 2.0)  # undirected edge count

        # Sanitise spectra
        eig_c = np.sort(np.maximum(eig_c, 0.0))
        eig_n = np.sort(np.clip(eig_n, 0.0, 2.0))

        # -- basic spectral invariants -------------------------------------
        fiedler = float(eig_c[1]) if len(eig_c) > 1 else 0.0
        spectral_gap = float(eig_c[1] - eig_c[0]) if len(eig_c) > 1 else 0.0
        spectral_radius = float(eig_c[-1]) if len(eig_c) > 0 else 0.0
        algebraic_connectivity = fiedler

        # -- entropic measures ---------------------------------------------
        trace_c = float(np.sum(eig_c))
        if trace_c > _EPS:
            p = eig_c / trace_c
            p = p[p > _EPS]
            von_neumann = float(-np.sum(p * np.log(p)))
            renyi_2 = float(-np.log(np.sum(p**2)))
        else:
            von_neumann = 0.0
            renyi_2 = 0.0

        # -- graph energy (adjacency spectrum) -----------------------------
        if n <= self.DENSE_THRESHOLD:
            eig_A = np.linalg.eigvalsh(A_und)
            graph_energy = float(np.sum(np.abs(eig_A)))
        else:
            try:
                k = min(n - 1, 100)
                eig_A = eigsh(A_und, k=k, which="LM", return_eigenvectors=False)
                graph_energy = float(np.sum(np.abs(eig_A)))
            except Exception:
                graph_energy = 0.0

        # -- spectral modularity (Fiedler bipartition) ---------------------
        modularity_index = 0.0
        if n > 2 and fiedler > _EPS:
            fiedler_vec = eigvecs_c[:, 1] if eigvecs_c.shape[1] > 1 else np.zeros(n)
            partition = fiedler_vec >= 0.0
            if np.any(partition) and np.any(~partition):
                k_deg = np.sum(A_und, axis=1)
                delta = np.equal.outer(partition, partition).astype(np.float64)
                Q_mat = (
                    A_und - np.outer(k_deg, k_deg) / (2.0 * m if m > _EPS else 1.0)
                ) * delta
                modularity_index = float(np.sum(Q_mat) / (2.0 * m if m > _EPS else 1.0))

        # -- effective rank (adjacency SVD entropy) ------------------------
        if n > 0:
            s = np.linalg.svd(A_und, compute_uv=False)
            if s[0] > _EPS:
                p_s = s / np.sum(s)
                p_s = p_s[p_s > _EPS]
                entropy_s = float(-np.sum(p_s * np.log(p_s)))
                effective_rank = float(np.exp(entropy_s))
            else:
                effective_rank = 0.0
        else:
            effective_rank = 0.0

        # -- higher-order spectral descriptors ------------------------------
        normalized_fiedler = (
            fiedler / spectral_radius if spectral_radius > _EPS else 0.0
        )
        eigengap_ratio = (
            float(eig_c[2] - eig_c[1]) / fiedler
            if len(eig_c) > 2 and fiedler > _EPS
            else 0.0
        )
        if len(eig_c) > 2:
            gaps = np.diff(eig_c)
            spectral_discrepancy = float(np.var(gaps))
        else:
            spectral_discrepancy = 0.0

        return {
            "order": float(n),
            "size": m,
            "fiedler": fiedler,
            "spectral_gap": spectral_gap,
            "algebraic_connectivity": algebraic_connectivity,
            "spectral_radius": spectral_radius,
            "von_neumann_entropy": von_neumann,
            "renyi_entropy_2": renyi_2,
            "graph_energy": graph_energy,
            "modularity_index": modularity_index,
            "effective_rank": effective_rank,
            "normalized_fiedler": normalized_fiedler,
            "eigengap_ratio": eigengap_ratio,
            "spectral_discrepancy": spectral_discrepancy,
        }

    # -- public entry ------------------------------------------------------

    def analyze(self) -> SpectralProfile:
        """Run the full spectral pipeline and return an immutable profile."""
        A_dir = self._build_directed_adjacency()
        A_und = np.maximum(A_dir, A_dir.T)

        L_c, L_n, L_rw, D = self._build_laplacians(A_und)
        eig_c, eigvecs_c = self._eigendecompose(L_c)
        eig_n, _ = self._eigendecompose(L_n)

        metrics = self._compute_spectral_metrics(
            A_und, L_c, L_n, eig_c, eig_n, eigvecs_c
        )

        # Fixed-order feature vector for the validator.
        feature_keys = [
            "order",
            "size",
            "fiedler",
            "spectral_gap",
            "algebraic_connectivity",
            "spectral_radius",
            "von_neumann_entropy",
            "renyi_entropy_2",
            "graph_energy",
            "modularity_index",
            "effective_rank",
            "normalized_fiedler",
            "eigengap_ratio",
            "spectral_discrepancy",
        ]
        feature_vector = np.array([metrics[k] for k in feature_keys], dtype=np.float64)

        return SpectralProfile(
            adjacency_directed=A_dir,
            adjacency_undirected=A_und,
            degree_matrix=D,
            laplacian_combinatorial=L_c,
            laplacian_normalized=L_n,
            laplacian_random_walk=L_rw,
            eigenvalues_combinatorial=eig_c,
            eigenvalues_normalized=eig_n,
            eigenvectors_combinatorial=eigvecs_c,
            feature_vector=feature_vector,
            fiedler_value=metrics["fiedler"],
            spectral_gap=metrics["spectral_gap"],
            algebraic_connectivity=metrics["algebraic_connectivity"],
            spectral_radius=metrics["spectral_radius"],
            von_neumann_entropy=metrics["von_neumann_entropy"],
            renyi_entropy_2=metrics["renyi_entropy_2"],
            graph_energy=metrics["graph_energy"],
            modularity_index=metrics["modularity_index"],
            effective_rank=metrics["effective_rank"],
            normalized_fiedler=metrics["normalized_fiedler"],
            eigengap_ratio=metrics["eigengap_ratio"],
            spectral_discrepancy=metrics["spectral_discrepancy"],
        )


# ---------------------------------------------------------------------------
# Orchestrator & result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StructuralAnalysisResult:
    """Top-level immutable result exported by the ``Analyzer``.

    Attributes
    ----------
    module_cfg:
        CFG for the module-level scope.
    function_cfgs:
        Mapping ``key -> CFG`` for every function / method discovered.
    module_spectral:
        Spectral profile of the module-level CFG.
    function_spectrals:
        Spectral profiles of each function CFG.
    aggregate_feature_vector:
        Fixed-dimension vector concatenating module features with
        mean / max / std statistics across all function profiles.
    node_feature_matrix:
        Per-node feature matrix for the module graph (n x f).
    node_index_map:
        UID-to-row-index mapping for ``node_feature_matrix``.
    """

    module_cfg: ControlFlowGraph
    function_cfgs: dict[str, ControlFlowGraph]
    module_spectral: SpectralProfile
    function_spectrals: dict[str, SpectralProfile]
    aggregate_feature_vector: NDArray[np.float64]
    node_feature_matrix: NDArray[np.float64]
    node_index_map: dict[str, int]


class CacheManager:
    """Disk-based cache for :class:`StructuralAnalysisResult` objects.

    Cache keys are SHA256 hex digests of the source code string.
    Objects are persisted as:
      * ``.npz`` — NumPy arrays (compressed, safe)
      * ``.json`` — metadata (block types, edges, scalars)
    Old ``.pkl`` files are detected, warned about, and skipped.
    An SHA256 integrity check guards against tampering or corruption.
    """

    def __init__(self, cache_dir: str | Path = ".omni_cache") -> None:
        self.cache_dir: Path = Path(cache_dir)
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key_to_npz(self, key: str) -> Path:
        return self.cache_dir / f"{key}.npz"

    def _key_to_json(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _key_to_pkl(self, key: str) -> Path:
        return self.cache_dir / f"{key}.pkl"

    def _compute_checksum(self, path: Path) -> str:
        """Return SHA256 hex digest over the file contents."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    # -- helpers to serialise / deserialise CFGs -----------------------------

    @staticmethod
    def _block_to_dict(block: BasicBlock) -> dict[str, Any]:
        return {
            "uid": block.uid,
            "block_type": int(block.block_type),
            "statement_count": block.statement_count,
            "predecessors": list(block.predecessors),
            "successors": list(block.successors),
        }

    @staticmethod
    def _dict_to_block(d: dict[str, Any]) -> BasicBlock:
        block = BasicBlock(
            uid=d["uid"],
            block_type=BlockType(d["block_type"]),
        )
        block.predecessors = set(d.get("predecessors", []))
        block.successors = set(d.get("successors", []))
        # Statements are opaque AST nodes — we store only counts; the
        # CFG structure (edges, block types) is what the cache needs.
        for _ in range(d.get("statement_count", 0)):
            block.add_statement(ast.Pass())
        return block

    @staticmethod
    def _cfg_to_dict(cfg: ControlFlowGraph) -> dict[str, Any]:
        return {
            "name": cfg.name,
            "blocks": {
                uid: CacheManager._block_to_dict(b)
                for uid, b in cfg.blocks.items()
            },
            "entry_uid": cfg.entry_block.uid,
            "exit_uid": cfg.exit_block.uid,
            "edges": list(cfg.edge_list),
        }

    @staticmethod
    def _dict_to_cfg(d: dict[str, Any]) -> ControlFlowGraph:
        cfg = ControlFlowGraph(name=d.get("name", "<unnamed>"))
        # Clear auto-created entry/exit so we can recreate exactly.
        cfg._blocks.clear()
        cfg._edges.clear()
        for uid, bd in d["blocks"].items():
            block = CacheManager._dict_to_block(bd)
            cfg._blocks[block.uid] = block
        cfg._entry = cfg._blocks[d["entry_uid"]]
        cfg._exit = cfg._blocks[d["exit_uid"]]
        for s_id, t_id in d.get("edges", []):
            cfg._blocks[s_id].successors.add(t_id)
            cfg._blocks[t_id].predecessors.add(s_id)
            cfg._edges.add((s_id, t_id))
        return cfg

    @staticmethod
    def _spectral_to_arrays(profile: SpectralProfile) -> dict[str, NDArray[np.float64]]:
        return {
            "adjacency_directed": profile.adjacency_directed,
            "adjacency_undirected": profile.adjacency_undirected,
            "degree_matrix": profile.degree_matrix,
            "laplacian_combinatorial": profile.laplacian_combinatorial,
            "laplacian_normalized": profile.laplacian_normalized,
            "laplacian_random_walk": profile.laplacian_random_walk,
            "eigenvalues_combinatorial": profile.eigenvalues_combinatorial,
            "eigenvalues_normalized": profile.eigenvalues_normalized,
            "eigenvectors_combinatorial": profile.eigenvectors_combinatorial,
            "feature_vector": profile.feature_vector,
        }

    @staticmethod
    def _spectral_from_arrays(data: dict[str, NDArray[np.float64]]) -> SpectralProfile:
        return SpectralProfile(
            adjacency_directed=data["adjacency_directed"],
            adjacency_undirected=data["adjacency_undirected"],
            degree_matrix=data["degree_matrix"],
            laplacian_combinatorial=data["laplacian_combinatorial"],
            laplacian_normalized=data["laplacian_normalized"],
            laplacian_random_walk=data["laplacian_random_walk"],
            eigenvalues_combinatorial=data["eigenvalues_combinatorial"],
            eigenvalues_normalized=data["eigenvalues_normalized"],
            eigenvectors_combinatorial=data["eigenvectors_combinatorial"],
            feature_vector=data["feature_vector"],
            fiedler_value=float(data["feature_vector"][2]),
            spectral_gap=float(data["feature_vector"][3]),
            algebraic_connectivity=float(data["feature_vector"][4]),
            spectral_radius=float(data["feature_vector"][5]),
            von_neumann_entropy=float(data["feature_vector"][6]),
            renyi_entropy_2=float(data["feature_vector"][7]),
            graph_energy=float(data["feature_vector"][8]),
            modularity_index=float(data["feature_vector"][9]),
            effective_rank=float(data["feature_vector"][10]),
            normalized_fiedler=float(data["feature_vector"][11]),
            eigengap_ratio=float(data["feature_vector"][12]),
            spectral_discrepancy=float(data["feature_vector"][13]),
        )

    # -- public API ----------------------------------------------------------

    def get(self, key: str) -> StructuralAnalysisResult | None:
        """Load a cached result by *key*, or ``None`` on miss / corruption."""
        npz_path = self._key_to_npz(key)
        json_path = self._key_to_json(key)
        pkl_path = self._key_to_pkl(key)

        if pkl_path.exists():
            logger.warning(
                "Deprecated pickle cache found for key %s. "
                "It will be ignored and regenerated. "
                "Remove %s to suppress this warning.",
                key,
                pkl_path,
            )
            return None

        if not npz_path.exists() or not json_path.exists():
            return None

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            return None

        # Integrity check (NPZ payload only — JSON is metadata)
        expected_checksum = meta.get("checksum")
        if expected_checksum is None:
            return None
        actual_checksum = self._compute_checksum(npz_path)
        if actual_checksum != expected_checksum:
            logger.warning(
                "Cache integrity check failed for key %s. "
                "Expected %s, got %s. Cache entry discarded.",
                key,
                expected_checksum,
                actual_checksum,
            )
            return None

        try:
            raw = np.load(npz_path, allow_pickle=False)
        except Exception:
            return None

        try:
            module_spectral = self._spectral_from_arrays(
                {k: raw[f"module_spectral.{k}"] for k in self._spectral_to_arrays(SpectralProfile(
                    adjacency_directed=np.zeros((1,1)), adjacency_undirected=np.zeros((1,1)),
                    degree_matrix=np.zeros((1,1)), laplacian_combinatorial=np.zeros((1,1)),
                    laplacian_normalized=np.zeros((1,1)), laplacian_random_walk=np.zeros((1,1)),
                    eigenvalues_combinatorial=np.zeros(1), eigenvalues_normalized=np.zeros(1),
                    eigenvectors_combinatorial=np.zeros((1,1)), feature_vector=np.zeros(14),
                    fiedler_value=0.0, spectral_gap=0.0, algebraic_connectivity=0.0,
                    spectral_radius=0.0, von_neumann_entropy=0.0, renyi_entropy_2=0.0,
                    graph_energy=0.0, modularity_index=0.0, effective_rank=0.0,
                    normalized_fiedler=0.0, eigengap_ratio=0.0, spectral_discrepancy=0.0,
                )).keys()}
            )
            function_spectrals: dict[str, SpectralProfile] = {}
            for func_key in meta["function_keys"]:
                function_spectrals[func_key] = self._spectral_from_arrays(
                    {k: raw[f"function_spectrals.{func_key}.{k}"] for k in self._spectral_to_arrays(SpectralProfile(
                        adjacency_directed=np.zeros((1,1)), adjacency_undirected=np.zeros((1,1)),
                        degree_matrix=np.zeros((1,1)), laplacian_combinatorial=np.zeros((1,1)),
                        laplacian_normalized=np.zeros((1,1)), laplacian_random_walk=np.zeros((1,1)),
                        eigenvalues_combinatorial=np.zeros(1), eigenvalues_normalized=np.zeros(1),
                        eigenvectors_combinatorial=np.zeros((1,1)), feature_vector=np.zeros(14),
                        fiedler_value=0.0, spectral_gap=0.0, algebraic_connectivity=0.0,
                        spectral_radius=0.0, von_neumann_entropy=0.0, renyi_entropy_2=0.0,
                        graph_energy=0.0, modularity_index=0.0, effective_rank=0.0,
                        normalized_fiedler=0.0, eigengap_ratio=0.0, spectral_discrepancy=0.0,
                    )).keys()}
                )
            module_cfg = self._dict_to_cfg(meta["module_cfg"])
            function_cfgs = {
                k: self._dict_to_cfg(v) for k, v in meta["function_cfgs"].items()
            }
            aggregate_feature_vector = raw["aggregate_feature_vector"]
            node_feature_matrix = raw["node_feature_matrix"]
            node_index_map = {k: int(v) for k, v in meta["node_index_map"].items()}
        except Exception:
            return None

        return StructuralAnalysisResult(
            module_cfg=module_cfg,
            function_cfgs=function_cfgs,
            module_spectral=module_spectral,
            function_spectrals=function_spectrals,
            aggregate_feature_vector=aggregate_feature_vector,
            node_feature_matrix=node_feature_matrix,
            node_index_map=node_index_map,
        )

    def set(self, key: str, result: StructuralAnalysisResult) -> None:
        """Persist *result* under *key*."""
        self._ensure_dir()
        npz_path = self._key_to_npz(key)
        json_path = self._key_to_json(key)

        arrays: dict[str, NDArray[np.float64]] = {}
        for k, arr in self._spectral_to_arrays(result.module_spectral).items():
            arrays[f"module_spectral.{k}"] = arr
        for func_key, profile in result.function_spectrals.items():
            for k, arr in self._spectral_to_arrays(profile).items():
                arrays[f"function_spectrals.{func_key}.{k}"] = arr
        arrays["aggregate_feature_vector"] = result.aggregate_feature_vector
        arrays["node_feature_matrix"] = result.node_feature_matrix

        np.savez_compressed(npz_path, **arrays)

        meta = {
            "module_cfg": self._cfg_to_dict(result.module_cfg),
            "function_cfgs": {
                k: self._cfg_to_dict(v) for k, v in result.function_cfgs.items()
            },
            "function_keys": list(result.function_spectrals.keys()),
            "node_index_map": result.node_index_map,
            "checksum": self._compute_checksum(npz_path),
        }

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

    def clear(self) -> None:
        """Remove the entire cache directory and its contents."""
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)


class Analyzer:
    """High-level facade: source text -> structural vectorised result."""

    def __init__(self, source_code: str) -> None:
        self.source_code: str = source_code
        self._tree: ast.AST = ast.parse(source_code)
        self._cache_manager: CacheManager = CacheManager()

    # -- internal helpers --------------------------------------------------

    def _build_node_feature_matrix(self, cfg: ControlFlowGraph) -> NDArray[np.float64]:
        n = len(cfg.blocks)
        if n == 0:
            return np.zeros((0, 0), dtype=np.float64)

        idx_map = cfg.get_index_mapping()
        num_types = len(BlockType)
        # Features: one-hot block type + statement count + in-degree + out-degree +
        #           total degree + local clustering + shortest-path depth + reachable flag
        num_features = num_types + 7
        features = np.zeros((n, num_features), dtype=np.float64)

        # Directed adjacency for shortest-path computation.
        A = np.zeros((n, n), dtype=np.float64)
        for s_id, t_id in cfg.edge_list:
            i, j = idx_map[s_id], idx_map[t_id]
            A[i, j] = 1.0

        entry_idx = idx_map[cfg.entry_block.uid]
        dists = dijkstra(
            csgraph=csr_matrix(A),
            directed=True,
            indices=entry_idx,
            unweighted=True,
            min_only=False,
        )
        distances: NDArray[np.float64] = np.asarray(dists).flatten()
        distances[np.isinf(distances)] = -1.0

        for uid, block in cfg.blocks.items():
            i = idx_map[uid]
            features[i, int(block.block_type)] = 1.0
            base = num_types
            features[i, base + 0] = float(block.statement_count)
            features[i, base + 1] = float(len(block.predecessors))
            features[i, base + 2] = float(len(block.successors))
            features[i, base + 3] = float(
                len(block.predecessors) + len(block.successors)
            )

            # Local clustering coefficient (directed, successor-only)
            succs = list(block.successors)
            if len(succs) > 1:
                count = 0
                for s1 in succs:
                    for s2 in succs:
                        if s1 != s2 and s2 in cfg.blocks[s1].successors:
                            count += 1
                possible = len(succs) * (len(succs) - 1)
                features[i, base + 4] = count / possible if possible > 0 else 0.0

            features[i, base + 5] = distances[i]
            features[i, base + 6] = 1.0 if distances[i] >= 0 else 0.0

        return features

    def _aggregate(
        self,
        module_spectral: SpectralProfile,
        function_spectrals: dict[str, SpectralProfile],
    ) -> NDArray[np.float64]:
        module_vec = module_spectral.feature_vector
        if not function_spectrals:
            pad = np.zeros_like(module_vec)
            return np.concatenate([module_vec, pad, pad, pad])

        func_matrix = np.stack(
            [sp.feature_vector for sp in function_spectrals.values()]
        )
        func_mean = np.mean(func_matrix, axis=0)
        func_max = np.max(func_matrix, axis=0)
        func_std = np.std(func_matrix, axis=0)
        return np.concatenate([module_vec, func_mean, func_max, func_std])

    # -- public entry ------------------------------------------------------

    def analyze(
        self, source_code: str | None = None, use_cache: bool = True
    ) -> StructuralAnalysisResult:
        """Run the complete structural analysis pipeline.

        Parameters
        ----------
        source_code:
            Optional source string to analyze.  When ``None`` the instance's
            original source code is used.
        use_cache:
            If ``True``, read from / write to the on-disk cache
            (``.omni_cache/``) using a SHA256 key.

        Returns
        -------
        StructuralAnalysisResult
            Immutable container with CFGs, Laplacians, eigen-decompositions,
            and a fixed-dimension aggregate feature vector.
        """
        code = self.source_code if source_code is None else source_code
        tree = self._tree if source_code is None else ast.parse(source_code)

        if use_cache:
            cache_key = hashlib.sha256(code.encode("utf-8")).hexdigest()
            cached = self._cache_manager.get(cache_key)
            if cached is not None:
                return cached

        module_cfg, func_cfgs = DeepASTVisitor.build(tree, name="<module>")

        module_spectral = SpectralGraphAnalyzer(module_cfg).analyze()

        func_spectrals: dict[str, SpectralProfile] = {}
        for key, func_cfg in func_cfgs.items():
            func_spectrals[key] = SpectralGraphAnalyzer(func_cfg).analyze()

        aggregate = self._aggregate(module_spectral, func_spectrals)
        node_features = self._build_node_feature_matrix(module_cfg)

        result = StructuralAnalysisResult(
            module_cfg=module_cfg,
            function_cfgs=func_cfgs,
            module_spectral=module_spectral,
            function_spectrals=func_spectrals,
            aggregate_feature_vector=aggregate,
            node_feature_matrix=node_features,
            node_index_map=module_cfg.get_index_mapping(),
        )

        if use_cache:
            self._cache_manager.set(cache_key, result)

        return result

    def clear_cache(self) -> None:
        """Wipe the ``.omni_cache/`` directory."""
        self._cache_manager.clear()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "BlockType",
    "BasicBlock",
    "ControlFlowGraph",
    "DeepASTVisitor",
    "SpectralProfile",
    "SpectralGraphAnalyzer",
    "StructuralAnalysisResult",
    "CacheManager",
    "Analyzer",
]
