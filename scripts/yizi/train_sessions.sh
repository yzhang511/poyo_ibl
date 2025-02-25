#!/bin/bash

#SBATCH --account=bcxj-delta-gpu
#SBATCH --partition=gpuA40x4
#SBATCH --job-name="train-multi"
#SBATCH --output="train-multi.%j.out"
#SBATCH -N 1
#SBACTH --array=0
#SBATCH -c 1
#SBATCH --ntasks-per-node=1
#SBATCH --mem 100000  
#SBATCH --gpus=1
#SBATCH -t 2-00
#SBATCH --export=ALL

# module load gpu
# module load slurm

. ~/.bashrc
cd ..
conda activate poyo

while IFS= read -r line
do
    python create_config.py --eid $line \
                            --base_path ../ \
                            --multitask

    # python calculate_normalization_scales.py --data_root /u/ywang74/Dev/poyo_ibl/data/processed \
    #                                         --dataset_config  /u/ywang74/Dev/poyo_ibl/configs/dataset/ibl_multitask_$line.yaml
done < ../data/train_eids.txt
# python create_config.py --eid_list_path ../data/train_eids.txt \
#                         --base_path ../ \
#                         --multitask

# unify the normalized config files
python unify_config.py --base_path ../ \
                        --eid_list_path ../data/train_eids.txt 
# normalize over all the training data
python calculate_normalization_scales.py --data_root /u/ywang74/Dev/poyo_ibl/data/processed \
                                        --dataset_config  /u/ywang74/Dev/poyo_ibl/configs/dataset/ibl_sessions.yaml
cd .. 
# echo "Finished calculating normalization scales"
python train.py --config-name train_ibl_sessions.yaml

conda deactivate
cd scripts/ppwang