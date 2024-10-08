import random
import time
from doors_detection_long_term.doors_detector.dataset.torch_dataset import DEEP_DOORS_2_LABELLED, FINAL_DOORS_DATASET
import numpy as np
import torch.optim
from models.detr import SetCriterion
from models.matcher import HungarianMatcher
from torch.utils.data import DataLoader
from doors_detection_long_term.doors_detector.dataset.dataset_doors_final.datasets_creator_doors_final import DatasetsCreatorDoorsFinal
from doors_detection_long_term.doors_detector.models.detr_door_detector import *
from doors_detection_long_term.doors_detector.models.model_names import DETR_RESNET50
from doors_detection_long_term.doors_detector.utilities.plot import plot_losses
from doors_detection_long_term.doors_detector.utilities.collate_fn_functions import collate_fn
from doors_detection_long_term.scripts.doors_detector.dataset_configurator import *
from doors_detection_long_term.doors_detector.utilities.collate_fn_functions import seed_everything


device = 'cuda'

epochs_general_detector = [40, 60]

model_names = [(EXP_GENERAL_DETECTOR_2_LAYERS_BACKBONE_GIBSON_40_EPOCHS, EXP_GENERAL_DETECTOR_2_LAYERS_BACKBONE_GIBSON_60_EPOCHS),
               (EXP_GENERAL_DETECTOR_2_LAYERS_BACKBONE_DEEP_DOORS_2_40_EPOCHS, EXP_GENERAL_DETECTOR_2_LAYERS_BACKBONE_DEEP_DOORS_2_60_EPOCHS),
               (EXP_GENERAL_DETECTOR_2_LAYERS_BACKBONE_GIBSON_DEEP_DOORS_2_HALF_40_EPOCHS, EXP_GENERAL_DETECTOR_2_LAYERS_BACKBONE_GIBSON_DEEP_DOORS_2_HALF_60_EPOCHS),
               (EXP_GENERAL_DETECTOR_2_LAYERS_BACKBONE_GIBSON_DEEP_DOORS_2_40_EPOCHS, EXP_GENERAL_DETECTOR_2_LAYERS_BACKBONE_GIBSON_DEEP_DOORS_2_60_EPOCHS)]

datasets = [get_final_doors_dataset_all_envs(), get_deep_doors_2_relabelled_dataset_for_gd(), get_gibson_and_deep_door_2_dataset(True), get_gibson_and_deep_door_2_dataset(False)]
# Params
params = {
    'epochs': 40,
    'batch_size': 1,
    'seed': 0,
    'lr': 1e-5,
    'weight_decay': 1e-4,
    'lr_drop': 20,
    'lr_backbone': 1e-6,
    # Criterion
    'bbox_loss_coef': 5,
    'giou_loss_coef': 2,
    'eos_coef': 0.1,
    # Matcher
    'set_cost_class': 1,
    'set_cost_bbox': 5,
    'set_cost_giou': 2,
}


def prepare_model(description, labels, reload_model, restart_checkpoint):
    print(labels.keys())
    model = DetrDoorDetector(model_name=DETR_RESNET50, n_labels=len(labels.keys()), pretrained=reload_model, dataset_name=FINAL_DOORS_DATASET, description=description)

    start_epoch = 0
    logs = {'train': [], 'train_after_backpropagation': [], 'validation': [], 'test': [], 'time': []}
    optimizer_state_dict = {}
    lr_scheduler_state_dict = {}
    if restart_checkpoint:
        checkpoint = model.load_checkpoint()
        start_epoch = checkpoint['epoch'] + 1
        logs = checkpoint['logs']
        optimizer_state_dict = checkpoint['optimizer_state_dict']
        lr_scheduler_state_dict = checkpoint['lr_scheduler_state_dict']

    print("Params to learn:")
    for n, p in model.named_parameters():
        if p.requires_grad:
            print(n)

    #for p in [p for n, p in model.named_parameters() if "backbone" in n]:
        #p.requires_grad = False

    param_dicts = [
        {"params": [p for n, p in model.named_parameters() if "backbone" not in n and p.requires_grad]},
        {
            "params": [p for n, p in model.named_parameters() if "backbone" in n and p.requires_grad],
            "lr": params['lr_backbone'],
        },
    ]
    [p.to(device) for n, p in model.named_parameters()]

    optimizer = torch.optim.AdamW(param_dicts, lr=params['lr'], weight_decay=params['weight_decay'])


    # StepLR decays the learning rate of each parameter group by gamma every step_size epochs
    lr_scheduler = torch.optim.lr_scheduler.StepLR(optimizer, params['lr_drop'])

    if restart_checkpoint:
        optimizer.load_state_dict(optimizer_state_dict)
        lr_scheduler.load_state_dict(lr_scheduler_state_dict)

    # Create criterion to calculate losses
    losses = ['labels', 'boxes', 'cardinality']
    weight_dict = {'loss_ce': 1, 'loss_bbox': params['bbox_loss_coef'], 'loss_giou': params['giou_loss_coef']}
    matcher = HungarianMatcher(cost_class=params['set_cost_class'], cost_bbox=params['set_cost_bbox'], cost_giou=params['set_cost_giou'])
    criterion = SetCriterion(len(labels.keys()), matcher=matcher, weight_dict=weight_dict,
                             eos_coef=params['eos_coef'], losses=losses)
    model.to(device)
    criterion.to(device)

    return model, criterion, lr_scheduler, optimizer, logs


if __name__ == '__main__':

    # Fix seeds
    seed_everything(params['seed'])

    # Train the general detector with multiple epochs
    for ((model_40_epochs, model_60_epochs), (train, validation, labels, _)) in zip(model_names, datasets):
        epoch_count = 0
        print(f'Train set size: {len(train)}', f'Validation set size: {len(validation)}')
        data_loader_train = DataLoader(train, batch_size=params['batch_size'], collate_fn=collate_fn, shuffle=False, num_workers=4)
        data_loader_validation = DataLoader(validation, batch_size=params['batch_size'], collate_fn=collate_fn, drop_last=False, num_workers=4)

        model, criterion, lr_scheduler, optimizer, logs = prepare_model(model_40_epochs, labels, reload_model=False, restart_checkpoint=False)
        #print(model._description)
        print_logs_every = 10

        start_time = time.time()

        for epoch in range(epochs_general_detector[-1]):

            temp_logs = {'train': [], 'train_after_backpropagation': [], 'validation': []}
            accumulate_losses = {}

            model.train()
            criterion.train()

            for i, training_data in enumerate(data_loader_train):
                # Data is a tuple where
                #   - data[0]: a tensor containing all images shape = [batch_size, channels, img_height, img_width]
                #   - data[1]: a tuple of dictionaries containing the images' targets

                images, targets = training_data
                images = images.to(device)

                # Move targets to device
                targets = [{k: v.to(device) for k, v in target.items() if k != 'folder_name' and k != 'absolute_count'} for target in targets]

                outputs = model(images)

                # Compute losses
                losses_dict = criterion(outputs, targets)
                weight_dict = criterion.weight_dict

                # Losses are weighted using parameters contained in a dictionary
                losses = sum(losses_dict[k] * weight_dict[k] for k in losses_dict.keys() if k in weight_dict)

                scaled_losses_dict = {k: v * weight_dict[k] for k, v in losses_dict.items() if k in weight_dict}
                scaled_losses_dict['loss'] = sum(scaled_losses_dict.values())

                # Back propagate the losses
                optimizer.zero_grad()
                losses.backward()
                optimizer.step()

                accumulate_losses = {k: scaled_losses_dict[k] if k not in accumulate_losses else sum([accumulate_losses[k], scaled_losses_dict[k]]) for k, _ in scaled_losses_dict.items()}

                if i % print_logs_every == print_logs_every - 1:
                    accumulate_losses = {k: v.item() / print_logs_every for k, v in accumulate_losses.items()}
                    print(f'Train epoch [{epoch}] -> [{i}/{len(data_loader_train)}]: ' + ', '.join([f'{k}: {v}' for k, v in accumulate_losses.items()]))
                    temp_logs['train'].append(accumulate_losses)
                    accumulate_losses = {}

            logs['time'].append(time.time() - start_time)
            epoch_total = {}
            for d in temp_logs['train']:
                for k in d:
                    epoch_total[k] = epoch_total.get(k, 0) + d[k]

            logs['train'].append({k: v / len(temp_logs['train']) for k, v in epoch_total.items()})

            print(f'----> EPOCH SUMMARY TRAIN [{epoch}] -> [{i}/{len(data_loader_train)}]: ' + ', '.join([f'{k}: {v}' for k, v in logs['train'][epoch].items()]))

            # Train loss after backpropagation
            with torch.no_grad():
                model.eval()
                criterion.eval()

                accumulate_losses = {}
                for i, test_data in enumerate(data_loader_train):
                    images, targets = test_data
                    images = images.to(device)

                    # Move targets to device
                    targets = [{k: v.to(device) for k, v in target.items() if k != 'folder_name' and k != 'absolute_count'} for target in targets]

                    outputs = model(images)

                    # Compute losses
                    losses_dict = criterion(outputs, targets)
                    weight_dict = criterion.weight_dict

                    # Losses are weighted using parameters contained in a dictionary
                    losses = sum(losses_dict[k] * weight_dict[k] for k in losses_dict.keys() if k in weight_dict)

                    scaled_losses_dict = {k: v * weight_dict[k] for k, v in losses_dict.items() if k in weight_dict}
                    scaled_losses_dict['loss'] = sum(scaled_losses_dict.values())
                    accumulate_losses = {k: scaled_losses_dict[k] if k not in accumulate_losses else sum([accumulate_losses[k], scaled_losses_dict[k]]) for k, _ in scaled_losses_dict.items()}

                    if i % print_logs_every == print_logs_every - 1:
                        accumulate_losses = {k: v.item() / print_logs_every for k, v in accumulate_losses.items()}
                        print(f'Test after backprop epoch [{epoch}] -> [{i}/{len(data_loader_train)}]: ' + ', '.join([f'{k}: {v}' for k, v in accumulate_losses.items()]))
                        temp_logs['train_after_backpropagation'].append(accumulate_losses)
                        accumulate_losses = {}

            epoch_total = {}
            for d in temp_logs['train_after_backpropagation']:
                for k in d:
                    epoch_total[k] = epoch_total.get(k, 0) + d[k]

            logs['train_after_backpropagation'].append({k: v / len(temp_logs['train_after_backpropagation']) for k, v in epoch_total.items()})

            print(f'----> EPOCH SUMMARY TRAIN AFTER BACKPROP [{epoch}] -> [{i}/{len(data_loader_train)}]: ' + ', '.join([f'{k}: {v}' for k, v in logs['train_after_backpropagation'][epoch].items()]))


            # Validation
            with torch.no_grad():
                model.eval()
                criterion.eval()

                accumulate_losses = {}
                for i, test_data in enumerate(data_loader_validation):
                    images, targets = test_data
                    images = images.to(device)

                    # Move targets to device
                    targets = [{k: v.to(device) for k, v in target.items() if k != 'folder_name' and k != 'absolute_count'} for target in targets]

                    outputs = model(images)

                    # Compute losses
                    losses_dict = criterion(outputs, targets)
                    weight_dict = criterion.weight_dict

                    # Losses are weighted using parameters contained in a dictionary
                    losses = sum(losses_dict[k] * weight_dict[k] for k in losses_dict.keys() if k in weight_dict)

                    scaled_losses_dict = {k: v * weight_dict[k] for k, v in losses_dict.items() if k in weight_dict}
                    scaled_losses_dict['loss'] = sum(scaled_losses_dict.values())
                    accumulate_losses = {k: scaled_losses_dict[k] if k not in accumulate_losses else sum([accumulate_losses[k], scaled_losses_dict[k]]) for k, _ in scaled_losses_dict.items()}

                    if i % print_logs_every == print_logs_every - 1:
                        accumulate_losses = {k: v.item() / print_logs_every for k, v in accumulate_losses.items()}
                        print(f'Validation epoch [{epoch}] -> [{i}/{len(data_loader_validation)}]: ' + ', '.join([f'{k}: {v}' for k, v in accumulate_losses.items()]))
                        temp_logs['validation'].append(accumulate_losses)
                        accumulate_losses = {}

            epoch_total = {}
            for d in temp_logs['validation']:
                for k in d:
                    epoch_total[k] = epoch_total.get(k, 0) + d[k]

            logs['validation'].append({k: v / len(temp_logs['validation']) for k, v in epoch_total.items()})

            print(f'----> EPOCH SUMMARY VALIDATION [{epoch}] -> [{i}/{len(data_loader_validation)}]: ' + ', '.join([f'{k}: {v}' for k, v in logs['validation'][epoch].items()]))


            plot_losses(logs)

            model.save(epoch=epoch,
                   optimizer_state_dict=optimizer.state_dict(),
                   lr_scheduler_state_dict=lr_scheduler.state_dict(),
                   params=params,
                   logs=logs,
                   )

            # Change the model description on each epoch step
            if epoch == epochs_general_detector[epoch_count] - 1 and epoch_count < len(epochs_general_detector) -1:
                epoch_count += 1
                model.set_description(model_60_epochs)