"""Loads the model from a workdir to perform analysis."""

import glob
import os
import pickle
import sys
from typing import Any, Dict, Optional, Sequence, Tuple

import jax
import jax.numpy as jnp
import jraph
import ml_collections
import numpy as np
import pandas as pd
import yaml
from absl import logging
from clu import checkpoint
from flax.training import train_state

sys.path.append("..")

import datatypes  # noqa: E402
import input_pipeline_tf  # noqa: E402
import models  # noqa: E402
import train  # noqa: E402
from configs import default  # noqa: E402


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

    #config.num_train_molecules = config.train_molecules[1] - config.train_molecules[0]

    def iterate_with_prefix(dictionary: Dict[str, Any], prefix: str):
        """Iterates through a nested dictionary, yielding the flattened and prefixed keys and values."""
        for k, v in dictionary.items():
            if isinstance(v, dict):
                yield from iterate_with_prefix(v, prefix=f"{prefix}{k}.")
            else:
                yield prefix + k, v

    config_dict = dict(iterate_with_prefix(config.to_dict(), "config."))
    return pd.DataFrame().from_dict(config_dict)


def get_results_as_dataframe(
    models: Sequence[str], metrics: Sequence[str], basedir: str
) -> Dict[str, pd.DataFrame]:
    """Returns the results for the given model as a pandas dataframe for each split."""


    results = {"val": pd.DataFrame(), "test": pd.DataFrame()}
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

            num_params = sum(jax.tree_util.tree_leaves(jax.tree_map(jnp.size, best_state.params)))
            config_df = config_to_dataframe(config)
            other_df = pd.DataFrame.from_dict({
                "model": [config.model.lower()],
                "max_l": [config.max_ell],
                "num_params": [num_params],
                "num_train_molecules": [config.train_molecules[1] - config.train_molecules[0]],
            })
            for split in results:
                metrics_for_split = {
                    metric: [metrics_for_best_state[split][metric].item()] for metric in metrics
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
    config.root_dir = default.get_root_dir()

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
    config.root_dir = default.get_root_dir()

    # Mimic what we do in train.py.
    rng = jax.random.PRNGKey(config.rng_seed)
    rng, dataset_rng = jax.random.split(rng)

    # Set up dummy variables to obtain the structure.
    rng, init_rng = jax.random.split(rng)

    net = train.create_model(config, run_in_evaluation_mode=False)
    eval_net = train.create_model(config, run_in_evaluation_mode=True)

    # If we have pickled parameters already, we don't need init_graphs to initialize the model.
    # Note that we restore the model parameters from the checkpoint anyways.
    # We only use the pickled parameters to initialize the model, so only the keys of the pickled parameters are important.
    if load_pickled_params:
        checkpoint_dir = os.path.join(workdir, "checkpoints")
        pickled_params_file = os.path.join(checkpoint_dir, "params.pkl")
        if not os.path.exists(pickled_params_file):
            raise FileNotFoundError(f"No pickled params found at {pickled_params_file}")

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


def to_mol_dict(dataset, save: bool = True, model_path: Optional[str] = None):
    """Converts a dataset of Fragments to a dictionary of molecules."""

    generated_dict = {}
    data_iter = dataset.as_numpy_iterator()
    for graph in data_iter:
        frag = datatypes.Fragments.from_graphstuple(graph)
        frag = jax.tree_map(jnp.asarray, frag)
        update_dict(generated_dict, frag_to_mol_dict(frag))
    if save:
        gen_path = os.path.join(model_path, "generated/")
        if not os.path.exists(gen_path):
            os.makedirs(gen_path)
        # get untaken filename and store results
        file_name = os.path.join(gen_path, file_name)
        if os.path.isfile(file_name + ".mol_dict"):
            expand = 0
            while True:
                expand += 1
                new_file_name = file_name + "_" + str(expand)
                if os.path.isfile(new_file_name + ".mol_dict"):
                    continue
                else:
                    file_name = new_file_name
                    break
        with open(file_name + ".mol_dict", "wb") as f:
            pickle.dump(generated_dict, f)
    return generated_dict


def frag_to_mol_dict(generated_frag: datatypes.Fragments) -> Dict[int, Dict[str, np.ndarray]]:
    '''from G-SchNet: https://github.com/atomistic-machine-learning/G-SchNet'''

    generated = (
        {}
    )
    for mol in jraph.unbatch(generated_frag):
        l = mol.nodes.species.shape[0]
        if l not in generated:
            generated[l] = {
                "_positions": np.array([mol.nodes.positions]),
                "_atomic_numbers": np.array([list(map(
                    lambda z: models.ATOMIC_NUMBERS[z],
                    mol.nodes.species
                ))]),
            }
        else:
            generated[l]["_positions"] = np.append(
                generated[l]["_positions"],
                np.array([mol.nodes.positions]),
                0,
            )
            generated[l]["_atomic_numbers"] = np.append(
                generated[l]["_atomic_numbers"],
                np.array([list(map(
                    lambda z: models.ATOMIC_NUMBERS[z],
                    mol.nodes.species
                ))]),
                0,
            )

    return generated


def update_dict(d: Dict[Any, np.ndarray], d_upd: Dict[Any, np.ndarray]) -> None:
    '''
    Updates a dictionary of numpy.ndarray with values from another dictionary of the
    same kind. If a key is present in both dictionaries, the array of the second
    dictionary is appended to the array of the first one and saved under that key in
    the first dictionary.
    Args:
        d (dict of numpy.ndarray): dictionary to be updated
        d_upd (dict of numpy.ndarray): dictionary with new values for updating

    Also from G-SchNet
    '''
    for key in d_upd:
        if key not in d:
            d[key] = d_upd[key]
        else:
            for k in d_upd[key]:
                d[key][k] = np.append(d[key][k], d_upd[key][k], 0)