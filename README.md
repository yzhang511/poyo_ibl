## Installation

Clone the project, enter the project's root directory, and then run the following:
```bash
conda create -n poyo python=3.9      # create an empty virtual environment
conda activate poyo                  # activate it
pip install -e .                     # install project-kirby into your path
pip install -r ibl_requirements.txt  # install packages needed to load IBL data
```

## To Run POYO on IBL Data:

1. Prepare datasets:
```
cd scripts/yizi
sbatch create_ibl_data.sh d23a44ef-1402-4ed7-97f5-47e9a7a504d9 True
```
Here, `d23a44ef-1402-4ed7-97f5-47e9a7a504d9` is the session ID, and `True` is a boolean flag indicating whether to prepare the aligned or unaligned data.

2. Create the dataset config file for each session and calculate normalization mean and std for continuous behavior:
```
cd scripts/yizi
sbatch create_ibl_data.sh d23a44ef-1402-4ed7-97f5-47e9a7a504d9 True
```
Here, `True` is a boolean flag indicating whether to create config files for the aligned or unaligned data.

3. Train:
```
cd scripts/yizi
sbatch train.sh d23a44ef-1402-4ed7-97f5-47e9a7a504d9 train_ibl_wheel_unaligned.yaml
```

4. Eval:
```
cd scripts/yizi
sbatch eval_ibl.sh d23a44ef-1402-4ed7-97f5-47e9a7a504d9 wheel True
```
Here, `True` is a boolean flag indicating whether to evaluate models trained on the aligned or unaligned data.


## To Run POYO on Allen Data:

1. Prepare datasets:
```
cd scripts/yizi
sbatch create_allen_data.sh 715093703 gabors
```
Here, `715093703` is the session ID, and `gabors` is the behavior; available behaviors are `gabors`, `static_gratings`, and `running_speed`.

2. Change session ID in data config: `configs/dataset/allen_gabors.yaml` for each behavior.

3. Calculate normalization mean and std for continuous behavior:
```
cd scripts/
python calculate_normalization_scales.py \
       --data_root /projects/bcxj/$user_name/allen/datasets/processed/ \
       --dataset_config ../configs/dataset/allen_running_speed.yaml
```
Paste the mean and std to data config file `allen_running_speed.yaml`. 

4. Change the params for unit drop out in `configs/train_allen_gabors.yaml` for each behavior: 
```
- _target_: kirby.transforms.UnitDropout
    max_units: 1000
    min_units: 40
    mode_units: 80
    tail_right: 120
    peak: 10
    M: 10
```
You mainly need to change `min_units` and `mode_units`. 

5. Train:
```
cd scripts/yizi
sbatch train.sh train_allen_gabors.yaml
```

6. Eval:
```
cd eval/scripts/allen_visual_behavior_neuropixels
python eval_single_task_poyo.py \
       --eid 715093703 \
       --behavior gabors \
       --config_name train_allen_gabors.yaml \
       --ckpt_name XXXXX  # Can find this in the logs folder
```
