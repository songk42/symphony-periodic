"""Defines the default training configuration."""

import ml_collections


def get_config() -> ml_collections.ConfigDict:
    """Get the default training configuration."""
    config = ml_collections.ConfigDict()

    # Dataset.
    config.dataset = "qm9"
    config.fragment_logic = "nn"
    config.heavy_first = False
    config.max_targets_per_graph = 1
    config.root_dir = None
    config.use_edm_splits = True
    config.num_train_molecules = 100000
    config.num_val_molecules = 17748
    config.num_test_molecules = 13083
    config.shuffle_datasets = True
    config.infer_edges_with_radial_cutoff = True
    config.radial_cutoff = 5.0

    # Optimizer.
    config.optimizer = "adam"
    config.learning_rate = 1e-3
    config.gradient_clip_norm = 1.0

    # Training.
    config.rng_seed = 0
    config.use_same_rng_across_structures = False
    config.num_train_steps = 2_000_000
    config.log_every_steps = 1_000
    config.eval = True
    config.num_eval_steps = 100
    config.eval_every_steps = 30_000
    config.generate = True
    config.generate_every_steps = 30_000
    config.nn_tolerance = 0.1
    config.compute_padding_dynamically = False
    config.max_n_graphs = 16
    config.max_n_nodes = 30 * config.get_ref("max_n_graphs")
    config.max_n_edges = 90 * config.get_ref("max_n_graphs")
    config.loss_kwargs = ml_collections.ConfigDict()
    config.loss_kwargs.ignore_position_loss_for_small_fragments = False
    config.add_noise_to_positions = True
    config.position_noise_std = 0.1

    # Prediction heads.
    config.focus_and_target_species_predictor = ml_collections.ConfigDict()
    config.focus_and_target_species_predictor.compute_global_embedding = False
    config.focus_and_target_species_predictor.latent_size = 128
    config.focus_and_target_species_predictor.num_layers = 3
    config.focus_and_target_species_predictor.activation = "softplus"

    config.target_position_predictor = ml_collections.ConfigDict()
    config.target_position_predictor.angular_predictor = ml_collections.ConfigDict()
    config.target_position_predictor.angular_predictor.num_channels = 2
    config.target_position_predictor.angular_predictor.radial_mlp_num_layers = 2
    config.target_position_predictor.angular_predictor.radial_mlp_latent_size = 8
    config.target_position_predictor.angular_predictor.res_beta = 100
    config.target_position_predictor.angular_predictor.res_alpha = 99
    config.target_position_predictor.angular_predictor.quadrature = "gausslegendre"
    config.target_position_predictor.angular_predictor.sampling_inverse_temperature_factor = 10.0
    config.target_position_predictor.angular_predictor.sampling_num_steps = 1000
    config.target_position_predictor.angular_predictor.sampling_init_step_size = 10.0

    config.target_position_predictor.radial_predictor = ml_collections.ConfigDict()
    config.target_position_predictor.radial_predictor.num_bins = 16
    config.target_position_predictor.radial_predictor.num_param_mlp_layers = 2
    config.target_position_predictor.radial_predictor.num_layers = 2
    config.target_position_predictor.radial_predictor.min_radius = 0.0
    config.target_position_predictor.radial_predictor.max_radius = 5.0
    config.target_position_predictor.radial_predictor.boundary_error = 0.35

    # Generation.
    config.generation = ml_collections.ConfigDict()
    config.generation.focus_and_atom_type_inverse_temperature = 1.0
    config.generation.position_inverse_temperature = 1.0
    config.generation.res_beta = config.target_position_predictor.angular_predictor.get_ref("res_beta")
    config.generation.res_alpha = config.target_position_predictor.angular_predictor.get_ref("res_alpha")
    config.generation.radial_cutoff = config.get_ref("radial_cutoff")
    config.generation.num_seeds = 100
    config.generation.num_seeds_per_chunk = 20
    config.generation.init_molecules = "H"
    config.generation.max_num_atoms = 35
    config.generation.avg_neighbors_per_atom = 10

    return config
