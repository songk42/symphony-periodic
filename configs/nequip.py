"""Defines the default hyperparameters and training configuration for the GraphMLP model."""

import ml_collections

from configs import default


def get_config() -> ml_collections.ConfigDict:
    """Get the hyperparameter configuration for the NequIP model."""
    config = default.get_config()

    # NequIP hyperparameters.
    config.model = "NequIP"
    config.num_channels = 128
    config.avg_num_neighbors = 15.0
    config.num_interactions = 1
    config.max_ell = 3
    config.even_activation = "swish"
    config.odd_activation = "tanh"
    config.mlp_activation = "swish"
    config.activation = "softplus"
    config.mlp_n_layers = 2
    config.num_basis_fns = 8

    return config
