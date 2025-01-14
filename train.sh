#!/bin/bash

#SBATCH --account=col168
#SBATCH --partition=gpu-shared
#SBATCH --job-name="poyo"
#SBATCH --output="poyo.%j.out"
#SBATCH -N 1
#SBACTH --array=0
#SBATCH -c 8
#SBATCH --ntasks-per-node=1
#SBATCH --mem 150000
#SBATCH --gpus=1
#SBATCH -t 0-06
#SBATCH --export=ALL

module load gpu
module load slurm

. ~/.bashrc

conda activate poyo

cd /home/yzhang39/project-kirby/

# python train.py --config-name train_ibl_choice.yaml

# python train.py --config-name train_ibl_block.yaml

# python train.py --config-name train_ibl_wheel.yaml

python train.py --config-name train_ibl_whisker.yaml

conda deactivate
