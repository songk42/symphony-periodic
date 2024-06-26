#!/bin/bash

# Set experiment name
branch=$(git symbolic-ref --short HEAD)
expname=tetris_"$branch"

# Loop over hyperparameters
for model in "nequip"
do
  for l in 1 2 3 4 5
  do
    for c in 64
    do
      for i in 2
      do
          CUDA_VISIBLE_DEVICES="$((l-1))" python -m symphony --config=configs/tetris/"$model".py --config.dataset="tetris"  --config.max_n_graphs=16  --config.max_ell="$l" --config.num_channels="$c"  --config.num_interactions="$i" --config.num_train_steps=100000 --workdir=workdirs/"$expname"/"$model"/interactions="$i"/l="$l"/channels="$c"/  > "$expname"_model="$model"_l="$l"_c="$c"_i="$i".txt 2>&1  &
      done
    done
  done
done


