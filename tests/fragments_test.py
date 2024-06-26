"""Tests for fragment generation."""

from typing import Tuple
import tempfile

from absl.testing import absltest
from absl.testing import parameterized
import ase
import jax
import jax.profiler
from jax import numpy as jnp
import logging
import os

from symphony.data import fragments, input_pipeline

# Important to see the logging messages!
logging.getLogger().setLevel(logging.INFO)


class FragmentTest(parameterized.TestCase):
    def setUp(self):
        super().setUp()

    @parameterized.product(
        mode=["nn", "radius"],
        heavy_first=[True, False],
        beta_com=[0.0, 100.0],
    )
    def test_fragment_generation_heavy_first(
        self,
        mode: str,
        heavy_first: bool,
        beta_com: float,
        seed: int = 0,
        eps: float = 1e-5,
    ):
        """Tests that training and evaluation runs without errors."""
        dirname = os.path.dirname(__file__)
        mol_file = os.path.join(dirname, "C3H8.xyz")
        mol = ase.io.read(mol_file)
        mol_graph = input_pipeline.ase_atoms_to_jraph_graph(mol, [1, 6, 7, 8, 9], 2.0)
        num_heavy_atoms = (mol_graph.nodes.species > 0).sum()
        com = jnp.average(
            mol_graph.nodes.positions,
            axis=0,
            weights=(mol_graph.nodes.species > 0) if heavy_first else None,
        )
        distances_com = jnp.linalg.norm(mol_graph.nodes.positions - com, axis=1)
        for frag in fragments.generate_fragments(
            jax.random.PRNGKey(seed),
            mol_graph,
            5,
            heavy_first=heavy_first,
            beta_com=beta_com,
            mode=mode,
        ):
            num_atoms = frag.nodes.species.shape[0]
            if heavy_first and num_atoms <= num_heavy_atoms:
                assert jnp.all(
                    frag.nodes.species > 0
                ), "hydrogen atom was added to fragment before all heavy atoms were added"
            if beta_com > 0 and num_atoms == 1:
                # This is assuming that beta_com is large enough that the first atom is always the closest to the center of mass
                assert (
                    jnp.abs(
                        distances_com.min()
                        - jnp.linalg.norm(frag.nodes.positions[0] - com)
                    )
                    < eps
                ), "first atom in fragment is not closest to center of mass"


if __name__ == "__main__":
    absltest.main()
