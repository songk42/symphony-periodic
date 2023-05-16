"""Loads the model from a workdir to perform analysis."""

import glob
import itertools
import os
import pickle
import sys
from typing import Any, Dict, Optional, Sequence, Tuple

import haiku as hk
import ase
import ase.build
import e3nn_jax as e3nn
import jax
import jax.numpy as jnp
import jraph
import ml_collections
import numpy as np
import pandas as pd
import tensorflow as tf
import yaml
from absl import logging
from clu import checkpoint
from flax.training import train_state
import plotly.graph_objects as go
import plotly.subplots

from openbabel import pybel
from openbabel import openbabel as ob

sys.path.append("..")

import qm9
import datatypes  # noqa: E402
import input_pipeline
import models  # noqa: E402
import train  # noqa: E402
from configs import default  # noqa: E402

try:
    import input_pipeline_tf  # noqa: E402

    tf.config.experimental.set_visible_devices([], "GPU")
except ImportError:
    logging.warning("TensorFlow not installed. Skipping import of input_pipeline_tf.")
    pass


ATOMIC_NUMBERS = models.ATOMIC_NUMBERS
ELEMENTS = ["H", "C", "N", "O", "F"]
RADII = models.RADII

# Colors and sizes for the atoms.
ATOMIC_COLORS = {
    1: "rgb(150, 150, 150)",  # H
    6: "rgb(50, 50, 50)",  # C
    7: "rgb(0, 100, 255)",  # N
    8: "rgb(255, 0, 0)",  # O
    9: "rgb(255, 0, 255)",  # F
}
ATOMIC_SIZES = {
    1: 10,  # H
    6: 30,  # C
    7: 30,  # N
    8: 30,  # O
    9: 30,  # F
}


def get_title_for_name(name: str) -> str:
    """Returns the title for the given name."""
    if "e3schnet" in name:
        return "E3SchNet"
    elif "mace" in name:
        return "MACE"
    elif "nequip" in name:
        return "NequIP"
    return name.title()


def combine_visualizations(figs: Sequence[go.Figure], label_name: Optional[str] = None, labels: Optional[Sequence[str]] = None) -> go.Figure:
    """Combines multiple plotly figures generated by visualize_predictions() into one figure with a slider."""
    all_traces = []
    for fig in figs:
        all_traces.extend(fig.data)

    steps = []
    ct = 0
    start_indices = [0]
    for fig in figs:
        steps.append(
            dict(
                method="restyle",
                args=[
                    {
                        "visible": [
                            True if ct <= i < ct + len(fig.data) else False
                            for i in range(len(all_traces))
                        ]
                    }
                ],
            )
        )
        ct += len(fig.data)
        start_indices.append(ct)

    if label_name is None:
        label_name = "steps"
    if labels is None:
        labels = steps

    axis = dict(
        showbackground=False,
        showticklabels=False,
        showgrid=False,
        zeroline=False,
        title="",
        nticks=3,
    )
    layout = dict(
        sliders=[{label_name: labels}],
        title_x=0.5,
        width=1500,
        height=800,
        scene=dict(
            xaxis=dict(**axis),
            yaxis=dict(**axis),
            zaxis=dict(**axis),
            aspectmode="data",
        ),
        paper_bgcolor="rgba(255,255,255,1)",
        plot_bgcolor="rgba(255,255,255,1)",
        legend=dict(
            yanchor="top",
            y=0.99,
            xanchor="right",
            x=0.1,
        ),
    )

    fig_all = plotly.subplots.make_subplots(
        rows=1, cols=2, specs=[[{"type": "scene"}, {"type": "xy"}]]
    )
    for i, trace in enumerate(all_traces):
        visible = True if i < start_indices[1] else False
        trace.update(visible=visible)
        if trace.type in ["scatter3d", "surface"]:
            col = 1
        else:
            col = 2
        fig_all.add_trace(trace, row=1, col=col)
    fig_all.update_layout(layout)
    return fig_all


def visualize_predictions(
    pred: datatypes.Predictions,
    input_molecule: ase.Atoms,
    molecule_with_target: Optional[ase.Atoms] = None,
    target: Optional[int] = None,
) -> go.Figure:
    """Visualizes the predictions for a molecule at a particular step."""

    def get_scaling_factor(focus_prob: float, num_nodes: int) -> float:
        """Returns a scaling factor for the size of the atom."""
        if focus_prob < 1 / num_nodes - 1e-3:
            return 0.95
        return 1 + focus_prob**2

    def chosen_focus_string(index: int, focus: int) -> str:
        """Returns a string indicating whether the atom was chosen as the focus."""
        if index == focus:
            return "(Chosen as Focus)"
        return "(Not Chosen as Focus)"

    def chosen_element_string(element: str, predicted_target_element: str) -> str:
        """Returns a string indicating whether an element was chosen as the target element."""
        if element == predicted_target_element:
            return "Chosen as Target Element"
        return "Not Chosen as Target Element"

    # Make subplots.
    fig = plotly.subplots.make_subplots(
        rows=1,
        cols=2,
        specs=[[{"type": "scene"}, {"type": "xy"}]],
        subplot_titles=("Molecule", "Target Element Probabilities"),
    )

    # Highlight the focus probabilities.
    mol_trace = []
    focus = pred.globals.focus_indices.item()
    focus_position = input_molecule.positions[focus]
    stop_prob = pred.globals.stop_probs.item()
    if np.isnan(stop_prob):
        stop_prob = 1
    target_species_probs = (1 - stop_prob) * pred.nodes.target_species_probs
    focus_probs = np.sum(target_species_probs, axis=1)

    mol_trace.append(
        go.Scatter3d(
            x=input_molecule.positions[:, 0],
            y=input_molecule.positions[:, 1],
            z=input_molecule.positions[:, 2],
            mode="markers",
            marker=dict(
                size=[
                    get_scaling_factor(float(focus_prob), len(input_molecule))
                    * ATOMIC_SIZES[num]
                    for focus_prob, num in zip(focus_probs, input_molecule.numbers)
                ],
                color=["rgba(150, 75, 0, 0.5)" for _ in input_molecule],
            ),
            hovertext=[
                f"Focus Probability: {focus_prob:.3f}<br>{chosen_focus_string(i, focus)}"
                for i, focus_prob in enumerate(focus_probs)
            ],
            name="Focus Probabilities",
        )
    )
    # Plot the actual molecule.
    mol_trace.append(
        go.Scatter3d(
            x=input_molecule.positions[:, 0],
            y=input_molecule.positions[:, 1],
            z=input_molecule.positions[:, 2],
            mode="markers",
            marker=dict(
                size=[ATOMIC_SIZES[num] for num in input_molecule.numbers],
                color=[ATOMIC_COLORS[num] for num in input_molecule.numbers],
            ),
            hovertext=[
                f"Element: {ase.data.chemical_symbols[num]}"
                for num in input_molecule.numbers
            ],
            opacity=1.0,
            name="Molecule Atoms",
            legendrank=1,
        )
    )

    # Highlight the target atom.
    if target is not None:
        mol_trace.append(
            go.Scatter3d(
                x=[molecule_with_target.positions[target, 0]],
                y=[molecule_with_target.positions[target, 1]],
                z=[molecule_with_target.positions[target, 2]],
                mode="markers",
                marker=dict(
                    size=[1.05 * ATOMIC_SIZES[molecule_with_target.numbers[target]]],
                    color=["green"],
                ),
                opacity=0.5,
                name="Target Atom",
            )
        )

    # Since we downsample the position grid, we need to recompute the position probabilities.
    position_coeffs = pred.globals.position_coeffs
    position_logits = e3nn.to_s2grid(
        position_coeffs,
        50,
        99,
        quadrature="gausslegendre",
        normalization="integral",
        p_val=1,
        p_arg=-1,
    )
    position_logits = position_logits.apply(
        lambda x: x - position_logits.grid_values.max()
    )
    position_probs = position_logits.apply(jnp.exp)

    count = 0
    cmin = 0.0
    cmax = position_probs.grid_values.max().item()
    for i in range(len(RADII)):
        prob_r = position_probs[i]

        # Skip if the probability is too small.
        if prob_r.grid_values.max() < 1e-2 * cmax:
            continue

        count += 1
        surface_r = go.Surface(
            **prob_r.plotly_surface(radius=RADII[i], translation=focus_position),
            colorscale=[[0, "rgba(4, 59, 192, 0.)"], [1, "rgba(4, 59, 192, 1.)"]],
            showscale=False,
            cmin=cmin,
            cmax=cmax,
            name="Position Probabilities",
            legendgroup="Position Probabilities",
            showlegend=(count == 1),
        )
        mol_trace.append(surface_r)

    # Plot spherical harmonic projections of logits.
    # Find closest index in RADII to the sampled positions.
    radius = jnp.linalg.norm(pred.globals.position_vectors, axis=-1)
    most_likely_radius_index = jnp.abs(RADII - radius).argmin()
    most_likely_radius = RADII[most_likely_radius_index]
    most_likely_radius_coeffs = position_coeffs[most_likely_radius_index]
    most_likely_radius_sig = e3nn.to_s2grid(
        most_likely_radius_coeffs, 50, 69, quadrature="soft", p_val=1, p_arg=-1
    )
    spherical_harmonics = go.Surface(
        most_likely_radius_sig.plotly_surface(
            scale_radius_by_amplitude=True,
            radius=most_likely_radius,
            translation=focus_position,
            normalize_radius_by_max_amplitude=True,
        ),
        name="Spherical Harmonics for Logits",
        showlegend=True,
    )
    mol_trace.append(spherical_harmonics)

    # All of the above is on the left subplot.
    for trace in mol_trace:
        fig.add_trace(trace, row=1, col=1)

    # Plot target species probabilities.
    predicted_target_species = pred.globals.target_species.item()
    predicted_target_element = ELEMENTS[predicted_target_species]
    species_probs = pred.nodes.target_species_probs[focus].tolist()

    # We highlight the true target if provided.
    if target is not None:
        target_element_index = ATOMIC_NUMBERS.index(
            molecule_with_target.numbers[target]
        )
        other_elements = (
            ELEMENTS[:target_element_index] + ELEMENTS[target_element_index + 1 :]
        )
        species_trace = [
            go.Bar(
                x=[ELEMENTS[target_element_index]],
                y=[species_probs[target_element_index]],
                hovertext=[
                    chosen_element_string(
                        ELEMENTS[target_element_index], predicted_target_element
                    )
                ],
                name="True Target Element Probability",
                marker=dict(color="green", opacity=0.8),
                showlegend=False,
            ),
            go.Bar(
                x=other_elements,
                y=species_probs[:target_element_index]
                + species_probs[target_element_index + 1 :],
                hovertext=[
                    chosen_element_string(elem, predicted_target_element)
                    for elem in other_elements
                ],
                name="Other Elements Probabilities",
                marker=dict(
                    color=[
                        "gray" if elem != predicted_target_element else "blue"
                        for elem in other_elements
                    ],
                    opacity=0.8,
                ),
                showlegend=False,
            ),
        ]

    else:
        species_trace = [
            go.Bar(
                x=ELEMENTS,
                y=species_probs,
                name="Elements Probabilities",
                hovertext=[
                    chosen_element_string(elem, predicted_target_element)
                    for elem in ELEMENTS
                ],
                marker=dict(
                    color=[
                        "gray" if elem != predicted_target_element else "blue"
                        for elem in ELEMENTS
                    ],
                    opacity=0.8,
                ),
                showlegend=False,
            ),
        ]

    for trace in species_trace:
        fig.add_trace(trace, row=1, col=2)

    # Update the layout.
    axis = dict(
        showbackground=False,
        showticklabels=False,
        showgrid=False,
        zeroline=False,
        title="",
        nticks=3,
    )
    fig.update_layout(
        width=1500,
        height=800,
        scene=dict(
            xaxis=dict(**axis),
            yaxis=dict(**axis),
            zaxis=dict(**axis),
            aspectmode="data",
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(
            yanchor="top",
            y=0.99,
            xanchor="right",
            x=0.1,
        ),
    )
    return fig


def cast_keys_as_int(dictionary: Dict[Any, Any]) -> Dict[Any, Any]:
    """Returns a dictionary with string keys converted to integers, wherever possible."""
    casted_dictionary = {}
    for key, val in dictionary.items():
        try:
            val = cast_keys_as_int(val)
        except AttributeError:
            pass

        try:
            key = int(key)
        except ValueError:
            pass
        finally:
            casted_dictionary[key] = val
    return casted_dictionary


def name_from_workdir(workdir: str) -> str:
    """Returns the full name of the model from the workdir."""
    index = workdir.find("workdirs") + len("workdirs/")
    return workdir[index:]


def config_to_dataframe(config: ml_collections.ConfigDict) -> Dict[str, Any]:
    """Flattens a nested config into a Pandas dataframe."""

    # Compatibility with old configs.
    if "num_interactions" not in config:
        config.num_interactions = config.n_interactions
        del config.n_interactions

    if "num_channels" not in config:
        config.num_channels = config.n_atom_basis
        assert config.num_channels == config.n_filters
        del config.n_atom_basis, config.n_filters

    def iterate_with_prefix(dictionary: Dict[str, Any], prefix: str):
        """Iterates through a nested dictionary, yielding the flattened and prefixed keys and values."""
        for k, v in dictionary.items():
            if isinstance(v, dict):
                yield from iterate_with_prefix(v, prefix=f"{prefix}{k}.")
            else:
                yield prefix + k, v

    config_dict = dict(iterate_with_prefix(config.to_dict(), "config."))
    return pd.DataFrame().from_dict(config_dict)


def load_model_at_step(
    workdir: str, step: int, run_in_evaluation_mode: bool
) -> Tuple[ml_collections.ConfigDict, hk.Transformed, Dict[str, jnp.ndarray]]:
    """Loads the model at a given step.

    This is a lightweight version of load_from_workdir, that only constructs the model and not the training state.
    """

    if step == -1:
        params_file = os.path.join(workdir, "checkpoints/params_best.pkl")
    else:
        params_file = os.path.join(workdir, f"checkpoints/params_{step}.pkl")

    try:
        with open(params_file, "rb") as f:
            params = pickle.load(f)
    except FileNotFoundError:
        if step == -1:
            try:
                params_file = os.path.join(workdir, "checkpoints/params.pkl")
                with open(params_file, "rb") as f:
                    params = pickle.load(f)
            except:
                raise FileNotFoundError(f"Could not find params file {params_file}")
        else:
            raise FileNotFoundError(f"Could not find params file {params_file}")

    with open(workdir + "/config.yml", "rt") as config_file:
        config = yaml.unsafe_load(config_file)
    assert config is not None
    config = ml_collections.ConfigDict(config)
    config.root_dir = default.get_root_dir("qm9")

    model = models.create_model(config, run_in_evaluation_mode=run_in_evaluation_mode)
    params = jax.tree_map(jnp.asarray, params)
    return model, params, config


def get_results_as_dataframe(
    models: Sequence[str], metrics: Sequence[str], basedir: str
) -> Dict[str, pd.DataFrame]:
    """Returns the results for the given model as a pandas dataframe for each split."""

    splits = ["train_eval_final", "val_eval_final", "test_eval_final"]
    results = {split: pd.DataFrame() for split in splits}
    for model in models:
        for config_file_path in glob.glob(
            os.path.join(basedir, "**", model, "**", "*.yml"), recursive=True
        ):
            workdir = os.path.dirname(config_file_path)
            try:
                config, best_state, _, metrics_for_best_state = load_from_workdir(
                    workdir
                )
            except FileNotFoundError:
                logging.warning(f"Skipping {workdir} because it is incomplete.")
                continue

            num_params = sum(
                jax.tree_util.tree_leaves(jax.tree_map(jnp.size, best_state.params))
            )
            config_df = config_to_dataframe(config)
            other_df = pd.DataFrame.from_dict(
                {
                    "model": [config.model.lower()],
                    "max_l": [config.max_ell],
                    "num_interactions": [config.num_interactions],
                    "num_channels": [config.num_channels],
                    "num_params": [num_params],
                    "num_train_molecules": [
                        config.train_molecules[1] - config.train_molecules[0]
                    ],
                }
            )
            for split in results:
                metrics_for_split = {
                    metric: [metrics_for_best_state[split][metric].item()]
                    for metric in metrics
                }
                metrics_df = pd.DataFrame.from_dict(metrics_for_split)
                df = pd.merge(config_df, metrics_df, left_index=True, right_index=True)
                df = pd.merge(df, other_df, left_index=True, right_index=True)
                results[split] = pd.concat([results[split], df], ignore_index=True)

    return results


def load_metrics_from_workdir(
    workdir: str,
) -> Tuple[
    ml_collections.ConfigDict,
    train_state.TrainState,
    train_state.TrainState,
    Dict[Any, Any],
]:
    """Loads only the config and the metrics for the best model."""

    if not os.path.exists(workdir):
        raise FileNotFoundError(f"{workdir} does not exist.")

    # Load config.
    saved_config_path = os.path.join(workdir, "config.yml")
    if not os.path.exists(saved_config_path):
        raise FileNotFoundError(f"No saved config found at {workdir}")

    logging.info("Saved config found at %s", saved_config_path)
    with open(saved_config_path, "r") as config_file:
        config = yaml.unsafe_load(config_file)

    # Check that the config was loaded correctly.
    assert config is not None
    config = ml_collections.ConfigDict(config)
    config.root_dir = default.get_root_dir("qm9")

    checkpoint_dir = os.path.join(workdir, "checkpoints")
    ckpt = checkpoint.Checkpoint(checkpoint_dir, max_to_keep=5)
    data = ckpt.restore({"metrics_for_best_state": None})

    return config, cast_keys_as_int(data["metrics_for_best_state"])


def load_from_workdir(
    workdir: str,
    load_pickled_params: bool = True,
    init_graphs: Optional[jraph.GraphsTuple] = None,
) -> Tuple[
    ml_collections.ConfigDict,
    train_state.TrainState,
    train_state.TrainState,
    Dict[Any, Any],
]:
    """Loads the config, best model (in train mode), best model (in eval mode) and metrics for the best model."""

    if not os.path.exists(workdir):
        raise FileNotFoundError(f"{workdir} does not exist.")

    # Load config.
    saved_config_path = os.path.join(workdir, "config.yml")
    if not os.path.exists(saved_config_path):
        raise FileNotFoundError(f"No saved config found at {workdir}")

    logging.info("Saved config found at %s", saved_config_path)
    with open(saved_config_path, "r") as config_file:
        config = yaml.unsafe_load(config_file)

    # Check that the config was loaded correctly.
    assert config is not None
    config = ml_collections.ConfigDict(config)
    config.root_dir = default.get_root_dir("qm9")

    # Mimic what we do in train.py.
    rng = jax.random.PRNGKey(config.rng_seed)
    rng, dataset_rng = jax.random.split(rng)

    # Set up dummy variables to obtain the structure.
    rng, init_rng = jax.random.split(rng)

    net = models.create_model(config, run_in_evaluation_mode=False)
    eval_net = models.create_model(config, run_in_evaluation_mode=True)

    # If we have pickled parameters already, we don't need init_graphs to initialize the model.
    # Note that we restore the model parameters from the checkpoint anyways.
    # We only use the pickled parameters to initialize the model, so only the keys of the pickled parameters are important.
    if load_pickled_params:
        checkpoint_dir = os.path.join(workdir, "checkpoints")
        pickled_params_file = os.path.join(checkpoint_dir, "params_best.pkl")
        if not os.path.exists(pickled_params_file):
            pickled_params_file = os.path.join(checkpoint_dir, "params_best.pkl")
            if not os.path.exists(pickled_params_file):
                raise FileNotFoundError(
                    f"No pickled params found at {pickled_params_file}"
                )

        logging.info(
            "Initializing dummy model with pickled params found at %s",
            pickled_params_file,
        )

        with open(pickled_params_file, "rb") as f:
            params = jax.tree_map(np.array, pickle.load(f))
    else:
        if init_graphs is None:
            logging.info("Initializing dummy model with init_graphs from dataloader")
            datasets = input_pipeline_tf.get_datasets(dataset_rng, config)
            train_iter = datasets["train"].as_numpy_iterator()
            init_graphs = next(train_iter)
        else:
            logging.info("Initializing dummy model with provided init_graphs")

        params = jax.jit(net.init)(init_rng, init_graphs)

    tx = train.create_optimizer(config)
    dummy_state = train_state.TrainState.create(
        apply_fn=net.apply, params=params, tx=tx
    )

    # Load the actual values.
    checkpoint_dir = os.path.join(workdir, "checkpoints")
    ckpt = checkpoint.Checkpoint(checkpoint_dir, max_to_keep=5)
    data = ckpt.restore({"best_state": dummy_state, "metrics_for_best_state": None})
    best_state = jax.tree_map(jnp.asarray, data["best_state"])
    best_state_in_eval_mode = best_state.replace(apply_fn=eval_net.apply)

    return (
        config,
        best_state,
        best_state_in_eval_mode,
        cast_keys_as_int(data["metrics_for_best_state"]),
    )


def construct_molecule(molecule_str: str) -> Tuple[ase.Atoms, str]:
    """Returns a molecule from the given input string.

    The input is interpreted either as an index for the QM9 dataset,
    a name for ase.build.molecule(),
    or a file with atomic numbers and coordinates for ase.io.read().
    """
    # If we believe the string is a file, try to read it.
    if os.path.exists(molecule_str):
        filename = os.path.basename(molecule_str).split(".")[0]
        return ase.io.read(molecule_str), filename

    # A number is interpreted as a QM9 molecule index.
    if molecule_str.isdigit():
        dataset = qm9.load_qm9("qm9_data")
        molecule = dataset[int(molecule_str)]
        return molecule, f"qm9_index={molecule_str}"

    # If the string is a valid molecule name, try to build it.
    molecule = ase.build.molecule(molecule_str)
    return molecule, molecule.get_chemical_formula()


def construct_obmol(mol: ase.Atoms) -> ob.OBMol:
    obmol = ob.OBMol()
    obmol.BeginModify()

    # set positions and atomic numbers of all atoms in the molecule
    for p, n in zip(mol.positions, mol.numbers):
        obatom = obmol.NewAtom()
        obatom.SetAtomicNum(int(n))
        obatom.SetVector(*p.tolist())

    # infer bonds and bond order
    obmol.ConnectTheDots()
    obmol.PerceiveBondOrders()

    obmol.EndModify()
    return obmol


def construct_pybel_mol(mol: ase.Atoms) -> pybel.Molecule:
    """Constructs a Pybel molecule from an ASE molecule."""
    obmol = construct_obmol(mol)

    return pybel.Molecule(obmol)
