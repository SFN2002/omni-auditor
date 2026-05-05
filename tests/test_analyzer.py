"""Unit tests for src.engine.analyzer."""

from __future__ import annotations

import ast
import unittest

import numpy as np

from src.engine.analyzer import Analyzer, DeepASTVisitor, SpectralGraphAnalyzer


class TestAnalyzer(unittest.TestCase):
    """Tests for AST parsing, CFG construction, and spectral analysis."""

    def test_ast_parsing(self) -> None:
        """Parse simple Python code and verify the AST is built correctly."""
        code = "def foo():\n    return 1\n"
        analyzer = Analyzer(code)
        result = analyzer.analyze(use_cache=False)

        self.assertIsNotNone(result)
        self.assertEqual(result.module_cfg.name, "<module>")
        self.assertIsInstance(result.module_spectral.eigenvalues_combinatorial, np.ndarray)

    def test_cfg_construction(self) -> None:
        """Verify ControlFlowGraph has ENTRY and EXIT blocks."""
        code = """
def bar(x):
    if x:
        return 1
    return 0
"""
        tree = ast.parse(code)
        cfg, func_cfgs = DeepASTVisitor.build(tree, name="<module>")

        self.assertEqual(cfg.entry_block.block_type.name, "ENTRY")
        self.assertEqual(cfg.exit_block.block_type.name, "EXIT")
        self.assertGreater(len(cfg.edge_list), 0)
        self.assertTrue(any("bar" in k for k in func_cfgs))

    def test_spectral_eigenvalues(self) -> None:
        """Verify Laplacian eigenvalues are computed and sorted ascending."""
        code = "def baz():\n    return 42\n"
        tree = ast.parse(code)
        cfg, _ = DeepASTVisitor.build(tree, name="<module>")
        spectral = SpectralGraphAnalyzer(cfg).analyze()

        eigvals = spectral.eigenvalues_combinatorial
        self.assertIsInstance(eigvals, np.ndarray)
        self.assertEqual(eigvals.ndim, 1)
        self.assertEqual(len(eigvals), len(cfg.blocks))

        # Laplacian eigenvalues are real and non-negative
        self.assertTrue(np.all(eigvals >= -1e-12))

        # Sorted ascending
        self.assertTrue(np.all(np.diff(eigvals) >= -1e-12))


if __name__ == "__main__":
    unittest.main()
