#!/bin/bash

# Set experiment name
expname=extras/learning_rates

# Loop over hyperparameters
for schedule in constant sgdr
do
  for l in 2
  do
    for c in 32
    do
      for i in 4
      do
        CUDA_VISIBLE_DEVICES=6 python -m symphony --config=configs/qm9/mace.py         --config.learning_rate_schedule="$schedule"  --config.max_ell="$l" --config.num_channels="$c" --config.num_interactions="$i" --workdir=workdirs/"$expname"/"$schedule"/mace/interactions="$i"/l="$l"/channels="$c"/  || break 10
        CUDA_VISIBLE_DEVICES=6 python -m symphony --config=configs/qm9/e3schnet.py     --config.learning_rate_schedule="$schedule" --config.max_ell="$l" --config.num_channels="$c" --config.num_interactions="$i" --workdir=workdirs/"$expname"/"$schedule"/e3schnet/interactions="$i"/l="$l"/channels="$c"/  || break 10
      done
    done
  done
done

