#!/bin/bash
#SBATCH --account bcxj-delta-gpu 
#SBATCH --partition=gpuA40x4
#SBATCH --job-name="eval-ibl"
#SBATCH --output="eval-ibl.%j.out"
#SBATCH -N 1
#SBATCH --array=0
#SBATCH -c 1
#SBATCH --ntasks-per-node=1
#SBATCH --mem 100000  
#SBATCH --gpus=1
#SBATCH -t 0-01
#SBATCH --export=ALL

. ~/.bashrc
conda activate poyo

eid=${1}
behavior=${2}
unaligned=${3}

config_name=train_ibl_${behavior}
ckpt_name=${eid}_${behavior}

if [ "$unaligned" = "True" ]; then
    config_name=${config_name}_unaligned.yaml
else
    config_name=${config_name}_aligned.yaml
fi

cd ../../eval/scripts/ibl_visual_behavior_neuropixels/

python eval_single_task_poyo.py --eid $eid \
                                --behavior $behavior \
                                --config_name $config_name \
                                --ckpt_name $ckpt_name \
                                --unaligned $unaligned

conda deactivate

cd scripts/yizi
