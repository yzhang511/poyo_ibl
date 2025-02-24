import os
import numpy as np
from tqdm import tqdm
import argparse
import torch
import random

import omegaconf                 # for loading model configuration from a fil
import hydra
from hydra import compose, initialize
from omegaconf import OmegaConf

from kirby.data import Dataset, collate
import kirby.taxonomy
from kirby.taxonomy import (
    decoder_registry,
    Decoder, OutputType, Task
)
from kirby.models import POYOPlusTokenizer
from kirby.transforms import Compose
from kirby.data.sampler import SequentialFixedWindowSampler
from kirby.utils import train_wrapper
from torch.utils.data import DataLoader

from torch_optimizer import Lamb
from lightning.pytorch.callbacks import (
    LearningRateMonitor,
    ModelCheckpoint,
    ModelSummary,
)
import lightning

from sklearn.metrics import accuracy_score, balanced_accuracy_score, r2_score

from eval.scripts.ibl_visual_behavior_neuropixels.eval_utils import bin_behaviors, viz_single_cell

import logging
logging.basicConfig(level=logging.INFO)

torch.set_float32_matmul_precision("medium")

def move_to_gpu(d, device):
    for k, v in d.items():
        if isinstance(v, dict):
            move_to_gpu(v, device)
        elif isinstance(v, torch.Tensor):
            d[k] = v.to(device)

def _one_hot(arr, T):
    uni = np.sort(np.unique(arr))
    ret = np.zeros((len(arr), T, len(uni)))
    for i, _uni in enumerate(uni):
        ret[:,:,i] = (arr == _uni)
    return ret

# -------
# SET UP
# -------
ap = argparse.ArgumentParser()
ap.add_argument("--eid", type=str, default="c7bf2d49-4937-4597-b307-9f39cb1c7b16")
ap.add_argument("--behavior", type=str, default="choice")
ap.add_argument("--model_path", type=str, default="../../../logs/lightning_logs/")
ap.add_argument("--ckpt_name", type=str, default="2xpi3x0u")
ap.add_argument("--save_path", type=str, default="../../../results/")
ap.add_argument("--data_path", type=str, default="/projects/bcxj/yzhang39/ibl/datasets/processed/")
ap.add_argument("--config_path", type=str, default="../../../configs/")
ap.add_argument("--config_name", type=str, default="train_ibl_choice.yaml")
ap.add_argument("--unaligned", type=bool, default=False)
args = ap.parse_args()

save_path = args.save_path
data_path = args.data_path
config_path = args.config_path
model_path = args.model_path
config_name = args.config_name
ckpt_name = args.ckpt_name

eid = args.eid
logging.info(f"Evaluating session: {eid}")

params = {
    'interval_len': 2, 
    'binsize': 0.02, 
    'single_region': False, 
    'align_time': 'stimOn_times', 
    'time_window': (-.5, 1.5), 
    'fr_thresh': 0.5
}

# ---------
# LOAD DATA
# ---------

dandiset = f"{eid}_unaligned" if args.unaligned else f"{eid}_aligned"

dataset = Dataset(
    root=data_path,
    split="test", # "train"/"valid"/"test"
    include=[{
        "selection": [{
            "dandiset": dandiset,
            "sortsets": {eid},
        }],
    }],
)

# ----------
# LOAD MODEL
# ----------

with initialize(version_base="1.3", config_path=config_path, job_name="test_app"):
    cfg = compose(config_name=config_name)

model = hydra.utils.instantiate(
    cfg.model,
    task_specs=decoder_registry,
    backend_config=cfg.backend_config,
    _convert_="object",
)

sequence_length = params["interval_len"]
transforms = hydra.utils.instantiate(
    cfg.train_transforms, sequence_length=sequence_length
)

def list_checkpoints(model_path, ckpt_name):
    ckpt_dir = f"{model_path}/{ckpt_name}"
    checkpoints = [f for f in os.listdir(ckpt_dir) if f.endswith(".ckpt") and f != "last.ckpt"]
    return checkpoints

checkpoints = list_checkpoints(model_path, ckpt_name)
print("available checkpoints:", checkpoints)

chosen_ckpt = checkpoints[0] if len(checkpoints) > 0 else "last.ckpt"
ckpt = torch.load(f"{model_path}/{ckpt_name}/{chosen_ckpt}", map_location="cpu")
state_dict = ckpt["state_dict"]       

new_state_dict = {}
for key, value in state_dict.items():
    new_key = key[6:]
    new_state_dict[new_key] = state_dict[key]

model.load_state_dict(new_state_dict)

tokenizer = POYOPlusTokenizer(
    model.unit_emb.tokenizer,
    model.session_emb.tokenizer,
    decoder_registry=decoder_registry,
    latent_step=1 / 8,
    num_latents_per_step=cfg.model.num_latents,
    batch_type=model.batch_type,
)

transform = Compose([*transforms, tokenizer])

val_tokenizer = tokenizer
val_tokenizer.eval = True
val_dataset = Dataset(
    cfg.data_root,
    "test",
    include=OmegaConf.to_container(cfg.dataset),  # converts to native list[dicts]
    transform=val_tokenizer,
)
val_sampler = SequentialFixedWindowSampler(
    interval_dict=val_dataset.get_sampling_intervals(),
    window_length=sequence_length,
    step=sequence_length / 2,
)

val_loader = DataLoader(
    val_dataset,
    sampler=val_sampler,
    collate_fn=collate,
    batch_size=cfg.get(
        "eval_batch_size", cfg.batch_size
    ),  # Default to training batch size, but allow override in config.
    num_workers=1,
)

max_lr = cfg.base_lr * cfg.batch_size

epochs = cfg.epochs

optimizer = Lamb(
    model.parameters(),  # filter(lambda p: p.requires_grad, model.parameters()),
    lr=max_lr,
    weight_decay=cfg.weight_decay,
)

scheduler = torch.optim.lr_scheduler.OneCycleLR(
    optimizer,
    max_lr=max_lr,
    epochs=epochs,
    steps_per_epoch=len(val_loader),
    pct_start=cfg.pct_start,
    anneal_strategy="cos",
    div_factor=1,
)

wrapper = train_wrapper.TrainWrapper(
    model=model,
    optimizer=optimizer,
    scheduler=scheduler,
)

callbacks = [
    ModelSummary(max_depth=2),  # Displays the number of parameters in the model.
    ModelCheckpoint(
        dirpath=f"../../../logs/lightning_logs/",
        save_last=True,
        save_on_train_epoch_end=True,
        every_n_epochs=cfg.eval_epochs,
    ),
    train_wrapper.CustomValidator(val_loader),
    LearningRateMonitor(
        logging_interval="step"
    ),  # Create a callback to log the learning rate.
]

trainer = lightning.Trainer(
    # logger=[tb, wandb],
    default_root_dir=cfg.log_dir,
    check_val_every_n_epoch=cfg.eval_epochs,
    max_epochs=epochs,
    log_every_n_steps=1,
    strategy=(
        "auto" if torch.cuda.is_available() else "auto"
    ),
    callbacks=callbacks,
    num_sanity_val_steps=0,
    precision=cfg.precision,
    reload_dataloaders_every_n_epochs=5,
    accelerator="gpu",
    devices=cfg.gpus,
    num_nodes=cfg.nodes,
)

# ---------
# INFERENCE
# ---------

session_timestamp = {}
session_subtask_index = {}
session_gt_output = {}
session_pred_output = {}

for batch in tqdm(val_loader):
    absolute_starts = batch.pop("absolute_start")  # (B,)
    session_ids = batch.pop("session_id")  # (B,)
    output_subtask_index = batch.pop("output_subtask_index")

    batch_format = None
    if "input_mask" in batch:
        batch_format = "padded"
    elif "input_seqlen" in batch:
        batch_format = "chained"
    else:
        raise ValueError("Invalid batch format.")

    # move_to_gpu(batch, pl_module)
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu") 
    model.to(device)
    move_to_gpu(batch, device)

    # Autocast is explicitly set based on the precision specified by the user.
    # By default, torch autocasts to float16 for 16-bit inference.
    # This behavior is overridden to use bfloat16 if specified in trainer.precision.
    # If 16-bit inference is not enabled, autocast is not used.
    def get_autocast_args(trainer):
        if trainer.precision.startswith("bf16"):
            return torch.bfloat16, True
        elif trainer.precision.startswith("16"):
            return torch.float16, True
        else:
            return None, False

    dtype, enabled = get_autocast_args(trainer)
    # forward pass
    with torch.cuda.amp.autocast(enabled=enabled, dtype=dtype):
        with torch.inference_mode():
            pred_output, loss, losses_taskwise = model(**batch)

    # we need to get the timestamps, the ground truth values, the task ids as well
    # as the subtask ids. since the batch is padded and chained, this is a bit tricky
    # tldr: this extracts the ground truth in the same format as the model output
    batch_size = len(pred_output)
    # get gt_output and timestamps to be in the same format as pred_output
    timestamps = [{} for _ in range(batch_size)]
    subtask_index = [{} for _ in range(batch_size)]
    gt_output = [{} for _ in range(batch_size)]

    # collect ground truth
    for taskname, spec in model.readout.decoder_specs.items():
        taskid = Decoder.from_string(taskname).value

        # get the mask of tokens that belong to this task
        mask = batch["output_decoder_index"] == taskid

        if not torch.any(mask):
            # there is not a single token for this task, so we skip
            continue

        # we need to distribute the outputs to their respective samples

        if batch_format == "padded":
            token_batch = torch.where(mask)[0]
        elif batch_format == "chained":
            token_batch = batch["output_batch_index"][mask]

        batch_i, token_batch = torch.unique(token_batch, return_inverse=True)
        for i in range(len(batch_i)):
            timestamps[batch_i[i]][taskname] = (
                batch["output_timestamps"][mask][token_batch == i]
                + absolute_starts[batch_i[i]]
            )
            subtask_index[batch_i[i]][taskname] = output_subtask_index[
                taskname
            ][(token_batch == i).detach().cpu()]
            gt_output[batch_i[i]][taskname] = batch["output_values"][taskname][
                token_batch == i
            ]

    # register all of the data
    for i in range(batch_size):
        session_id = session_ids[i]

        if session_id not in session_pred_output:
            session_pred_output[session_id] = {}
            session_gt_output[session_id] = {}
            session_timestamp[session_id] = {}
            session_subtask_index[session_id] = {}

        for taskname, pred_values in pred_output[i].items():
            if taskname not in session_pred_output[session_id]:
                session_pred_output[session_id][
                    taskname
                ] = pred_values.detach().cpu()
                session_gt_output[session_id][taskname] = (
                    gt_output[i][taskname].detach().cpu()
                )
                session_timestamp[session_id][taskname] = (
                    timestamps[i][taskname].detach().cpu()
                )
                session_subtask_index[session_id][taskname] = (
                    subtask_index[i][taskname].detach().cpu()
                )
            else:
                session_pred_output[session_id][taskname] = torch.cat(
                    (
                        session_pred_output[session_id][taskname],
                        pred_values.detach().cpu(),
                    )
                )
                session_gt_output[session_id][taskname] = torch.cat(
                    (
                        session_gt_output[session_id][taskname],
                        gt_output[i][taskname].detach().cpu(),
                    )
                )
                session_timestamp[session_id][taskname] = torch.cat(
                    (
                        session_timestamp[session_id][taskname],
                        timestamps[i][taskname].detach().cpu(),
                    )
                )
                session_subtask_index[session_id][taskname] = torch.cat(
                    (
                        session_subtask_index[session_id][taskname],
                        subtask_index[i][taskname].detach().cpu(),
                    )
                )

# -----
# EVAL
# -----

results = {'choice': {}, 'block': {}, 'whisker':{}, 'wheel': {}}

if args.behavior == "choice":
    choice = dataset.get_session_data(session_ids[0]).choice.choice
    choice = session_gt_output[session_ids[0]]['CHOICE']
    pred = session_pred_output[session_ids[0]]['CHOICE'].argmax(-1)
    results['choice']['accuracy'] = accuracy_score(choice, pred)
    results['choice']['balanced_accuracy'] = balanced_accuracy_score(choice, pred)

if args.behavior == "block":
    block = dataset.get_session_data(session_ids[0]).block.block
    block = session_gt_output[session_ids[0]]['BLOCK']
    pred = session_pred_output[session_ids[0]]['BLOCK'].argmax(-1)
    results['block']['accuracy'] = accuracy_score(block, pred)
    results['block']['balanced_accuracy'] = balanced_accuracy_score(block, pred)

if args.behavior in ["wheel", "whisker"]:
    intervals = np.c_[
        dataset.get_session_data(session_ids[0]).trials.start, 
        dataset.get_session_data(session_ids[0]).trials.end, 
    ]

if args.behavior == "wheel":
    wh_gt = session_gt_output[session_ids[0]]['WHEEL']
    wh_pred = session_pred_output[session_ids[0]]['WHEEL']
    wh_timestamps = session_timestamp[session_ids[0]]['WHEEL']
    wh_subtask_index = session_subtask_index[session_ids[0]]['WHEEL']

    wh_gt_vals = wh_gt.squeeze()
    wh_pred_vals = wh_pred.squeeze()
    
    behave_dict, mask_dict = bin_behaviors(
        wh_timestamps,
        wh_gt_vals.numpy(),
        intervals=intervals, 
        beh = 'wheel',
        **params
    )
    
    binned_gt = behave_dict['wheel']
    
    behave_dict, mask_dict = bin_behaviors(
        wh_timestamps,
        wh_pred_vals.numpy(),
        intervals=intervals, 
        beh = 'wheel',
        **params
    )
    binned_pred = behave_dict['wheel']

if args.behavior == "whisker":
    me_gt = session_gt_output[session_ids[0]]['WHISKER']
    me_pred = session_pred_output[session_ids[0]]['WHISKER']
    me_timestamps = session_timestamp[session_ids[0]]['WHISKER']
    me_subtask_index = session_subtask_index[session_ids[0]]['WHISKER']

    me_gt_vals = me_gt.squeeze()
    me_pred_vals = me_pred.squeeze()
    
    behave_dict, mask_dict = bin_behaviors(
        me_timestamps,
        me_gt_vals.numpy(),
        intervals=intervals, 
        beh = 'whisker',
        **params
    )
    
    binned_gt = behave_dict['whisker']
    
    behave_dict, mask_dict = bin_behaviors(
        me_timestamps,
        me_pred_vals.numpy(),
        intervals=intervals, 
        beh = 'whisker',
        **params
    )
    binned_pred = behave_dict['whisker']

if args.behavior in ["wheel", "whisker"]:
    
    results[args.behavior]["r2_trial"] = r2_score(binned_gt.flatten(), binned_pred.flatten())

    save_res = {
        'gt': binned_gt,
        'pred': binned_pred,
        'beh_name': args.behavior,
        'eid': eid
    }

print(results)

res_path = f"{save_path}/{eid}/"
if not os.path.exists(res_path):
    os.makedirs(res_path)
np.save(f"{res_path}/{args.behavior}.npy", results)
